"""NewsletterDraft: bozza della newsletter settimanale dalle notizie scelte negli ultimi 7 giorni.

Legge dalla memoria (non dipende dal successo di NewsScout di oggi). Non invia nulla: la bozza
va caricata a mano nello strumento di invio (es. Mailchimp) dopo la tua approvazione.
"""
from __future__ import annotations

import json
from typing import Any

from ..core.base_agent import AgentResult, BaseAgent, NothingToDo

MAX_SUBJECT = 70


class NewsletterDraft(BaseAgent):
    name = "newsletter_draft"
    description = "Bozza della newsletter settimanale"
    required_keys = ("summary", "drafts")
    after = ("news_scout",)  # solo ordine: così include anche le notizie di oggi

    def _fetch_data(self, context: dict[str, AgentResult]) -> dict[str, Any]:
        items: dict[str, dict[str, str]] = {}
        for entry in self.memory.recent_within("news_scout", days=7):
            for it in entry.data.get("items", []):
                if it.get("link"):
                    items.setdefault(it["link"], it)
        if len(items) < 3:
            raise NothingToDo(f"servono almeno 3 notizie negli ultimi 7 giorni, ce ne sono {len(items)}")
        return {"items": list(items.values())[:12]}

    def _build_prompt(self, data: dict[str, Any], memory_context: str) -> tuple[str, str]:
        system = f"""[AGENT:newsletter_draft]
Scrivi in italiano la bozza di una newsletter settimanale su IA e tecnologia, tono diretto e pratico.
Regole:
- Usa SOLO le notizie fornite e i loro link; non aggiungere fatti, numeri o link.
- Oggetto massimo {MAX_SUBJECT} caratteri. Una riga di anteprima. Corpo in Markdown con un breve paragrafo per notizia.
- Elenca in claims_to_verify ogni affermazione da controllare.
Esecuzioni precedenti:
{memory_context}
Rispondi con JSON: {{"summary": "una riga", "drafts": [{{"platform": "newsletter", "format": "newsletter", "subject": str, "preview_text": str, "body_markdown": str, "links": [str], "claims_to_verify": [str]}}]}}"""
        return system, "Notizie della settimana:\n" + json.dumps(data["items"], ensure_ascii=False)

    def _postprocess(self, result: AgentResult, analysis: dict[str, Any], data: dict[str, Any]) -> None:
        allowed = {i["link"] for i in data["items"]}
        result.drafts = result.drafts[:1]
        if not result.drafts:
            raise RuntimeError("l'LLM non ha prodotto bozze")
        for d in result.drafts:
            d["platform"] = "newsletter"
            problems: list[str] = []
            if not str(d.get("body_markdown", "")).strip():
                problems.append("corpo vuoto")
            if len(str(d.get("subject", ""))) > MAX_SUBJECT:
                problems.append(f"oggetto oltre {MAX_SUBJECT} caratteri")
            stray = [l for l in d.get("links", []) if l not in allowed]
            if stray:
                problems.append(f"{len(stray)} link non presenti nelle notizie fornite")
            if "{{" in str(d.get("body_markdown", "")):
                problems.append("contiene segnaposto {{...}} da compilare")
            d["problems"] = problems
