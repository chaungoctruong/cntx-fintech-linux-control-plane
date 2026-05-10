from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 7


def _structured_log_enabled() -> bool:
    raw = (os.getenv("STRUCTURED_LOG_FILE_ENABLED", "1") or "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_log_dir() -> Path:
    raw_hubbot = os.getenv("HUBBOT_LOG_DIR", "").strip()
    if raw_hubbot:
        return Path(raw_hubbot).expanduser().resolve()
    raw_root = (os.getenv("CNTX_LOG_DIR") or os.getenv("LOG_DIR") or "").strip()
    if raw_root:
        return (Path(raw_root).expanduser().resolve() / "hubbot").resolve()
    return (project_root() / "logs" / "hubbot").resolve()


def _level(value: int | str) -> int:
    if isinstance(value, int):
        return value
    return int(getattr(logging, str(value or "INFO").upper(), logging.INFO))


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _has_handler(logger: logging.Logger, *, marker: str, path: Path | None = None) -> bool:
    for handler in logger.handlers:
        if getattr(handler, "_cntx_marker", "") != marker:
            continue
        if path is None:
            return True
        if Path(getattr(handler, "baseFilename", "")).resolve() == path.resolve():
            return True
    return False


def _add_rotating_handler(
    logger: logging.Logger,
    *,
    path: Path,
    level: int,
    formatter: logging.Formatter,
    marker: str,
) -> None:
    if _has_handler(logger, marker=marker, path=path):
        return
    handler = RotatingFileHandler(
        path,
        maxBytes=_int_env("LOG_MAX_BYTES", _DEFAULT_MAX_BYTES),
        backupCount=_int_env("LOG_BACKUP_COUNT", _DEFAULT_BACKUP_COUNT),
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    setattr(handler, "_cntx_marker", marker)
    logger.addHandler(handler)


def configure_service_logging(service_name: str, *, level: int | str = logging.INFO) -> Path:
    level_no = _level(level)
    service_slug = service_name.strip().lower().replace("_", "-").replace(" ", "-")
    log_dir = resolve_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    service_log = log_dir / f"{service_slug}.log"
    error_log = log_dir / f"{service_slug}.error.log"
    formatter = logging.Formatter(_FORMAT)
    root = logging.getLogger()
    root.setLevel(min(root.level or level_no, level_no))

    if not _has_handler(root, marker="console"):
        console = logging.StreamHandler()
        console.setLevel(level_no)
        console.setFormatter(formatter)
        setattr(console, "_cntx_marker", "console")
        root.addHandler(console)

    _add_rotating_handler(
        root,
        path=service_log,
        level=level_no,
        formatter=formatter,
        marker=f"{service_slug}:service",
    )
    _add_rotating_handler(
        root,
        path=error_log,
        level=logging.ERROR,
        formatter=formatter,
        marker=f"{service_slug}:error",
    )

    if _structured_log_enabled():
        try:
            from app.log_context import JsonFormatter, install_context_filter
            json_path = log_dir / f"{service_slug}.jsonl"
            _add_rotating_handler(
                root,
                path=json_path,
                level=level_no,
                formatter=JsonFormatter(service_name=service_slug),
                marker=f"{service_slug}:json",
            )
            install_context_filter("")
        except Exception:
            pass

    logging.captureWarnings(True)
    return log_dir
