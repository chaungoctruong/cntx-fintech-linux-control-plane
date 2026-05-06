from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store
from app.settings import settings

_LAST_SYNC_TS = 0.0
_SYNC_TTL_SEC = 30.0
MT5_RUNNER_CANARY_BOT_ID = "mt5_runner_canary"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _bot_trading_root() -> Path:
    return (_project_root() / "bot-trading").resolve()


def _slugify(value: str) -> str:
    out: list[str] = []
    previous_sep = False
    for ch in str(value or "").lower():
        if ch.isalnum():
            out.append(ch)
            previous_sep = False
            continue
        if not previous_sep:
            out.append("_")
            previous_sep = True
    return "".join(out).strip("_")


def _normalize_bot_identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def disabled_mt5_bot_identities() -> list[str]:
    raw = getattr(settings, "MT5_BOT_CATALOG_DISABLED_CODES", []) or []
    if isinstance(raw, str):
        values = [part.strip() for part in re.split(r"[,\n]+", raw) if part.strip()]
    else:
        values = [str(item or "").strip() for item in raw if str(item or "").strip()]
    return sorted({_normalize_bot_identity(item) for item in values if _normalize_bot_identity(item)})


def _bot_identity_values(bot: dict[str, Any]) -> list[str]:
    values = [
        bot.get("bot_id"),
        bot.get("bot_code"),
        bot.get("bot_name"),
        bot.get("display_name"),
    ]
    for key in ("runtime_env", "resource_hints", "metadata"):
        payload = bot.get(key)
        if isinstance(payload, dict):
            values.extend(
                [
                    payload.get("package_dir"),
                    payload.get("bot_id"),
                    payload.get("bot_code"),
                    payload.get("bot_name"),
                ]
            )
    return [str(value or "").strip() for value in values if str(value or "").strip()]


def is_disabled_mt5_bot_catalog_entry(bot: Optional[dict[str, Any]]) -> bool:
    if not bot:
        return False
    disabled = set(disabled_mt5_bot_identities())
    if not disabled:
        return False
    return any(_normalize_bot_identity(value) in disabled for value in _bot_identity_values(bot))


def _should_skip_dir(name: str) -> bool:
    value = str(name or "").strip()
    return not value or value.startswith(".") or value.startswith("_")


def _iter_bot_repo_dirs(root: Path) -> list[Path]:
    repos: list[Path] = []
    if not root.exists():
        return repos
    for owner_dir in sorted(root.iterdir()):
        if not owner_dir.is_dir() or _should_skip_dir(owner_dir.name):
            continue
        for repo_dir in sorted(owner_dir.iterdir()):
            if repo_dir.is_dir() and not _should_skip_dir(repo_dir.name):
                repos.append(repo_dir)
    return repos


def _read_text_if_exists(path: Path) -> str:
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return ""


def _first_existing(root: Path, candidates: list[str]) -> Optional[Path]:
    for rel in candidates:
        path = root / rel
        if path.exists() and path.is_file():
            return path
    return None


def _discover_language(root: Path) -> str:
    if _first_existing(root, ["ctrader_contract.py"]):
        return "adapter"
    if _first_existing(root, ["main.cpp", "src/main.cpp", "CMakeLists.txt"]):
        return "cpp"
    if _first_existing(root, ["main.py", "start.py", "run.py", "app.py", "bot.py"]):
        return "python"
    for path in root.rglob("*.py"):
        if path.is_file():
            return "python"
    for path in root.rglob("*.cpp"):
        if path.is_file():
            return "cpp"
    return "other"


def _discover_entrypoint(root: Path, language: str) -> str:
    if language == "adapter":
        path = _first_existing(root, ["ctrader_contract.py"])
        return str(path.relative_to(root)) if path else ""
    if language == "cpp":
        path = _first_existing(root, ["main.cpp", "src/main.cpp", "CMakeLists.txt"])
        return str(path.relative_to(root)) if path else ""
    path = _first_existing(root, ["main.py", "start.py", "run.py", "app.py", "bot.py", "live_trade_mt5.py"])
    if path:
        return str(path.relative_to(root))
    for candidate in root.rglob("*.py"):
        if candidate.is_file():
            return str(candidate.relative_to(root))
    return ""


def _extract_version(root: Path) -> str:
    version_file = _first_existing(root, ["VERSION", ".version"])
    if version_file:
        raw = _read_text_if_exists(version_file).strip()
        if raw:
            return raw.splitlines()[0].strip()
    pyproject = _read_text_if_exists(root / "pyproject.toml")
    if pyproject:
        match = re.search(r'version\s*=\s*"([^"]+)"', pyproject)
        if match:
            return match.group(1).strip()
    setup_py = _read_text_if_exists(root / "setup.py")
    if setup_py:
        match = re.search(r'version\s*=\s*"([^"]+)"', setup_py)
        if match:
            return match.group(1).strip()
    return "0.1.0"


