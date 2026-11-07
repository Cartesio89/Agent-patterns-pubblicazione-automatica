"""Classe base dei subagent.

Pipeline di ogni agente: dati -> prompt -> LLM (JSON) -> validazione -> controlli
deterministici -> risultato -> memoria.

Corretto rispetto all'originale: un agente non risulta "ok" se l'LLM non ha prodotto un
JSON valido o se mancano i campi obbligatori. Lo stato è esplicito (ok/error/skipped).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import SystemConfig
from .llm import LLMClient, LLMError
from .memory import Memory

logger = logging.getLogger(__name__)

STATUS_OK, STATUS_ERROR, STATUS_SKIPPED = "ok", "error", "skipped"


class NothingToDo(Exception):
    """L'agente non ha niente da fare ora (non è un errore)."""


@dataclass
class AgentResult:
    agent_name: str
    status: str  # ok | error | skipped
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    drafts: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    severity: str = "info"  # info | warning | critical

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseAgent(ABC):
    name: str = "base_agent"
    description: str = "Agente base"
    required_keys: tuple[str, ...] = ("summary",)
    depends_on: tuple[str, ...] = ()  # devono essere "ok", altrimenti l'agente viene saltato
    after: tuple[str, ...] = ()  # solo ordine di esecuzione, nessun vincolo di successo

    def __init__(self, llm: LLMClient, memory: Memory, config: SystemConfig):
        self.llm = llm
        self.memory = memory
        self.config = config

    # ---- da implementare
    @abstractmethod
    def _fetch_data(self, context: dict[str, AgentResult]) -> Any:
        """Raccoglie i dati. Solleva NothingToDo se non c'è lavoro, qualsiasi altra eccezione = errore."""

    @abstractmethod
    def _build_prompt(self, data: Any, memory_context: str) -> tuple[str, str]:
        """Restituisce (system_prompt, user_message)."""

    # ---- hook opzionali
    def _validate(self, analysis: dict[str, Any]) -> list[str]:
        return [f"campo mancante nella risposta LLM: {k}" for k in self.required_keys if k not in analysis]

    def _postprocess(self, result: AgentResult, analysis: dict[str, Any], data: Any) -> None:
        """Controlli deterministici sul risultato (non delegati all'LLM)."""

    def _memory_data(self, result: AgentResult) -> dict[str, Any]:
        return {"summary": result.summary, "severity": result.severity}

    # ---- esecuzione
    def _fail(self, summary: str, problems: list[str] | None = None) -> AgentResult:
        logger.error("[%s] %s", self.name, summary)
        result = AgentResult(self.name, STATUS_ERROR, summary, problems=problems or [summary], severity="critical")
        self.memory.store_analysis(self.name, "run", {"summary": summary}, status=STATUS_ERROR)
        return result

    def run(self, context: dict[str, AgentResult] | None = None) -> AgentResult:
        context = context or {}
        logger.info("[%s] avvio", self.name)
        try:
            data = self._fetch_data(context)
        except NothingToDo as exc:
            logger.info("[%s] niente da fare: %s", self.name, exc)
            return AgentResult(self.name, STATUS_SKIPPED, f"Niente da fare: {exc}")
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"raccolta dati fallita: {exc}")

        system_prompt, user_message = self._build_prompt(data, self.memory.get_context_summary(self.name, 3))
        try:
            analysis = self.llm.ask_json(system_prompt, user_message)
        except LLMError as exc:
            return self._fail(f"analisi LLM fallita: {exc}")

        problems = self._validate(analysis)
        if problems:
            return self._fail("risposta LLM incompleta", problems)

        result = AgentResult(
            agent_name=self.name,
            status=STATUS_OK,
            summary=str(analysis.get("summary", "")),
            data=analysis.get("data", {}) if isinstance(analysis.get("data"), dict) else {},
            drafts=[d for d in analysis.get("drafts", []) if isinstance(d, dict)],
            recommendations=[str(r) for r in analysis.get("recommendations", [])],
            severity=str(analysis.get("severity", "info")),
        )
        try:
            self._postprocess(result, analysis, data)
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"controlli finali falliti: {exc}")

        self.memory.store_analysis(self.name, "run", self._memory_data(result), result.recommendations, STATUS_OK)
        logger.info("[%s] ok, %d bozze", self.name, len(result.drafts))
        return result
