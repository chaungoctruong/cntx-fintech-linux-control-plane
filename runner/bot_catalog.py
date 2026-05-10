from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

_MANIFEST_FILENAME = "bot_manifest.json"
_BOT_ID_REGEX = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "data", "logs"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_bot_trading_root() -> Path:
    """Resolve the Windows runner bot package root.

    `BOT_TRADING_ROOT` is the preferred Windows Phase 1 setting. The MT5/RUNNER
    aliases are accepted so older runner env files can opt in without code
    changes.
    """
    for key in ("BOT_TRADING_ROOT", "RUNNER_BOT_TRADING_ROOT", "MT5_BOT_TRADING_ROOT"):
        raw = os.getenv(key, "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
    return (_repo_root() / "bot-trading").resolve()


def _read_json(path: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - catalog discovery must not crash runner
        return None, str(exc)
    if not isinstance(data, dict):
        return None, f"manifest must be a JSON object, got {type(data).__name__}"
    return data, None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_relpath(root: Path, raw: Any) -> str:
    text = str(raw or "").strip().replace("\\", "/").lstrip("./")
    if not text:
        return ""
    full = (root / text).resolve()
    try:
        full.relative_to(root.resolve())
    except ValueError:
        return ""
    return str(full.relative_to(root.resolve())).replace("\\", "/")


def _iter_files_for_checksum(package_dir: Path) -> Iterable[Path]:
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(package_dir).parts):
            continue
        yield path


def _checksum_package(package_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in _iter_files_for_checksum(package_dir):
        rel = path.relative_to(package_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except Exception:
            continue
        digest.update(b"\0")
    return digest.hexdigest()


def _should_skip_dir(path: Path) -> bool:
    name = path.name.strip()
    return not name or name.startswith(".") or name.startswith("_")


def _iter_manifest_dirs(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []

    out: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or _should_skip_dir(entry):
            continue
        if (entry / _MANIFEST_FILENAME).is_file():
            out.append(entry)
            continue
        for child in sorted(entry.iterdir()):
            if child.is_dir() and not _should_skip_dir(child) and (child / _MANIFEST_FILENAME).is_file():
                out.append(child)
    return out


@dataclass(frozen=True)
class BotCatalogProvider:
    bot_trading_root: Path
    source: str = "disk"

    @classmethod
    def from_env(cls) -> "BotCatalogProvider":
        return cls(default_bot_trading_root())

    def discover(self) -> dict[str, Any]:
        root = self.bot_trading_root.resolve()
        if not root.exists() or not root.is_dir():
            return {
                "source": self.source,
                "bot_trading_root": str(root),
                "count": 0,
                "bots": [],
                "errors": [
                    {
                        "package_dir": str(root),
                        "reason": "bot_trading_root_missing",
                    }
                ],
            }

        bots: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for package_dir in _iter_manifest_dirs(root):
            manifest, error = _read_json(package_dir / _MANIFEST_FILENAME)
            if manifest is None:
                errors.append(
                    {
                        "package_dir": str(package_dir),
                        "reason": "manifest_unreadable",
                        "errors": [error or "manifest_unreadable"],
                    }
                )
                continue
            validation_errors = self._validate_manifest(package_dir, manifest)
            if validation_errors:
                errors.append(
                    {
                        "package_dir": str(package_dir),
                        "reason": "manifest_invalid",
                        "errors": validation_errors,
                    }
                )
                continue
            bots.append(self._bot_payload(package_dir, manifest))

        bots.sort(key=lambda item: (str(item.get("bot_id") or ""), str(item.get("version") or "")))
        payload: dict[str, Any] = {
            "source": self.source,
            "bot_trading_root": str(root),
            "count": len(bots),
            "bots": bots,
        }
        if errors:
            payload["errors"] = errors
        return payload

    def register_payload_fields(self) -> dict[str, Any]:
        catalog = self.discover()
        bots = [item for item in catalog.get("bots", []) if isinstance(item, dict)]
        bot_ids = [str(item.get("bot_id") or item.get("bot_code") or "").strip() for item in bots]
        bot_ids = [item for item in bot_ids if item]
        return {
            "available_bots": bot_ids,
            "available_bot_names": list(bot_ids),
            "bot_catalog": catalog,
        }

    def _validate_manifest(self, package_dir: Path, manifest: dict[str, Any]) -> list[str]:
        errors: list[str] = []

        if manifest.get("manifest_version") != 1:
            errors.append(f"manifest_version must be 1 (got {manifest.get('manifest_version')!r})")

        bot_id = str(manifest.get("bot_id") or "").strip()
        if not _BOT_ID_REGEX.fullmatch(bot_id):
            errors.append(f"bot_id {bot_id!r} does not match {_BOT_ID_REGEX.pattern}")
        if bot_id and bot_id != package_dir.name:
            errors.append(f"bot_id {bot_id!r} must equal directory name {package_dir.name!r}")

        version = str(manifest.get("version") or "").strip()
        version_file = _read_text(package_dir / "VERSION")
        if not version:
            errors.append("version is required")
        if version_file and version and version_file.splitlines()[0].strip() != version:
            errors.append("manifest.version must match VERSION")

        runtime_language = str(manifest.get("runtime_language") or "").strip().lower()
        if runtime_language != "python":
            errors.append(f"runtime_language must be 'python' for Phase 1 (got {runtime_language!r})")
        if not str(manifest.get("entrypoint") or "").strip():
            errors.append("entrypoint is required")

        for rel in ("VERSION", "README.md", "config/schema.json", "config/default.json", "app/runner_impl.py"):
            if not (package_dir / rel).is_file():
                errors.append(f"required file missing: {rel}")
        if runtime_language == "python" and not (package_dir / "requirements.txt").is_file():
            errors.append("required file missing: requirements.txt")

        config_schema = _safe_relpath(package_dir, manifest.get("config_schema"))
        default_config_path = _safe_relpath(package_dir, manifest.get("default_config_path"))
        if not config_schema or not (package_dir / config_schema).is_file():
            errors.append("config_schema must point to an existing package file")
        if not default_config_path or not (package_dir / default_config_path).is_file():
            errors.append("default_config_path must point to an existing package file")

        secrets_source = str(manifest.get("secrets_source") or "").strip()
        if secrets_source != "runtime_context":
            errors.append("secrets_source must be 'runtime_context'")

        platform_contract = _as_dict(manifest.get("platform_contract"))
        if not platform_contract:
            errors.append("platform_contract is required and must be an object")
        else:
            for key in (
                "receives_runtime_context",
                "receives_stop_event",
                "must_not_kill_processes",
                "must_not_choose_terminal_path",
                "must_not_call_redis_directly",
                "must_not_hardcode_production_paths",
                "tenant_isolated",
            ):
                if platform_contract.get(key) is not True:
                    errors.append(f"platform_contract.{key} must be true")
            if (
                platform_contract.get("must_not_write_postgres_core") is not True
                and platform_contract.get("must_not_write_postgres") is not True
            ):
                errors.append("platform_contract.must_not_write_postgres_core must be true")

        return errors

    def _bot_payload(self, package_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        bot_id = str(manifest.get("bot_id") or package_dir.name).strip()
        bot_code = str(manifest.get("bot_code") or bot_id).strip() or bot_id
        bot_name = str(manifest.get("bot_name") or bot_code).strip() or bot_code
        runtime_language = str(manifest.get("runtime_language") or "python").strip().lower() or "python"
        entrypoint = str(manifest.get("entrypoint") or "").strip()
        config_schema = _safe_relpath(package_dir, manifest.get("config_schema"))
        default_config_path = _safe_relpath(package_dir, manifest.get("default_config_path"))

        return {
            "manifest_version": int(manifest.get("manifest_version") or 1),
            "bot_id": bot_id,
            "bot_code": bot_code,
            "bot_name": bot_name,
            "display_name": bot_name,
            "version": str(manifest.get("version") or _read_text(package_dir / "VERSION") or "0.1.0").strip(),
            "runtime_language": runtime_language,
            "language": runtime_language,
            "entrypoint": entrypoint,
            "runtime_entry": entrypoint,
            "legacy_entrypoints": _as_dict(manifest.get("legacy_entrypoints")),
            "profile_class": str(manifest.get("profile_class") or "normal").strip().lower() or "normal",
            "strategy_tags": _as_string_list(manifest.get("strategy_tags")),
            "required_params": _as_string_list(manifest.get("required_params")),
            "resource_hints": _as_dict(manifest.get("resource_hints")),
            "risk_contract": _as_dict(manifest.get("risk_contract")),
            "config_schema": config_schema,
            "default_config_path": default_config_path,
            "checksum": _checksum_package(package_dir),
            "package_dir": package_dir.name,
            "package_path": str(package_dir.relative_to(self.bot_trading_root.resolve())).replace("\\", "/"),
            "supports_demo": True,
            "supports_live": True,
        }


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Discover Windows runner bot packages from disk.")
    parser.add_argument("--root", default="", help="Path to bot-trading root. Defaults to BOT_TRADING_ROOT or ./bot-trading.")
    parser.add_argument("--expect-bot", default="", help="Fail unless this bot_id is discovered.")
    parser.add_argument("--expect-version", default="", help="Fail unless --expect-bot has this version.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON.")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve() if args.root else default_bot_trading_root()
    provider = BotCatalogProvider(root)
    catalog = provider.discover()
    print(json.dumps(catalog, indent=None if args.compact else 2, sort_keys=True))

    if args.expect_bot:
        matches = [
            item for item in catalog.get("bots", [])
            if isinstance(item, dict) and item.get("bot_id") == args.expect_bot
        ]
        if not matches:
            print(f"expected bot not found: {args.expect_bot}", file=sys.stderr)
            return 1
        if args.expect_version and str(matches[0].get("version") or "") != args.expect_version:
            print(
                f"expected {args.expect_bot}@{args.expect_version}, got {matches[0].get('version')}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
