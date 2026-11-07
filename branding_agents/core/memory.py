"""Memoria persistente su JSON.

Corretto rispetto all'originale:
- scrittura atomica (file temporaneo + os.replace): un riavvio a metà scrittura non tronca il file;
- lock: più agenti in thread possono scrivere senza intrecciarsi;
- un file corrotto viene messo da parte con un nome datato invece di essere sovrascritto
  al salvataggio successivo (l'originale ripartiva da vuoto e perdeva tutto);
- ogni voce ha uno stato (ok/error/skipped) e la cadenza conta solo le esecuzioni riuscite;
- elenco "visti" per non riproporre la stessa notizia.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import data_dir
from .util import atomic_write_text

logger = logging.getLogger(__name__)

STATUS_OK = "ok"


@dataclass
class MemoryEntry:
    agent: str
    action: str
    data: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    status: str = STATUS_OK
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryEntry":
        allowed = {"agent", "action", "data", "recommendations", "status", "timestamp"}
        return cls(**{k: v for k, v in d.items() if k in allowed})


class Memory:
    def __init__(self, path: Path | str | None = None, max_entries: int = 500):
        self.path = Path(path) if path else data_dir() / "memory.json"
        self.max_entries = max_entries
        self._lock = threading.RLock()
        self._entries: list[MemoryEntry] = []
        self._seen: dict[str, str] = {}
        self._load()

    # ---------------------------------------------------------------- persistenza
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):  # formato dell'originale
                entries, seen = raw, {}
            else:
                entries, seen = raw.get("entries", []), raw.get("seen", {})
            self._entries = [MemoryEntry.from_dict(e) for e in entries]
            self._seen = dict(seen)
        except (OSError, ValueError, TypeError) as exc:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = self.path.with_name(f"{self.path.stem}.corrotto-{stamp}{self.path.suffix}")
            try:
                self.path.rename(backup)
                logger.error("memoria illeggibile (%s): messa da parte in %s", exc, backup)
            except OSError:
                logger.error("memoria illeggibile (%s) e impossibile spostarla", exc)
            self._entries, self._seen = [], {}

    def _save_locked(self) -> None:
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]
        payload = {
            "version": 2,
            "entries": [asdict(e) for e in self._entries],
            "seen": self._seen,
        }
        atomic_write_text(self.path, json.dumps(payload, indent=2, ensure_ascii=False))

    # ---------------------------------------------------------------- scrittura
    def store(self, entry: MemoryEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            self._save_locked()

    def store_analysis(
        self,
        agent: str,
        action: str,
        data: dict[str, Any],
        recommendations: list[str] | None = None,
        status: str = STATUS_OK,
    ) -> None:
        self.store(MemoryEntry(agent, action, data, recommendations or [], status))

    def mark_seen(self, key: str) -> None:
        with self._lock:
            self._seen[key] = datetime.now().isoformat(timespec="seconds")
            self._save_locked()

    # ---------------------------------------------------------------- lettura
    def has_seen(self, key: str) -> bool:
        with self._lock:
            return key in self._seen

    def get_by_agent(self, agent: str, limit: int = 10, only_ok: bool = False) -> list[MemoryEntry]:
        with self._lock:
            rows = [e for e in self._entries if e.agent == agent and (not only_ok or e.status == STATUS_OK)]
            return rows[-limit:]

    def get_recent(self, limit: int = 20) -> list[MemoryEntry]:
        with self._lock:
            return self._entries[-limit:]

    def recent_within(self, agent: str, days: float) -> list[MemoryEntry]:
        """Voci riuscite di un agente negli ultimi N giorni."""
        cutoff = datetime.now() - timedelta(days=days)
        return [
            e for e in self.get_by_agent(agent, limit=self.max_entries, only_ok=True)
            if datetime.fromisoformat(e.timestamp) >= cutoff
        ]

    def days_since(self, agent: str) -> float | None:
        """Giorni dall'ultima esecuzione RIUSCITA; None se non c'è mai stata.

        Un'esecuzione fallita non azzera la cadenza: l'agente riprova al giro dopo.
        """
        last = self.get_by_agent(agent, limit=1, only_ok=True)
        if not last:
            return None
        return (datetime.now() - datetime.fromisoformat(last[0].timestamp)).total_seconds() / 86400

    def get_context_summary(self, agent: str | None = None, limit: int = 5) -> str:
        entries = self.get_by_agent(agent, limit) if agent else self.get_recent(limit)
        if not entries:
            return "Nessuna esecuzione precedente."
        lines = []
        for e in entries:
            recs = "; ".join(e.recommendations[:3]) if e.recommendations else "nessuna"
            lines.append(f"[{e.timestamp[:10]}] {e.agent}/{e.action} ({e.status}): {recs}")
        return "\n".join(lines)

    @property
    def total_entries(self) -> int:
        with self._lock:
            return len(self._entries)
