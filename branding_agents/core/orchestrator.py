"""Orchestratore: pianifica, esegue per livelli in parallelo, controlla, sintetizza.

Corretto rispetto all'originale:
- il controllo finale è fail-closed: se la revisione fallisce, o la risposta non contiene
  `approved: true` esplicito, nessuna bozza risulta approvata (l'originale approvava);
- i controlli deterministici degli agenti (`problems`) non si possono scavalcare con l'LLM;
- la pianificazione "smart" è deterministica (cadenza calcolata dal codice sulla memoria),
  non decisa dall'LLM: costa zero token e non cambia da una run all'altra;
- le dipendenze tra agenti sono esplicite: un agente dipendente viene saltato se la
  sorgente fallisce, invece di lavorare su dati vuoti;
- se la sintesi LLM fallisce il report esce comunque, con una sintesi fatta dal codice.

L'orchestratore NON pubblica. Produce bozze con stato; la pubblicazione è un passo umano.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from ..agents import REGISTRY
from .base_agent import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, AgentResult, BaseAgent
from .config import SystemConfig
from .llm import LLMClient, LLMError
from .memory import Memory

logger = logging.getLogger(__name__)

DUE_TOLERANCE_DAYS = 0.1  # ~2,4 ore: una run giornaliera alle 08:00 non salta per qualche secondo di scarto

REVIEW_SYSTEM = """[AGENT:reviewer]
Sei il revisore finale di bozze per i social e la newsletter di un professionista italiano.
Per ogni bozza verifica:
1. Ogni affermazione fattuale, numero o nome è supportato dalla fonte fornita. Ciò che non lo è è un problema.
2. Nessun invito ingannevole, nessuna esca per commenti o follow, una sola richiesta al lettore.
3. Il tono è coerente e non promette risultati.
4. Nessuna bozza ne duplica un'altra.
Sii severo: nel dubbio NON approvare.
Rispondi con JSON: {"approved": true|false, "confidence": 0.0-1.0, "issues": ["..."], "blocked_indexes": [indici delle bozze da bloccare]}"""

SYNTH_SYSTEM = """[AGENT:synthesizer]
Scrivi in italiano una sintesi esecutiva di 2-4 righe dell'esecuzione degli agenti: cosa è stato prodotto,
cosa è fallito o è stato saltato, cosa richiede l'attenzione umana. Usa solo i fatti forniti.
Rispondi con JSON: {"executive_summary": "..."}"""


class Orchestrator:
    def __init__(self, llm: LLMClient, memory: Memory, config: SystemConfig):
        self.llm = llm
        self.memory = memory
        self.config = config

    # ------------------------------------------------------------ pianificazione
    def _is_due(self, name: str) -> bool:
        cadence = self.config.min_days_between.get(name, 1)
        elapsed = self.memory.days_since(name)
        return elapsed is None or elapsed >= cadence - DUE_TOLERANCE_DAYS

    def _with_dependencies(self, names: list[str]) -> list[str]:
        out: list[str] = []

        def add(n: str) -> None:
            for dep in REGISTRY[n].depends_on:
                add(dep)
            if n not in out:
                out.append(n)

        for n in names:
            add(n)
        return out

    def plan(self, mode: str = "smart") -> list[str]:
        if mode == "full":
            return self._with_dependencies(list(REGISTRY))
        if mode == "quick":
            return ["news_scout"]
        if mode == "smart":
            return self._with_dependencies([n for n in REGISTRY if self._is_due(n)])
        raise ValueError(f"modalità sconosciuta: {mode}")

    def _layers(self, names: list[str]) -> list[list[str]]:
        selected, done, remaining, layers = set(names), set(), list(names), []
        while remaining:
            layer = [
                n for n in remaining
                if all(d in done or d not in selected for d in (*REGISTRY[n].depends_on, *REGISTRY[n].after))
            ]
            if not layer:
                raise ValueError(f"dipendenze circolari tra gli agenti: {remaining}")
            layers.append(layer)
            done.update(layer)
            remaining = [n for n in remaining if n not in layer]
        return layers

    # ------------------------------------------------------------ esecuzione
    def _run_one(self, name: str, context: dict[str, AgentResult]) -> AgentResult:
        cls: type[BaseAgent] = REGISTRY[name]
        unmet = [d for d in cls.depends_on if d not in context or context[d].status != STATUS_OK]
        if unmet:
            return AgentResult(name, STATUS_SKIPPED, f"Saltato: dipendenze non soddisfatte ({', '.join(unmet)})")
        try:
            return cls(self.llm, self.memory, self.config).run(context)
        except Exception as exc:  # noqa: BLE001 - un bug in un agente non deve fermare gli altri
            logger.exception("[%s] eccezione non gestita", name)
            return AgentResult(name, STATUS_ERROR, f"eccezione non gestita: {exc}", problems=[str(exc)], severity="critical")

    def execute(self, names: list[str]) -> dict[str, AgentResult]:
        results: dict[str, AgentResult] = {}
        for layer in self._layers(names):
            with ThreadPoolExecutor(max_workers=max(1, self.config.max_workers)) as pool:
                futures = {pool.submit(self._run_one, n, dict(results)): n for n in layer}
                for fut in as_completed(futures):
                    results[futures[fut]] = fut.result()
        return results

    # ------------------------------------------------------------ cancello di controllo
    def reflect(self, results: dict[str, AgentResult]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Restituisce (gate, bozze con stato). Fail-closed."""
        drafts: list[dict[str, Any]] = []
        for r in results.values():
            if r.status == STATUS_OK:
                for d in r.drafts:
                    drafts.append({**d, "agent": r.agent_name, "problems": list(d.get("problems", []))})

        gate: dict[str, Any] = {"checked": False, "approved": False, "issues": [], "confidence": None, "error": None}
        if not drafts:
            gate["issues"] = ["nessuna bozza da controllare"]
            return gate, drafts

        sources = {}
        for r in results.values():
            for item in r.data.get("items", []) if isinstance(r.data, dict) else []:
                sources[item.get("link", "")] = {"title": item.get("title"), "testo": item.get("snippet") or item.get("why")}
        payload = {
            "bozze": [
                {
                    "indice": i,
                    "piattaforma": d.get("platform"),
                    "titolo": d.get("hook") or d.get("subject"),
                    "testo": (d.get("caption") or d.get("body_markdown") or "")[:1500],
                    "fonte": d.get("source_link"),
                    "affermazioni_da_verificare": d.get("claims_to_verify", []),
                }
                for i, d in enumerate(drafts)
            ],
            "fonti": sources,
        }
        blocked: set[int] = set()
        try:
            review = self.llm.ask_json(REVIEW_SYSTEM, json.dumps(payload, ensure_ascii=False))
            gate["checked"] = True
            gate["approved"] = review.get("approved") is True  # solo un True esplicito vale
            gate["issues"] = [str(x) for x in review.get("issues", []) if x]
            conf = review.get("confidence")
            gate["confidence"] = conf if isinstance(conf, (int, float)) else None
            blocked = {i for i in review.get("blocked_indexes", []) if isinstance(i, int)}
        except LLMError as exc:
            gate["error"] = str(exc)
            gate["issues"] = ["revisione automatica non riuscita: tutte le bozze restano da rivedere"]

        for i, d in enumerate(drafts):
            if d["problems"]:
                d["status"] = "da_rivedere"  # i controlli deterministici non si scavalcano
            elif i in blocked:
                d["status"] = "bloccata"
            elif gate["approved"]:
                d["status"] = "pronta_per_approvazione_umana"
            else:
                d["status"] = "da_rivedere"
        return gate, drafts

    # ------------------------------------------------------------ sintesi
    def synthesize(self, results: dict[str, AgentResult], gate: dict[str, Any], drafts: list[dict[str, Any]]) -> tuple[str, bool]:
        counts = {s: sum(1 for r in results.values() if r.status == s) for s in (STATUS_OK, STATUS_ERROR, STATUS_SKIPPED)}
        pronte = sum(1 for d in drafts if d.get("status") == "pronta_per_approvazione_umana")
        fallback = (
            f"Agenti: {counts[STATUS_OK]} ok, {counts[STATUS_ERROR]} in errore, {counts[STATUS_SKIPPED]} saltati. "
            f"Bozze: {len(drafts)} totali, {pronte} pronte per la tua approvazione. "
            f"Revisione automatica: {'approvata' if gate.get('approved') else 'NON approvata'}."
        )
        facts = {
            "agenti": {n: {"stato": r.status, "sintesi": r.summary, "problemi": r.problems} for n, r in results.items()},
            "conteggi": counts,
            "bozze_pronte": pronte,
            "bozze_totali": len(drafts),
            "revisione": gate,
        }
        try:
            out = self.llm.ask_json(SYNTH_SYSTEM, json.dumps(facts, ensure_ascii=False))
            text = out.get("executive_summary")
            if isinstance(text, str) and text.strip():
                return text.strip(), True
        except LLMError as exc:
            logger.warning("sintesi LLM fallita: %s", exc)
        return fallback, False

    # ------------------------------------------------------------ pipeline completa
    def run(self, mode: str = "smart") -> dict[str, Any]:
        logger.info("=== ORCHESTRATORE (modalità %s) ===", mode)
        plan = self.plan(mode)
        report: dict[str, Any] = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "plan": plan,
            "human_approval_required": True,
        }
        if not plan:
            report.update(
                results=[], drafts=[], gate={"checked": False, "approved": False, "issues": []},
                executive_summary="Nessun agente in scadenza secondo la cadenza configurata: niente da fare.",
                synthesis_ok=True, nothing_to_do=True,
            )
            return report

        results = self.execute(plan)
        gate, drafts = self.reflect(results)
        summary, synth_ok = self.synthesize(results, gate, drafts)
        report.update(
            results=[r.to_dict() for r in results.values()],
            drafts=drafts,
            gate=gate,
            executive_summary=summary,
            synthesis_ok=synth_ok,
            nothing_to_do=False,
        )
        errors = any(r.status == STATUS_ERROR for r in results.values())
        self.memory.store_analysis(
            "orchestrator", "run",
            {"mode": mode, "plan": plan, "gate_approved": gate["approved"], "drafts": len(drafts)},
            status=STATUS_ERROR if errors else STATUS_OK,
        )
        return report
