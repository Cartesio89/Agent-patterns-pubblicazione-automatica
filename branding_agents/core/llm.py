"""Client LLM.

Differenza chiave rispetto all'originale: se la risposta non è JSON valido, qui si
solleva LLMParseError (dopo i tentativi di riparazione). L'originale restituiva
{"error": ...} e l'agente dichiarava comunque "Analisi completata" con dati vuoti.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import LLMConfig

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Errore di chiamata o di risposta dell'LLM."""


class LLMParseError(LLMError):
    """La risposta non è un oggetto JSON valido."""


def extract_json(raw: str) -> dict[str, Any] | None:
    """Estrae un oggetto JSON da una risposta, tollerando recinti markdown e testo attorno.

    Restituisce None se non trova un OGGETTO JSON (un array o un valore scalare non valgono).
    """
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z0-9]*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class LLMClient:
    def __init__(self, config: LLMConfig, client: Any = None):
        self.config = config
        self._client = client  # iniettabile nei test

    def _sdk(self) -> Any:
        if self._client is None:
            import anthropic  # import tardivo: i test non lo richiedono

            self._client = anthropic.Anthropic(api_key=self.config.api_key)
        return self._client

    def ask(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        kwargs: dict[str, Any] = dict(
            model=self.config.model,
            max_tokens=max_tokens or self.config.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        temp = temperature if temperature is not None else self.config.temperature
        if temp is not None:  # alcuni modelli rifiutano il parametro: LLM_TEMPERATURE=none
            kwargs["temperature"] = temp
        try:
            response = self._sdk().messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - qualsiasi errore SDK/rete diventa LLMError
            raise LLMError(f"chiamata LLM fallita: {exc}") from exc
        parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
        if not parts:
            raise LLMError("risposta LLM senza testo")
        return "".join(parts)

    def ask_json(
        self, system_prompt: str, user_message: str, temperature: float | None = None
    ) -> dict[str, Any]:
        system = (
            system_prompt
            + "\n\nRispondi ESCLUSIVAMENTE con un oggetto JSON valido, senza testo prima o dopo "
            "e senza blocchi markdown."
        )
        message = user_message
        last = ""
        attempts = self.config.json_retries + 1
        for _ in range(attempts):
            last = self.ask(system, message, temperature)
            obj = extract_json(last)
            if obj is not None:
                return obj
            logger.warning("risposta non JSON, riprovo")
            message = (
                user_message
                + "\n\nLa risposta precedente non era un JSON valido. "
                "Rispondi di nuovo solo con il JSON."
            )
        raise LLMParseError(f"risposta non JSON dopo {attempts} tentativi: {last[:200]!r}")
