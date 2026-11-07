"""Salva il report in Markdown e JSON (scrittura atomica)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import data_dir
from .util import atomic_write_text


def render_markdown(report: dict[str, Any]) -> str:
    gate = report.get("gate", {})
    lines = [
        f"# Report agenti, {report.get('generated_at', '')}",
        "",
        f"**Modalità:** {report.get('mode')}  |  **Agenti eseguiti:** {', '.join(report.get('plan', [])) or 'nessuno'}",
        "",
        "## Sintesi",
        report.get("executive_summary", ""),
        "",
        "## Cancello di controllo",
        f"- Revisione automatica approvata: **{gate.get('approved')}**",
        f"- Approvazione umana richiesta: **{report.get('human_approval_required', True)}**",
    ]
    for issue in gate.get("issues", []):
        lines.append(f"- Problema segnalato: {issue}")
    if gate.get("error"):
        lines.append(f"- Errore nella revisione: {gate['error']}")
    lines += ["", "## Agenti"]
    for r in report.get("results", []):
        lines.append(f"- **{r['agent_name']}** [{r['status']}]: {r['summary']}")
        for p in r.get("problems", []):
            lines.append(f"  - problema: {p}")
    lines += ["", "## Bozze"]
    if not report.get("drafts"):
        lines.append("Nessuna bozza.")
    for i, d in enumerate(report.get("drafts", [])):
        lines += ["", f"### [{i}] {d.get('platform', '?')} / {d.get('format', '')}  ({d.get('status')})"]
        title = d.get("hook") or d.get("subject") or ""
        if title:
            lines.append(f"**{title}**")
        lines.append("")
        lines.append(d.get("caption") or d.get("body_markdown") or "")
        if d.get("source_link"):
            lines.append(f"\nFonte: {d['source_link']}")
        for p in d.get("problems", []):
            lines.append(f"- da sistemare: {p}")
    return "\n".join(lines) + "\n"


def save_report(report: dict[str, Any], out_dir: Path | None = None) -> Path:
    out = Path(out_dir) if out_dir else data_dir() / "reports"
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    md_path = out / f"report_{stamp}.md"
    atomic_write_text(out / f"report_{stamp}.json", json.dumps(report, indent=2, ensure_ascii=False, default=str))
    atomic_write_text(md_path, render_markdown(report))
    return md_path
