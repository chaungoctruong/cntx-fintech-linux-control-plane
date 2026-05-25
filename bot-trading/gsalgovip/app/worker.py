from __future__ import annotations

import time
from logging import Logger

from .config import Settings
from .models import ExecutionResult
from .mt5_executor import Mt5Executor, Mt5OrderRequest
from .state_store import StateStore
from .telegram_notify import TelegramNotifier


class SignalWorker:
    def __init__(self, settings: Settings, store: StateStore, logger: Logger):
        self.settings = settings
        self.store = store
        self.logger = logger
        self.executor = Mt5Executor(settings)
        self.notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)

    def run_forever(self) -> None:
        self.logger.info("worker_started trading_enabled=%s dry_run=%s", self.settings.trading_enabled, self.settings.dry_run)
        while True:
            self._tick_once()
            time.sleep(self.settings.poll_interval_sec)

    def _tick_once(self) -> None:
        signal = self.store.claim_pending_signal()
        if not signal:
            return

        signal_id = int(signal["id"])
        if self.store.has_execution(signal_id):
            self.store.mark_signal_status(signal_id, "duplicate_ignored", "execution_exists")
            return

        request = Mt5OrderRequest(
            signal_id=signal_id,
            side=str(signal["side"]),
            symbol=self.settings.symbol_map.get(str(signal["symbol"]).upper(), str(signal["symbol"])),
            entry=float(signal["entry"]),
            sl=float(signal["sl"]),
            tp=float(signal["tp"]),
            volume=self.settings.default_volume,
            config_key=str(signal["config_key"]),
        )
        self.logger.info("signal_dispatch signal_id=%s side=%s symbol=%s", signal_id, request.side, request.symbol)
        result = self.executor.execute(request)
        self._apply_result(result)

    def _apply_result(self, result: ExecutionResult) -> None:
        self.store.insert_execution(result)
        if result.status == "dry_run":
            self.store.mark_signal_status(result.signal_id, "dry_run", result.error)
            self.logger.info("signal_dry_run signal_id=%s ticket=%s", result.signal_id, result.mt5_ticket)
            self._notify_safe(
                f"GsAlgo signal {result.signal_id} dry_run {result.side} {result.symbol} "
                f"ticket={result.mt5_ticket} retcode={result.mt5_retcode}"
            )
            return
        if result.status == "executed":
            self.store.mark_signal_status(result.signal_id, "executed", result.error)
            self.logger.info("signal_executed signal_id=%s ticket=%s status=%s", result.signal_id, result.mt5_ticket, result.status)
            self._notify_safe(
                f"GsAlgo signal {result.signal_id} {result.status} {result.side} {result.symbol} "
                f"ticket={result.mt5_ticket} retcode={result.mt5_retcode}"
            )
            return
        self.store.mark_signal_status(result.signal_id, "failed", result.error)
        self.logger.error("signal_failed signal_id=%s error=%s retcode=%s", result.signal_id, result.error, result.mt5_retcode)
        self._notify_safe(
            f"GsAlgo signal {result.signal_id} failed {result.side} {result.symbol} "
            f"error={result.error} retcode={result.mt5_retcode}"
        )

    def _notify_safe(self, message: str) -> None:
        try:
            self.notifier.send(message)
        except Exception as exc:
            self.logger.warning("telegram_notify_failed error=%s", exc)
