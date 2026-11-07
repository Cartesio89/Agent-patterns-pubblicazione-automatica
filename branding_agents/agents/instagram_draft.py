"""InstagramDraft: scrive una bozza di post/reel Instagram dalla notizia migliore.

Non pubblica. Controlli deterministici nel codice (lunghezza, hashtag, fonte, segnaposto) e,
se IG_PACK_PATH punta alla cartella skills/ del pacchetto Instagram italiano, passa la
didascalia da humanize.py e caption.py.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..core.base_agent import AgentResult, BaseAgent, NothingToDo

HASHTAG_RE = re.compile(r"#\w+")
MAX_CAPTION = 2200
MAX_HASHTAGS = 5  # tetto per post su Instagram da dicembre 2025 (valore da riverificare)


def validate_ig_draft(draft: dict[str, Any], allowed_links: set[str]) -> list[str]:
    problems: list[str] = []
    caption = str(draft.get("caption", "")).strip()
    if not str(draft.get("hook", "")).strip():
        problems.append("hook vuoto")
    if not caption:
        problems.append("didascalia vuota")
    if len(caption) > MAX_CAPTION:
        problems.append(f"didascalia di {len(caption)} caratteri, il limite è {MAX_CAPTION}")
    n_tags = len(HASHTAG_RE.findall(caption))
    if n_tags > MAX_HASHTAGS:
        problems.append(f"{n_tags} hashtag, il massimo è {MAX_HASHTAGS}")
    if draft.get("source_link") not in allowed_links:
        problems.append("source_link non corrisponde a nessuna notizia selezionata")
    if "{{" in caption or "{{" in str(draft.get("hook", "")):
        problems.append("contiene segnaposto {{...}} da compilare prima di pubblicare")
    return problems


def run_pack_tool(python_cmd: str, script: Path, text: str, *args: str) -> dict[str, Any]:
    """Esegue uno script del pacchetto Instagram con --json. Solleva se fallisce."""
    proc = subprocess.run(
        [python_cmd, "-I", str(script), "-", *args, "--json"],
        input=text, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    # caption.py esce con codice 1 quando il verdetto non è PRONTA (segnale per l'automazione):
    # non è un guasto. Conta il JSON prodotto, non il codice di uscita.
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"{script.name} codice {proc.returncode}, output non JSON: {proc.stderr.strip()[:200]}") from exc


class InstagramDraft(BaseAgent):
    name = "instagram_draft"
    description = "Scrive una bozza Instagram dalla notizia migliore"
    required_keys = ("summary", "drafts")
    depends_on = ("news_scout",)

    def _fetch_data(self, context: dict[str, AgentResult]) -> dict[str, Any]:
        items = context["news_scout"].data.get("items", [])
        fresh = [i for i in items if not self.memory.has_seen(i["link"])]
        if not fresh:
            raise NothingToDo("nessuna notizia nuova da trasformare in post")
        return {"item": fresh[0]}

    def _build_prompt(self, data: dict[str, Any], memory_context: str) -> tuple[str, str]:
        system = f"""[AGENT:instagram_draft]
Scrivi UNA bozza di post Instagram in italiano, in prima persona, tono diretto da professionista.
Regole:
- Usa SOLO i fatti presenti nel testo fornito. Se serve un numero o un nome che non c'è, scrivi {{{{da verificare}}}}.
- Elenca in claims_to_verify ogni affermazione fattuale che l'autore deve controllare.
- La prima riga (hook) deve reggersi da sola nei primi 125 caratteri.
- Al massimo {MAX_HASHTAGS} hashtag, una sola richiesta al lettore, nessuna esca per commenti o follow.
- source_link deve essere esattamente il link fornito.
Esecuzioni precedenti:
{memory_context}
Rispondi con JSON: {{"summary": "una riga", "drafts": [{{"platform": "instagram", "format": "post|reel|carosello", "hook": str, "caption": str, "source_link": str, "claims_to_verify": [str]}}]}}"""
        return system, "Notizia:\n" + json.dumps(data["item"], ensure_ascii=False)

    def _postprocess(self, result: AgentResult, analysis: dict[str, Any], data: dict[str, Any]) -> None:
        allowed = {data["item"]["link"]}
        result.drafts = result.drafts[:1]  # una bozza per esecuzione
        if not result.drafts:
            raise RuntimeError("l'LLM non ha prodotto bozze")
        pack = Path(self.config.ig_pack_path) if self.config.ig_pack_path else None
        for d in result.drafts:
            d["platform"] = "instagram"
            d["problems"] = validate_ig_draft(d, allowed)
            if pack:
                self._pack_qa(d, pack, allowed)
        # La notizia è "usata" solo se la bozza è stata prodotta: se prima falliva, resta disponibile.
        self.memory.mark_seen(data["item"]["link"])

    def _pack_qa(self, draft: dict[str, Any], pack: Path, allowed: set[str]) -> None:
        try:
            cleaned = run_pack_tool(self.config.python_cmd, pack / "ig-human" / "humanize.py", draft["caption"])
            draft["caption_originale"] = draft["caption"]
            draft["caption"] = cleaned["text"].strip()
            qa = run_pack_tool(self.config.python_cmd, pack / "ig-caption" / "caption.py", draft["caption"])
            draft["qa"] = {"verdetto": qa.get("verdict"), "controlli": qa.get("checks"), "umanizzatore": cleaned.get("report")}
            # la didascalia è cambiata: ricalcola i controlli deterministici e aggiungi il verdetto
            draft["problems"] = validate_ig_draft(draft, allowed)
            if qa.get("verdict") != "PRONTA":
                draft["problems"].append(f"controllo didascalia: {qa.get('verdict')} (vedi qa)")
        except Exception as exc:  # noqa: BLE001 - il controllo di qualità è un di più, ma va segnalato
            draft["problems"].append(f"controllo qualità del pacchetto Instagram non eseguito: {exc}")
