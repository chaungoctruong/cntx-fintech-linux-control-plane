from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store
from app.settings import settings

log = logging.getLogger(__name__)

_LAST_SYNC_TS = 0.0
_SYNC_TTL_SEC = 30.0
MT5_RUNNER_CANARY_BOT_ID = "mt5_runner_canary"

_MANIFEST_FILENAME = "bot_manifest.json"
_BOT_ID_REGEX = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
_PLATFORM_CONTRACT_MUST_NOT_KEYS = (
    "must_not_kill_processes",
    "must_not_choose_terminal_path",
    "must_not_write_postgres_core",
    "must_not_call_redis_directly",
    "must_not_hardcode_production_paths",
)
_KNOWN_SECRET_NAMES = frozenset(
    {
        "WEBHOOK_SECRET",
        "MT5_PASSWORD",
        "DATABASE_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    }
)
_EXECUTION_CONTRACT_TEXT_KEYS = (
    "catalog_lane",
    "bot_type",
    "execution_owner",
    "windows_role",
    "tradingview_webhook_owner",
    "runtime_language",
)
_EXECUTION_CONTRACT_BOOL_KEYS = ("requires_executor_slot",)


def _contract_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _manifest_execution_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    resource_hints = manifest.get("resource_hints") if isinstance(manifest.get("resource_hints"), dict) else {}
    contract: dict[str, Any] = {}
    for source in (resource_hints, manifest):
        for key in _EXECUTION_CONTRACT_TEXT_KEYS:
            value = str(source.get(key) or "").strip()
            if value:
                contract[key] = value
        for key in _EXECUTION_CONTRACT_BOOL_KEYS:
            if key in source:
                contract[key] = _contract_bool(source.get(key))
    return contract


def _allow_single_level() -> bool:
    """Return True when single-level bot packages may be loaded.

    Controlled by env ``MT5_CATALOG_ALLOW_SINGLE_LEVEL``; defaults to True.
    Setting it to ``false`` reverts to the legacy two-level only behavior.
    """
    raw = os.getenv("MT5_CATALOG_ALLOW_SINGLE_LEVEL", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


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
    """Iterate the legacy two-level layout ``bot-trading/<owner>/<repo>/``.

    Top-level directories that are themselves manifest packages
    (``bot-trading/<bot>/bot_manifest.json`` exists) are skipped because they
    are picked up by :func:`_iter_manifest_packages` at depth 1; treating them
    as owner directories would incorrectly iterate ``app/``, ``config/`` etc.
    as if they were individual bots.
    """
    repos: list[Path] = []
    if not root.exists():
        return repos
    for owner_dir in sorted(root.iterdir()):
        if not owner_dir.is_dir() or _should_skip_dir(owner_dir.name):
            continue
        if (owner_dir / _MANIFEST_FILENAME).is_file():
            continue
        for repo_dir in sorted(owner_dir.iterdir()):
            if repo_dir.is_dir() and not _should_skip_dir(repo_dir.name):
                repos.append(repo_dir)
    return repos


def _iter_manifest_packages(root: Path) -> list[Path]:
    """Iterate the single-level layout ``bot-trading/<bot>/`` where the
    directory itself contains a ``bot_manifest.json``.

    Directories without a manifest are ignored at this depth; legacy two-level
    layouts go through :func:`_iter_bot_repo_dirs` instead.
    """
    if not root.exists() or not _allow_single_level():
        return []
    out: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or _should_skip_dir(entry.name):
            continue
        if (entry / _MANIFEST_FILENAME).is_file():
            out.append(entry)
    return out


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


# ---------------------------------------------------------------------------
# Manifest-driven discovery (PACKAGE_STANDARD v1)
#
# Bot packages that ship a ``bot_manifest.json`` are loaded via this path.
# The contract is documented in ``bot-trading/PACKAGE_STANDARD.md``. Packages
# without a manifest still load via the legacy heuristic-based discovery so
# existing bots keep working.
# ---------------------------------------------------------------------------


def _read_manifest(repo_dir: Path) -> Optional[dict[str, Any]]:
    """Load ``bot_manifest.json`` from ``repo_dir`` or return ``None``.

    Malformed JSON is treated as ``None`` and logged at WARNING; the caller is
    expected to fall back to heuristic discovery for such packages so a single
    broken file cannot empty the catalog.
    """
    manifest_path = repo_dir / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - defensive, must not raise
        log.warning(
            "bot_manifest_unreadable path=%s error=%s",
            manifest_path,
            exc,
        )
        return None
    if not isinstance(data, dict):
        log.warning(
            "bot_manifest_not_object path=%s actual_type=%s",
            manifest_path,
            type(data).__name__,
        )
        return None
    return data


def _validate_manifest_v1(
    manifest: dict[str, Any],
    repo_dir: Path,
) -> list[str]:
    """Return a list of human-readable validation errors. Empty = manifest OK.

    Mirrors the ``PACKAGE_STANDARD.md`` v1 MUST contracts. The validation is
    intentionally conservative: ambiguous values are accepted, only
    explicit violations are reported.
    """
    errors: list[str] = []

    if manifest.get("manifest_version") != 1:
        errors.append(
            f"manifest_version must be 1 (got {manifest.get('manifest_version')!r})"
        )

    bot_id = str(manifest.get("bot_id") or "").strip()
    if not _BOT_ID_REGEX.fullmatch(bot_id):
        errors.append(f"bot_id {bot_id!r} does not match {_BOT_ID_REGEX.pattern}")
    if bot_id and bot_id != repo_dir.name:
        errors.append(
            f"bot_id {bot_id!r} must equal directory name {repo_dir.name!r}"
        )

    secrets_source = str(manifest.get("secrets_source") or "").strip()
    if secrets_source and secrets_source != "runtime_context":
        errors.append(
            f"secrets_source must be 'runtime_context' (got {secrets_source!r})"
        )

    platform_contract = manifest.get("platform_contract")
    if not isinstance(platform_contract, dict):
        errors.append("platform_contract is required and must be an object")
    else:
        for key in _PLATFORM_CONTRACT_MUST_NOT_KEYS:
            if platform_contract.get(key) is not True:
                errors.append(
                    f"platform_contract.{key} must be true "
                    f"(got {platform_contract.get(key)!r})"
                )
        if platform_contract.get("tenant_isolated") is not True:
            errors.append(
                "platform_contract.tenant_isolated must be true "
                f"(got {platform_contract.get('tenant_isolated')!r})"
            )

    data_store = manifest.get("data_store")
    if isinstance(data_store, dict) and str(data_store.get("kind") or "").lower() not in {
        "",
        "none",
    }:
        if data_store.get("must_be_separate_from_linux_core_db") is not True:
            errors.append(
                "data_store.must_be_separate_from_linux_core_db must be true "
                f"(got {data_store.get('must_be_separate_from_linux_core_db')!r})"
            )

    secrets_required = manifest.get("secrets_required")
    if secrets_required is None:
        secrets_required = []
    if not isinstance(secrets_required, list):
        errors.append("secrets_required must be a list of env var names")
    else:
        for raw in secrets_required:
            name = str(raw or "").strip()
            if not name:
                errors.append("secrets_required contains empty entry")
                continue
            if not name.isupper() or not name.replace("_", "a").isalnum() or " " in name:
                errors.append(
                    f"secrets_required entry {name!r} is not an env-style name"
                )

    env_example = repo_dir / ".env.example"
    if env_example.is_file():
        watched_secrets = set(secrets_required) if isinstance(secrets_required, list) else set()
        watched_secrets.update(_KNOWN_SECRET_NAMES)
        try:
            for raw_line in env_example.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() in watched_secrets and value.strip():
                    errors.append(
                        f".env.example exposes a non-empty value for {key.strip()!r}"
                    )
        except Exception as exc:  # noqa: BLE001
            errors.append(f".env.example unreadable: {exc}")

    return errors


def _ensure_valid_relative_path(repo_dir: Path, candidate: str) -> Optional[str]:
    if not candidate:
        return None
    candidate_clean = candidate.strip().lstrip("./")
    if not candidate_clean:
        return None
    full = (repo_dir / candidate_clean).resolve()
    try:
        full.relative_to(repo_dir.resolve())
    except ValueError:
        return None
    return str(full.relative_to(repo_dir.resolve()))


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _definition_from_manifest(
    manifest: dict[str, Any],
    repo_dir: Path,
) -> dict[str, Any]:
    """Build a catalog definition dict from a validated v1 manifest."""

    bot_id = str(manifest.get("bot_id") or repo_dir.name).strip()
    bot_code = str(manifest.get("bot_code") or bot_id).strip() or bot_id
    display_name = str(manifest.get("bot_name") or _display_name(bot_id)).strip()
    description = str(manifest.get("description") or "").strip()

    declared_version = str(manifest.get("version") or "").strip()
    version_file_value = _extract_version(repo_dir)
    version = declared_version or version_file_value or "0.1.0"

    runtime_language = str(manifest.get("runtime_language") or "python").strip().lower()
    safe_entrypoint = str(manifest.get("entrypoint") or "").strip()
    legacy = manifest.get("legacy_entrypoints") if isinstance(manifest.get("legacy_entrypoints"), dict) else {}
    fastapi_asgi = str(legacy.get("fastapi_asgi") or "").strip()
    worker_main = str(legacy.get("worker_main") or "").strip()
    runtime_entry = safe_entrypoint or _discover_entrypoint(repo_dir, runtime_language or "python")

    profile_class = str(manifest.get("profile_class") or "normal").strip().lower() or "normal"
    if profile_class not in {"light", "normal", "heavy"}:
        profile_class = "normal"

    strategy_tags = _string_list(manifest.get("strategy_tags"))
    required_params = _string_list(manifest.get("required_params"))
    secrets_required = _string_list(manifest.get("secrets_required"))
    secrets_optional = _string_list(manifest.get("secrets_optional"))

    deployment_model = manifest.get("deployment_model") if isinstance(manifest.get("deployment_model"), dict) else {}
    data_store = manifest.get("data_store") if isinstance(manifest.get("data_store"), dict) else {}
    risk_contract = manifest.get("risk_contract") if isinstance(manifest.get("risk_contract"), dict) else {}
    platform_contract = manifest.get("platform_contract") if isinstance(manifest.get("platform_contract"), dict) else {}
    execution_contract = _manifest_execution_contract(manifest)

    declared_resource_hints = manifest.get("resource_hints") if isinstance(manifest.get("resource_hints"), dict) else {}
    resource_hints = dict(declared_resource_hints)
    resource_hints.update(execution_contract)
    resource_hints.setdefault("profile_class", profile_class)
    # `requires_single_slot` means one deployment must own one MT5 slot. It is
    # not the same as requiring a dedicated/isolated Windows runner.
    resource_hints.setdefault("requires_isolated_runner", False)
    resource_hints.setdefault(
        "high_modify_frequency",
        bool(declared_resource_hints.get("high_modify_frequency", False)),
    )
    resource_hints.setdefault(
        "cpu_class",
        "high" if profile_class == "heavy" else ("medium" if profile_class == "normal" else "low"),
    )

    risk_profile = {
        "class": "elevated" if profile_class == "heavy" else "standard",
        "strategy_tags": list(strategy_tags),
        "requires_sl": bool(risk_contract.get("requires_sl")),
        "requires_tp": bool(risk_contract.get("requires_tp")),
        "max_orders": risk_contract.get("max_orders"),
        "max_basket": risk_contract.get("max_basket"),
        "max_order_per_minute": risk_contract.get("max_order_per_minute"),
        "max_modify_per_minute": risk_contract.get("max_modify_per_minute"),
        "default_volume_min": risk_contract.get("default_volume_min"),
        "default_volume_max": risk_contract.get("default_volume_max"),
        "trading_disabled_by_default": bool(
            risk_contract.get("trading_disabled_by_default", True)
        ),
        "dry_run_by_default": bool(risk_contract.get("dry_run_by_default", True)),
        "risk_contract": dict(risk_contract),
    }

    requirements_path = (
        "requirements.txt" if (repo_dir / "requirements.txt").is_file() else ""
    )
    runtime_env = {
        "language": runtime_language or "python",
        "requirements_path": requirements_path,
        "build_manifest_path": "",
        "runtime_python": str(manifest.get("runtime_python") or "").strip(),
        "safe_entrypoint": safe_entrypoint,
        "fastapi_asgi": fastapi_asgi,
        "worker_main": worker_main,
        "manifest_version": int(manifest.get("manifest_version") or 1),
        "deployment_model": deployment_model,
        "data_store": data_store,
        "platform_contract": platform_contract,
        "secrets_required": secrets_required,
        "secrets_optional": secrets_optional,
        "needs_inbound_webhook": bool(declared_resource_hints.get("needs_inbound_webhook")),
        "default_webhook_port": declared_resource_hints.get("default_webhook_port"),
        "package_dir": str(repo_dir),
    }
    runtime_env.update(execution_contract)
    if risk_contract:
        runtime_env["risk_contract"] = dict(risk_contract)

    config_schema = _ensure_valid_relative_path(repo_dir, str(manifest.get("config_schema") or ""))
    default_config = _ensure_valid_relative_path(repo_dir, str(manifest.get("default_config_path") or ""))
    if not default_config:
        default_config = _default_config_path(repo_dir)
    if config_schema:
        runtime_env["config_schema_path"] = config_schema
    if default_config:
        runtime_env["default_config_path"] = default_config

    return {
        "bot_id": bot_id,
        "bot_name": bot_code,
        "display_name": display_name or bot_id,
        "description": description,
        "language": runtime_language or "python",
        "version": version,
        "runtime_entry": runtime_entry,
        "profile_class": profile_class,
        "strategy_tags": strategy_tags,
        "required_params": required_params,
        "risk_profile": risk_profile,
        "resource_hints": resource_hints,
        "indicator_requirements": _indicator_requirements(_tokenize_name(bot_id)),
        "supports_demo": True,
        "supports_live": True,
        "default_config_path": default_config,
        "config_schema_path": config_schema,
        "runtime_env": runtime_env,
        "checksum": _checksum(repo_dir, runtime_entry, version),
        "source_path": str(repo_dir),
        "metadata": {
            "execution_contract": dict(execution_contract),
            "risk_contract": dict(risk_contract),
            "manifest_contract": dict(execution_contract),
        },
        "manifest_loaded": True,
    }


def _heuristic_definition(repo_dir: Path) -> dict[str, Any]:
    """Build a catalog definition for a legacy package without a manifest."""
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
    return {
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
        "manifest_loaded": False,
    }


def discover_bot_definitions(root: Optional[Path] = None) -> list[dict[str, Any]]:
    """Walk ``bot-trading/`` and return one definition per discovered bot.

    Two layouts are supported:

    1. Single-level manifest packages: ``bot-trading/<bot>/bot_manifest.json``
       (PACKAGE_STANDARD v1). Validated; invalid manifests are skipped with a
       structured warning.
    2. Two-level layout: ``bot-trading/<owner>/<bot>/`` (legacy). If the
       inner directory has a ``bot_manifest.json`` it is treated the same as
       case (1); otherwise heuristic discovery (filename tokens, file
       presence) is used so legacy packages keep working.

    A package directory is read only once even if both layouts could match.
    """
    scan_root = (root or _bot_trading_root()).resolve()
    out: list[dict[str, Any]] = []
    seen: set[Path] = set()

    def _consume(repo_dir: Path, *, manifest_required: bool) -> None:
        """Add a definition for ``repo_dir`` to ``out``.

        ``manifest_required=True`` (depth-1 packages) means an unreadable or
        invalid manifest causes the package to be skipped entirely with a
        warning — heuristic fallback is not allowed because the directory
        only made it into the iterator because it had a manifest file.

        ``manifest_required=False`` (depth-2 legacy) allows packages without
        a manifest to be loaded via heuristics (filename tokens, file
        presence). If a manifest IS present at depth 2 it is still preferred
        over heuristics, and a malformed/invalid manifest still causes the
        package to be skipped (a half-broken entry is worse than absent).
        """
        resolved = repo_dir.resolve()
        if resolved in seen:
            return
        manifest_path = repo_dir / _MANIFEST_FILENAME
        if manifest_path.is_file():
            manifest = _read_manifest(repo_dir)
            if manifest is None:
                # _read_manifest already emitted a structured warning.
                return
            errors = _validate_manifest_v1(manifest, repo_dir)
            if errors:
                log.warning(
                    "bot_manifest_invalid path=%s errors=%s",
                    repo_dir,
                    "; ".join(errors),
                )
                return
            try:
                definition = _definition_from_manifest(manifest, repo_dir)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "bot_manifest_load_failed path=%s error=%s",
                    repo_dir,
                    exc,
                )
                return
            seen.add(resolved)
            out.append(definition)
            log.info(
                "bot_manifest_loaded bot_id=%s version=%s path=%s",
                definition.get("bot_id"),
                definition.get("version"),
                repo_dir,
            )
            return
        if manifest_required:
            # Should not happen — _iter_manifest_packages only yields dirs
            # with a manifest file. Defensive guard against TOCTOU races.
            log.warning(
                "bot_manifest_disappeared path=%s",
                repo_dir,
            )
            return
        try:
            definition = _heuristic_definition(repo_dir)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "bot_heuristic_load_failed path=%s error=%s",
                repo_dir,
                exc,
            )
            return
        seen.add(resolved)
        out.append(definition)

    for repo_dir in _iter_manifest_packages(scan_root):
        _consume(repo_dir, manifest_required=True)
    for repo_dir in _iter_bot_repo_dirs(scan_root):
        _consume(repo_dir, manifest_required=False)

    out.sort(
        key=lambda item: (
            str(item.get("display_name") or "").lower(),
            str(item.get("bot_name") or "").lower(),
        )
    )
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
