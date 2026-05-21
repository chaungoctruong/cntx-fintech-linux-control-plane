from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .models import ExecutionResult


@dataclass(slots=True)
class Mt5OrderRequest:
    signal_id: int
    side: str
    symbol: str
    entry: float
    sl: float
    tp: float
    volume: float
    config_key: str
    entry_type: str = "market"


class Mt5Executor:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        if not left or not right:
            return False
        left_path = Path(left).resolve()
        right_path = Path(right).resolve()
        if left_path.name.casefold() == "terminal64.exe":
            left_path = left_path.parent
        if right_path.name.casefold() == "terminal64.exe":
            right_path = right_path.parent
        return str(left_path).casefold() == str(right_path).casefold()

    def execute(self, req: Mt5OrderRequest) -> ExecutionResult:
        if self.settings.dry_run or not self.settings.trading_enabled:
            dry_reason = "DRY_RUN_MODE" if self.settings.dry_run else "TRADING_DISABLED"
            return ExecutionResult(
                signal_id=req.signal_id,
                status="dry_run",
                mt5_ticket=f"DRY-{req.signal_id}",
                side=req.side,
                volume=req.volume,
                symbol=req.symbol,
                requested_entry=req.entry,
                executed_price=req.entry,
                sl=req.sl,
                tp=req.tp,
                mt5_retcode="DRY_RUN",
                error=dry_reason,
            )

        return self._execute_live(req)

    def _execute_live(self, req: Mt5OrderRequest) -> ExecutionResult:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except Exception as exc:
            return ExecutionResult(
                signal_id=req.signal_id,
                status="failed",
                side=req.side,
                volume=req.volume,
                symbol=req.symbol,
                requested_entry=req.entry,
                sl=req.sl,
                tp=req.tp,
                mt5_retcode="MT5_IMPORT_ERROR",
                error=str(exc),
            )

        if not mt5.initialize(path=self.settings.mt5_terminal_path):
            return ExecutionResult(
                signal_id=req.signal_id,
                status="failed",
                side=req.side,
                volume=req.volume,
                symbol=req.symbol,
                requested_entry=req.entry,
                sl=req.sl,
                tp=req.tp,
                mt5_retcode="MT5_INIT_FAILED",
                error=str(mt5.last_error()),
            )

        send_last_error = ""
        check_retcode: str | None = None
        check_comment = ""
        try:
            terminal = mt5.terminal_info()
            attached_path = str(getattr(terminal, "path", "") or "")
            if not self._same_path(attached_path, self.settings.mt5_terminal_path):
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="MT5_PATH_MISMATCH",
                    error=f"attached_path={attached_path}",
                )

            if not mt5.login(
                login=self.settings.mt5_login,
                password=self.settings.mt5_password,
                server=self.settings.mt5_server,
            ):
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="MT5_LOGIN_FAILED",
                    error=str(mt5.last_error()),
                )

            account = mt5.account_info()
            if account is None:
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="MT5_ACCOUNT_INFO_FAILED",
                    error=str(mt5.last_error()),
                )
            if int(getattr(account, "login", 0) or 0) != int(self.settings.mt5_login):
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="MT5_LOGIN_MISMATCH",
                    error=f"attached_login={getattr(account, 'login', '')}",
                )
            attached_server = str(getattr(account, "server", "") or "")
            if attached_server != self.settings.mt5_server:
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="MT5_SERVER_MISMATCH",
                    error=f"attached_server={attached_server}",
                )

            symbol = mt5.symbol_info(req.symbol)
            if symbol is None:
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="SYMBOL_NOT_FOUND",
                    error="symbol_not_found",
                )

            if not symbol.visible and not mt5.symbol_select(req.symbol, True):
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="SYMBOL_SELECT_FAILED",
                    error="symbol_select_failed",
                )

            tick = mt5.symbol_info_tick(req.symbol)
            if tick is None:
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="TICK_UNAVAILABLE",
                    error="tick_unavailable",
                )

            entry_type = str(req.entry_type or "market").strip().lower()
            is_limit = entry_type in {"limit", "pending_limit", "buy_limit", "sell_limit"}
            if is_limit:
                order_type = mt5.ORDER_TYPE_BUY_LIMIT if req.side == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
                price = float(req.entry)
            else:
                order_type = mt5.ORDER_TYPE_BUY if req.side == "BUY" else mt5.ORDER_TYPE_SELL
                price = tick.ask if req.side == "BUY" else tick.bid
            point = float(getattr(symbol, "point", 0.0) or 0.0)
            if point <= 0:
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="POINT_INVALID",
                    error="symbol_point_invalid",
                )
            spread_points = abs(float(tick.ask) - float(tick.bid)) / point
            if self.settings.max_spread_points > 0 and spread_points > self.settings.max_spread_points:
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="SPREAD_TOO_HIGH",
                    error=f"spread_points={spread_points:.2f}",
                )
            drift_points = abs(float(price) - float(req.entry)) / point
            if not is_limit and self.settings.max_entry_drift_points > 0 and drift_points > self.settings.max_entry_drift_points:
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="ENTRY_DRIFT_TOO_LARGE",
                    error=f"drift_points={drift_points:.2f}",
                )
            digits = int(symbol.digits)
            comment = f"gsalgo_{req.signal_id}"
            request_data: dict[str, Any] = {
                "action": mt5.TRADE_ACTION_PENDING if is_limit else mt5.TRADE_ACTION_DEAL,
                "symbol": req.symbol,
                "volume": float(req.volume),
                "type": order_type,
                "price": round(price, digits),
                "sl": round(req.sl, digits),
                "tp": round(req.tp, digits),
                "deviation": int(self.settings.max_slippage_points),
                "magic": int(self.settings.mt5_magic),
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
            }
            if not is_limit:
                request_data["type_filling"] = mt5.ORDER_FILLING_IOC
            check = mt5.order_check(request_data)
            if check is None:
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode="ORDER_CHECK_NONE",
                    error=f"order_check_none last_error={mt5.last_error()}",
                )
            check_retcode = str(getattr(check, "retcode", ""))
            check_comment = str(getattr(check, "comment", ""))
            if int(getattr(check, "retcode", -1)) != 0:
                return ExecutionResult(
                    signal_id=req.signal_id,
                    status="failed",
                    side=req.side,
                    volume=req.volume,
                    symbol=req.symbol,
                    requested_entry=req.entry,
                    sl=req.sl,
                    tp=req.tp,
                    mt5_retcode=f"ORDER_CHECK_{check_retcode}",
                    error=check_comment,
                )
            result = mt5.order_send(request_data)
            send_last_error = str(mt5.last_error())
        finally:
            mt5.shutdown()
        if result is None:
            return ExecutionResult(
                signal_id=req.signal_id,
                status="failed",
                side=req.side,
                volume=req.volume,
                symbol=req.symbol,
                requested_entry=req.entry,
                sl=req.sl,
                tp=req.tp,
                mt5_retcode="ORDER_SEND_NONE",
                error=(
                    "order_send_none "
                    f"last_error={send_last_error} "
                    f"check_retcode={check_retcode} check_comment={check_comment}"
                ),
            )

        retcode = str(getattr(result, "retcode", ""))
        ok_retcodes = {
            int(getattr(mt5, "TRADE_RETCODE_DONE", 10009)),
            int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008)),
        }
        is_ok = int(getattr(result, "retcode", -1)) in ok_retcodes
        return ExecutionResult(
            signal_id=req.signal_id,
            status="executed" if is_ok else "failed",
            mt5_ticket=str(getattr(result, "order", "")),
            side=req.side,
            volume=req.volume,
            symbol=req.symbol,
            requested_entry=req.entry,
            executed_price=float(getattr(result, "price", 0.0) or 0.0),
            sl=req.sl,
            tp=req.tp,
            mt5_retcode=retcode,
            error="" if is_ok else str(getattr(result, "comment", "")),
        )
