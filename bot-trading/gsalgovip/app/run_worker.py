from __future__ import annotations

from pathlib import Path

from .config import Settings
from .logger import build_logger
from .state_store import StateStore
from .worker import SignalWorker


def main() -> None:
    root_dir = Path(__file__).resolve().parents[1]
    settings = Settings.from_env(root_dir)
    logger = build_logger(settings.log_path, instance_label=settings.runtime_label)
    logger.info(
        "gsalgovip_worker_boot tenant_id=%s instance_id=%s db=%s "
        "dry_run=%s trading_enabled=%s",
        settings.tenant_id or "-",
        settings.instance_id or "-",
        settings.database_url_safe,
        settings.dry_run,
        settings.trading_enabled,
    )
    store = StateStore(settings.database_url)
    worker = SignalWorker(settings, store, logger)
    worker.run_forever()


if __name__ == "__main__":
    main()