def _tokenize_name(raw: str) -> list[str]:
    tokens = [part for part in re.split(r"[-_]+", str(raw or "").strip()) if part]
    return [token.lower() for token in tokens]


def _display_name(repo_name: str) -> str:
    tokens = _tokenize_name(repo_name.removesuffix("-main"))
    return " ".join(token.upper() if token.isupper() else token.capitalize() for token in tokens) or repo_name


def _indicator_requirements(tokens: list[str]) -> list[str]:
    known = {"ema", "smc", "rsi", "macd", "atr", "hmm", "xgb"}
    return sorted({token for token in tokens if token in known})


def _profile_class(tokens: list[str]) -> str:
    heavy_markers = {"dca", "basket", "heavy", "ensemble", "rl", "dreamer", "xgb", "macro", "hmm", "gpt"}
    normal_markers = {"smc", "ema", "scalper", "regime", "ppo", "trauma"}
    if any(token in heavy_markers for token in tokens):
        return "heavy"
    if any(token in normal_markers for token in tokens):
        return "normal"
    return "light"


def _resource_hints(tokens: list[str], profile_class: str) -> dict[str, Any]:
    isolated = any(token in {"dca", "basket", "ensemble", "rl"} for token in tokens)
    high_modify = any(token in {"dca", "basket", "grid"} for token in tokens)
    return {
        "profile_class": profile_class,
        "requires_isolated_runner": isolated,
        "high_modify_frequency": high_modify,
        "cpu_class": "high" if profile_class == "heavy" else ("medium" if profile_class == "normal" else "low"),
    }


def _default_config_path(root: Path) -> Optional[str]:
    path = _first_existing(root, [".env.example", "config.json", "config/default.json"])
    return str(path.relative_to(root)) if path else None


def _runtime_env(root: Path, language: str) -> dict[str, Any]:
    requirements = _first_existing(root, ["requirements.txt"])
    build_file = _first_existing(root, ["CMakeLists.txt", "setup.py", "pyproject.toml"])
    return {
        "language": language,
        "requirements_path": str(requirements.relative_to(root)) if requirements else "",
        "build_manifest_path": str(build_file.relative_to(root)) if build_file else "",
    }


def _required_params(root: Path) -> list[str]:
    contract_file = root / "ctrader_contract.py"
    if contract_file.exists():
        text = _read_text_if_exists(contract_file)
        match = re.search(r"DEFAULT_CONFIG\s*=\s*(\{.*?\})", text, flags=re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(1).replace("'", '"'))
                if isinstance(payload, dict):
                    return sorted(str(key).strip() for key in payload.keys() if str(key).strip())
            except Exception:
                return []
    return []


def _checksum(root: Path, entrypoint: str, version: str) -> str:
    digest = hashlib.sha1()
    digest.update(str(root).encode("utf-8", errors="ignore"))
    digest.update(str(entrypoint).encode("utf-8", errors="ignore"))
    digest.update(str(version).encode("utf-8", errors="ignore"))
    try:
        stat = root.stat()
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
    except FileNotFoundError:
        pass
    return digest.hexdigest()


def _system_checksum(bot_id: str, version: str, runtime_entry: str) -> str:
    digest = hashlib.sha1()
    digest.update(str(bot_id).encode("utf-8", errors="ignore"))
    digest.update(str(version).encode("utf-8", errors="ignore"))
    digest.update(str(runtime_entry).encode("utf-8", errors="ignore"))
    return digest.hexdigest()


