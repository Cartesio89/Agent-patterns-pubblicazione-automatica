"""NewsScout: legge i feed RSS/Atom configurati e fa scegliere le notizie più rilevanti.

Fonti: solo feed pubblici (FEEDS nel .env). Niente scraping di LinkedIn: vieta i bot nei
Termini d'uso. Per Reddit si può usare il feed RSS del subreddit (es. https://www.reddit.com/r/LocalLLaMA/.rss).
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from typing import Any

try:  # difende da entità XML malevole nei feed di terzi
    from defusedxml import ElementTree as ET
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as ET  # type: ignore[no-redef]

from ..core.base_agent import AgentResult, BaseAgent, NothingToDo

ATOM = "{http://www.w3.org/2005/Atom}"
TAG_RE = re.compile(r"<[^>]+>")
MAX_BYTES = 2_000_000


def fetch_url(url: str, timeout: int = 15) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"schema non consentito: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "branding-agents/0.1 (lettore RSS)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - schema controllato sopra
        return resp.read(MAX_BYTES).decode("utf-8", errors="replace")


def _clean(text: str | None, limit: int = 400) -> str:
    return " ".join(html.unescape(TAG_RE.sub(" ", text or "")).split())[:limit]


def parse_feed(xml_text: str, max_items: int = 15) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    if root.tag.lower().endswith("feed"):  # Atom
        for e in root.findall(f"{ATOM}entry"):
            link = ""
            for l in e.findall(f"{ATOM}link"):
                if l.get("rel", "alternate") == "alternate" and l.get("href"):
                    link = l.get("href", "")
                    break
            summary = e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content")
            items.append({"title": _clean(e.findtext(f"{ATOM}title"), 200), "link": link.strip(),
                          "snippet": _clean(summary), "published": (e.findtext(f"{ATOM}updated") or "").strip()})
    else:  # RSS 2.0
        for e in root.iter("item"):
            items.append({"title": _clean(e.findtext("title"), 200), "link": (e.findtext("link") or "").strip(),
                          "snippet": _clean(e.findtext("description")), "published": (e.findtext("pubDate") or "").strip()})
    valid = [i for i in items if i["title"] and i["link"].lower().startswith(("http://", "https://"))]
    return valid[:max_items]


class NewsScout(BaseAgent):
    name = "news_scout"
    description = "Raccoglie notizie da feed RSS e sceglie le più rilevanti"
    required_keys = ("summary", "items")
    fetcher = staticmethod(fetch_url)  # sostituibile nei test

    def _fetch_data(self, context: dict[str, AgentResult]) -> dict[str, Any]:
        if not self.config.feeds:
            raise NothingToDo("nessun feed configurato (variabile FEEDS)")
        collected: dict[str, dict[str, str]] = {}
        errors: list[str] = []
        for url in self.config.feeds:
            try:
                for item in parse_feed(self.fetcher(url), self.config.max_items_per_feed):
                    collected.setdefault(item["link"], item)
            except Exception as exc:  # noqa: BLE001 - un feed rotto non ferma gli altri
                errors.append(f"{url}: {exc}")
        if not collected and errors:
            raise RuntimeError("tutti i feed hanno fallito: " + " | ".join(errors))
        fresh = [i for l, i in collected.items() if not self.memory.has_seen(l)]
        if not fresh:
            raise NothingToDo("nessuna notizia nuova rispetto a quelle già usate")
        return {"items": fresh[:40], "feed_errors": errors}

    def _build_prompt(self, data: dict[str, Any], memory_context: str) -> tuple[str, str]:
        system = f"""[AGENT:news_scout]
Sei un redattore che seleziona notizie su IA e tecnologia per un pubblico italiano di professionisti.
Scegli al massimo 5 notizie, le più rilevanti e verificabili. Per ognuna spiega in una riga perché interessa.
Usa SOLO i link presenti nell'elenco fornito: non inventarne.
Esecuzioni precedenti:
{memory_context}
Rispondi con JSON: {{"summary": "una riga", "items": [{{"title": str, "link": str, "why": str}}]}}"""
        compact = [{"title": i["title"], "link": i["link"], "testo": i["snippet"]} for i in data["items"]]
        return system, "Notizie disponibili:\n" + json.dumps(compact, ensure_ascii=False)

    def _postprocess(self, result: AgentResult, analysis: dict[str, Any], data: dict[str, Any]) -> None:
        by_link = {i["link"]: i for i in data["items"]}
        kept, dropped = [], 0
        for it in analysis.get("items", []):
            raw = by_link.get(it.get("link")) if isinstance(it, dict) else None
            if raw is None:
                dropped += 1  # link non presente nella raccolta: scartato, mai fidarsi di un link scritto dall'LLM
                continue
            kept.append({"title": raw["title"], "link": raw["link"], "why": str(it.get("why", "")), "snippet": raw["snippet"]})
        if dropped:
            result.problems.append(f"{dropped} notizie scartate perché con link non presente nei feed")
        if not kept:
            raise RuntimeError("nessuna notizia valida dopo il controllo dei link")
        result.data = {"items": kept, "feed_errors": data["feed_errors"]}

    def _memory_data(self, result: AgentResult) -> dict[str, Any]:
        return {"summary": result.summary, "items": [{"title": i["title"], "link": i["link"]} for i in result.data["items"]]}
