"""Configurazione.

Nessun effetto collaterale all'import: l'originale creava cartelle appena il modulo
veniva importato.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Identificativo del modello preso dalla documentazione della piattaforma al momento
# della scrittura. NON verificato contro i modelli abilitati sul tuo account: se la
# chiamata fallisce con "model not found", imposta ANTHROPIC_MODEL nel .env.
DEFAULT_MODEL = "claude-sonnet-5-5"


def data_dir() -> Path:
    return Path(os.getenv("AGENTS_DATA_DIR", str(PROJECT_ROOT / "data")))


def _csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _temperature(value: str | None) -> float | None:
    """'' -> 0.2 (default); 'none' -> non inviare il parametro; altrimenti il numero."""
    if value is None or value.strip() == "":
        return 0.2
    if value.strip().lower() in ("none", "off", "no"):
        return None
    return float(value)


@dataclass
class LLMConfig:
    api_key: str = ""
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    temperature: float | None = 0.2
    json_retries: int = 1  # tentativi extra se la risposta non è JSON valido

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL,
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            temperature=_temperature(os.getenv("LLM_TEMPERATURE")),
            json_retries=int(os.getenv("LLM_JSON_RETRIES", "1")),
        )

    def is_valid(self) -> bool:
        return bool(self.api_key)


@dataclass
class SystemConfig:
    feeds: list[str] = field(default_factory=list)
    # giorni minimi tra due esecuzioni riuscite dello stesso agente (modalità smart)
    min_days_between: dict[str, int] = field(
        default_factory=lambda: {"news_scout": 1, "instagram_draft": 2, "newsletter_draft": 7}
    )
    max_items_per_feed: int = 15
    max_workers: int = 3
    max_memory_entries: int = 500
    # cartella "skills/" del pacchetto Instagram italiano (opzionale)
    ig_pack_path: str = ""
    python_cmd: str = sys.executable or "python"

    @classmethod
    def from_env(cls) -> "SystemConfig":
        cfg = cls(
            feeds=_csv(os.getenv("FEEDS", "")),
            max_workers=int(os.getenv("MAX_WORKERS", "3")),
            ig_pack_path=os.getenv("IG_PACK_PATH", "").strip(),
        )
        for name in list(cfg.min_days_between):
            raw = os.getenv(f"CADENCE_{name.upper()}")
            if raw:
                cfg.min_days_between[name] = int(raw)
        return cfg


def load_all_config() -> tuple[LLMConfig, SystemConfig]:
    """Carica .env se python-dotenv è installato, poi legge l'ambiente."""
    try:
        from dotenv import load_dotenv

        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass
    return LLMConfig.from_env(), SystemConfig.from_env()
