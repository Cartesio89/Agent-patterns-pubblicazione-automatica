"""Registro degli agenti. Per aggiungerne uno: crea il modulo e aggiungilo qui."""
from __future__ import annotations

from ..core.base_agent import BaseAgent
from .instagram_draft import InstagramDraft
from .news_scout import NewsScout
from .newsletter_draft import NewsletterDraft

REGISTRY: dict[str, type[BaseAgent]] = {
    NewsScout.name: NewsScout,
    InstagramDraft.name: InstagramDraft,
    NewsletterDraft.name: NewsletterDraft,
}
