from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.bot_catalog.mt5_repository_loader import (
    discover_bot_definitions,
    is_disabled_mt5_bot_catalog_entry,
)
from app.settings import settings


def normalize_bot_identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


@dataclass(frozen=True)
class LicensedBotPackage:
    code: str
    name: str
    package_path: str
    version: str
    source_path: str
    identities: frozenset[str]


class BotTradingLicenseCatalog:
    """Read-only allowlist backed by physical packages in bot-trading/.

    Token licensing must never grant access to a bot that is not packaged for
    the platform. The DB token row can say "gsalgovip", but this catalog is the
    source of truth that the package exists and is not disabled.
    """

    _cache: tuple[float, dict[str, LicensedBotPackage]] = (0.0, {})

    def __init__(self, *, root: Optional[Path] = None, cache_ttl_sec: float = 15.0) -> None:
        self._root = root
        self._cache_ttl_sec = max(0.0, float(cache_ttl_sec))

    def _configured_root(self) -> Optional[Path]:
        raw = str(getattr(settings, "BOT_TOKEN_TRADING_ROOT", "") or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve()

    def _build(self) -> dict[str, LicensedBotPackage]:
        root = self._root or self._configured_root()
        definitions = discover_bot_definitions(root=root) if root is not None else discover_bot_definitions()
        out: dict[str, LicensedBotPackage] = {}
        for definition in definitions:
            if is_disabled_mt5_bot_catalog_entry(definition):
                continue
            source_path = str(definition.get("source_path") or "").strip()
            if source_path.startswith("system://"):
                continue
            code = str(definition.get("bot_code") or definition.get("bot_id") or "").strip()
            name = str(definition.get("display_name") or definition.get("bot_name") or code).strip()
            if not code:
                continue
            identities = {
                normalize_bot_identity(code),
                normalize_bot_identity(definition.get("bot_id")),
                normalize_bot_identity(definition.get("bot_code")),
                normalize_bot_identity(definition.get("bot_name")),
                normalize_bot_identity(definition.get("display_name")),
                normalize_bot_identity(Path(source_path).name if source_path else ""),
            }
            identities.discard("")
            package = LicensedBotPackage(
                code=code,
                name=name or code,
                package_path=str(definition.get("package_path") or source_path or ""),
                version=str(definition.get("version") or ""),
                source_path=source_path,
                identities=frozenset(identities),
            )
            for identity in package.identities:
                out[identity] = package
        return out

    def _packages_by_identity(self) -> dict[str, LicensedBotPackage]:
        now = time.time()
        cached_at, cached = self.__class__._cache
        if cached and self._cache_ttl_sec > 0 and now - cached_at <= self._cache_ttl_sec:
            return cached
        rebuilt = self._build()
        self.__class__._cache = (now, rebuilt)
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
            }
            for package in packages
        ]
