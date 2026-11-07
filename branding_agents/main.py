"""Punto d'ingresso. Esegue UNA volta e termina: la pianificazione è del sistema operativo.

Uso (dalla cartella radice del repo):
    python -m branding_agents --mode smart     # solo gli agenti in scadenza per cadenza
    python -m branding_agents --mode full      # tutti
    python -m branding_agents --mode quick     # solo la raccolta notizie

Su Windows pianifica con l'Utilità di pianificazione (vedi README): sopravvive ai riavvii,
a differenza di un processo Python sempre acceso.
Codici d'uscita: 0 ok, 1 configurazione, 3 almeno un agente in errore o revisione non approvata.
"""
from __future__ import annotations

import argparse
import logging
import sys

from .core.config import data_dir, load_all_config
from .core.llm import LLMClient
from .core.memory import Memory
from .core.orchestrator import Orchestrator
from .core.reporter import save_report


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # console Windows: evita errori su accenti ed emoji
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Orchestratore agenti di personal branding (bozze, nessuna pubblicazione)")
    parser.add_argument("--mode", choices=["smart", "full", "quick"], default="smart")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    data_dir().mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(data_dir() / "agents.log", encoding="utf-8")],
    )

    llm_cfg, sys_cfg = load_all_config()
    if not llm_cfg.is_valid():
        logging.getLogger("main").error("ANTHROPIC_API_KEY mancante: copia env.example in .env e compilalo")
        return 1

    orch = Orchestrator(LLMClient(llm_cfg), Memory(max_entries=sys_cfg.max_memory_entries), sys_cfg)
    report = orch.run(args.mode)
    path = save_report(report)

    print("\n" + "=" * 60)
    print(report["executive_summary"])
    print(f"Revisione automatica approvata: {report['gate'].get('approved')}")
    print("Nessuna bozza è stata pubblicata: serve la tua approvazione.")
    print(f"Report: {path}")
    print("=" * 60)

    failed = any(r["status"] == "error" for r in report["results"])
    return 3 if failed or (report["drafts"] and not report["gate"].get("approved")) else 0