def _strategy_tags(tokens: list[str]) -> list[str]:
    out = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def discover_bot_definitions(root: Optional[Path] = None) -> list[dict[str, Any]]:
    scan_root = (root or _bot_trading_root()).resolve()
    out: list[dict[str, Any]] = []
    for repo_dir in _iter_bot_repo_dirs(scan_root):
        repo_name = repo_dir.name.removesuffix("-main")
        tokens = _tokenize_name(repo_name)
        language = _discover_language(repo_dir)
        version = _extract_version(repo_dir)
        entrypoint = _discover_entrypoint(repo_dir, language)
        profile_class = _profile_class(tokens)
        strategy_tags = _strategy_tags(tokens)
        indicator_requirements = _indicator_requirements(tokens)
        risk_profile = {
            "class": "elevated" if profile_class == "heavy" else "standard",
            "strategy_tags": strategy_tags,
        }
        definition = {
            "bot_id": _slugify(repo_name),
            "bot_name": _slugify(repo_name),
            "display_name": _display_name(repo_name),
            "language": language,
            "version": version,
            "runtime_entry": entrypoint,
            "profile_class": profile_class,
            "strategy_tags": strategy_tags,
            "required_params": _required_params(repo_dir),
            "risk_profile": risk_profile,
            "resource_hints": _resource_hints(tokens, profile_class),
            "indicator_requirements": indicator_requirements,
            "supports_demo": True,
            "supports_live": True,
            "default_config_path": _default_config_path(repo_dir),
            "runtime_env": _runtime_env(repo_dir, language),
            "checksum": _checksum(repo_dir, entrypoint, version),
            "source_path": str(repo_dir),
        }
        out.append(definition)
    out.sort(key=lambda item: (str(item.get("display_name") or "").lower(), str(item.get("bot_name") or "").lower()))
    return out


def system_bot_definitions() -> list[dict[str, Any]]:
    """Built-in control-plane bot metadata that is not sourced from bot-trading."""
    version = "0.1.0"
    runtime_entry = "mt5_runner_canary.noop"
    bot_id = MT5_RUNNER_CANARY_BOT_ID
    risk_profile = {
        "class": "canary",
        "execute_orders": False,
        "allow_live_orders": False,
        "lot_size": 0.01,
        "max_positions": 0,
        "martingale": False,
        "dca": False,
        "purpose": "deployment_lifecycle_canary",
    }
    runtime_env = {
        "runtime": "windows_mt5",
        "lane": "mt5_runner",
        "broker_type": "mt5",
        "execution_mode": "dry_run",
        "execute_orders": False,
        "allow_live_orders": False,
        "no_order": True,
        "noop": True,
        "canary": True,
    }
    return [
        {
            "bot_id": bot_id,
            "bot_name": bot_id,
            "display_name": "MT5 Runner Canary",
            "language": "noop",
            "version": version,
            "runtime_entry": runtime_entry,
            "profile_class": "light",
            "strategy_tags": ["canary", "noop", "mt5", "mt5_runner", "lifecycle"],
            "required_params": [],
            "risk_profile": risk_profile,
            "resource_hints": {
                "profile_class": "light",
                "requires_isolated_runner": False,
                "high_modify_frequency": False,
                "cpu_class": "low",
                "runtime": "windows_mt5",
                "lane": "mt5_runner",
            },
            "indicator_requirements": [],
            "supports_demo": True,
            "supports_live": False,
            "default_config_path": None,
            "runtime_env": runtime_env,
            "checksum": _system_checksum(bot_id, version, runtime_entry),
            "source_path": "system://cntx-labs/mt5_runner_canary",
        }
    ]


class MT5BotCatalogLoader:
    def __init__(self, *, repo: Optional[ControlPlaneRepository] = None, root: Optional[Path] = None) -> None:
        self._repo = repo or ControlPlaneRepository(get_process_store())
        self._root = root

    def sync_catalog(self, *, force: bool = False) -> list[dict[str, Any]]:
        global _LAST_SYNC_TS
        now = time.time()
        if not force and (now - _LAST_SYNC_TS) < _SYNC_TTL_SEC:
            return self._repo.list_bots()

        discovered = discover_bot_definitions(root=self._root)
        discovered.extend(system_bot_definitions())
        discovered = [definition for definition in discovered if not is_disabled_mt5_bot_catalog_entry(definition)]
        active_codes: list[str] = []
        for definition in discovered:
            active_codes.append(str(definition.get("bot_id") or ""))
            self._repo.upsert_bot_catalog_entry(definition)
            self._repo.upsert_bot_version(
                bot_id=str(definition.get("bot_id") or ""),
                version=str(definition.get("version") or "0.1.0"),
                checksum=str(definition.get("checksum") or ""),
                source_path=str(definition.get("source_path") or ""),
                metadata=definition,
            )
        self._repo.retire_missing_bots(active_bot_ids=active_codes)
        if hasattr(self._repo, "retire_bot_catalog_entries"):
            self._repo.retire_bot_catalog_entries(bot_identities=disabled_mt5_bot_identities())
        _LAST_SYNC_TS = now
        return self._repo.list_bots()

    def get_bot(self, bot_name: str, *, force_sync: bool = False) -> Optional[dict[str, Any]]:
        self.sync_catalog(force=force_sync)
        return self._repo.get_bot_by_name(bot_name=bot_name)
