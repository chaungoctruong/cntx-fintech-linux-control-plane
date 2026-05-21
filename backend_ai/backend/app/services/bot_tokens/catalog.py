from __future__ import annotations

import json
import re
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.bot_catalog.mt5_repository_loader import (
    discover_bot_definitions,
    is_disabled_mt5_bot_catalog_entry,
)
from app.settings import settings


log = logging.getLogger(__name__)


def normalize_bot_identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


@dataclass(frozen=True)
class LicensedBotPackage:
    code: str
    name: str
    package_path: str
    version: str
    source_path: str
    identities: frozenset[str]
    catalog_source: str = "package"


class BotTradingLicenseCatalog:
    """Read-only allowlist backed by platform bot catalog.

    Physical packages in ``bot-trading/`` and bot rows reported by Windows
    runners both flow into ``bot_catalog``. Token licensing should follow that
    platform catalog so new bots do not need hardcoded names.
    """

    def __init__(self, *, root: Optional[Path] = None, store: Any = None, cache_ttl_sec: float = 15.0) -> None:
        self._root = root
        self._store = store
        self._cache_ttl_sec = max(0.0, float(cache_ttl_sec))
        self._cache: tuple[float, dict[str, LicensedBotPackage]] = (0.0, {})

    def _configured_root(self) -> Optional[Path]:
        raw = str(getattr(settings, "BOT_TOKEN_TRADING_ROOT", "") or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve()

    def _package_from_definition(self, definition: dict[str, Any], *, catalog_source: str) -> Optional[LicensedBotPackage]:
        if is_disabled_mt5_bot_catalog_entry(definition):
            return None
        source_path = str(definition.get("source_path") or "").strip()
        if source_path.startswith("system://"):
            return None
        code = str(definition.get("bot_code") or definition.get("bot_id") or "").strip()
        name = str(definition.get("display_name") or definition.get("bot_name") or code).strip()
        if not code:
            return None
        source_name = ""
        if source_path and "://" not in source_path:
            source_name = Path(source_path).name
        elif source_path.startswith("runner://"):
            source_name = source_path.rstrip("/").rsplit("/", 1)[-1]
        identities = {
            normalize_bot_identity(code),
            normalize_bot_identity(definition.get("bot_id")),
            normalize_bot_identity(definition.get("bot_code")),
            normalize_bot_identity(definition.get("bot_name")),
            normalize_bot_identity(definition.get("display_name")),
            normalize_bot_identity(source_name),
        }
        for section_key in ("resource_hints", "runtime_env", "metadata", "metadata_json"):
            section = _json_dict(definition.get(section_key))
            identities.update(
                normalize_bot_identity(section.get(key))
                for key in ("bot_id", "bot_code", "bot_name", "display_name", "package_dir")
            )
        identities.discard("")
        return LicensedBotPackage(
            code=code,
            name=name or code,
            package_path=str(definition.get("package_path") or source_path or ""),
            version=str(definition.get("version") or ""),
            source_path=source_path,
            identities=frozenset(identities),
            catalog_source=catalog_source,
        )

    def _db_catalog_definitions(self) -> list[dict[str, Any]]:
        if self._store is None:
            return []

        def _do(_con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT
                    bot_code,
                    bot_name,
                    display_name,
                    version,
                    profile_class,
                    resource_hints,
                    runtime_env,
                    source_path,
                    metadata_json
                FROM bot_catalog
                WHERE enabled = TRUE
                  AND status IN ('ACTIVE', 'DEPRECATED')
                ORDER BY display_name ASC, bot_name ASC, bot_code ASC
                """
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        try:
            return self._store._with_retry_read(_do)
        except Exception as exc:
            log.warning("bot_token_catalog_db_read_failed: %s", str(exc)[:180])
            return []

    def _build(self) -> dict[str, LicensedBotPackage]:
        root = self._root or self._configured_root()
        definitions = discover_bot_definitions(root=root) if root is not None else discover_bot_definitions()
        out: dict[str, LicensedBotPackage] = {}
        for definition in definitions:
            package = self._package_from_definition(definition, catalog_source="package")
            if package is None:
                continue
            for identity in package.identities:
                out[identity] = package
        for definition in self._db_catalog_definitions():
            package = self._package_from_definition(definition, catalog_source="bot_catalog")
            if package is None:
                continue
            for identity in package.identities:
                out[identity] = package
        return out

    def _packages_by_identity(self) -> dict[str, LicensedBotPackage]:
        now = time.time()
        cached_at, cached = self._cache
        if cached and self._cache_ttl_sec > 0 and now - cached_at <= self._cache_ttl_sec:
            return cached
        rebuilt = self._build()
        self._cache = (now, rebuilt)
        return rebuilt

    def resolve(self, *values: Any) -> Optional[LicensedBotPackage]:
        packages = self._packages_by_identity()
        for value in values:
            identity = normalize_bot_identity(value)
            if identity and identity in packages:
                return packages[identity]
        return None

    def list_packages(self) -> list[dict[str, Any]]:
        seen: set[str] = set()
        packages: list[LicensedBotPackage] = []
        for package in self._packages_by_identity().values():
            identity = normalize_bot_identity(package.code)
            if identity in seen:
                continue
            seen.add(identity)
            packages.append(package)
        packages.sort(key=lambda item: (item.name.lower(), item.code.lower()))
        return [
            {
                "bot_code": package.code,
                "bot_name": package.name,
                "package_path": package.package_path,
                "source_path": package.source_path,
                "version": package.version,
                "catalog_source": package.catalog_source,
            }
            for package in packages
        ]
