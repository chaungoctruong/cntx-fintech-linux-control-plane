from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def build_logger(log_path: Path, *, instance_label: str = "") -> logging.Logger:
    """Build a per-instance logger.

    ``instance_label`` is used as the logger NAME suffix and prepended to the
    formatter so multi-tenant deployments can distinguish lines originating
    from different tenants/instances when streamed through a shared aggregator.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    name = f"gsalgovip.{instance_label}" if instance_label else "gsalgovip"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    label = instance_label or "-"
    fmt = logging.Formatter(
        f"%(asctime)s %(levelname)s [{label}] %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=_int_env("LOG_MAX_BYTES", 10 * 1024 * 1024),
        backupCount=_int_env("LOG_BACKUP_COUNT", 7),
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    error_path = log_path.with_name(f"{log_path.stem}.error{log_path.suffix or '.log'}")
    error_handler = RotatingFileHandler(
        error_path,
        maxBytes=_int_env("LOG_MAX_BYTES", 10 * 1024 * 1024),
        backupCount=_int_env("LOG_BACKUP_COUNT", 7),
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(fmt)
    logger.addHandler(error_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)
    return logger
