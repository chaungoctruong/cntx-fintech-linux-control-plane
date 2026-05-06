# -*- coding: utf-8 -*-
"""
BotCatalogRepository: Data access for bot_catalog table.
Encapsulates available bot definitions (code, name, strategy, tags).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.store import Store


class BotCatalogRepository:
    """Repository for bot_catalog: available bots for MiniApp selection."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def list(self, *, only_enabled: bool = True) -> list[Dict[str, Any]]:
        """List bots. Returns [{code, name, strategy, tags, enabled}, ...]."""
        return self._store.list_bot_catalog(only_enabled=only_enabled)

    def upsert(
        self,
        *,
        bot_code: str,
        bot_name: str,
        strategy: Optional[str] = None,
        tags: Optional[list[str]] = None,
        enabled: bool = True,
    ) -> None:
        """Create or update bot in catalog."""
        self._store.upsert_bot_catalog(
            bot_code=bot_code,
            bot_name=bot_name,
            strategy=strategy,
            tags=tags,
            enabled=enabled,
        )
