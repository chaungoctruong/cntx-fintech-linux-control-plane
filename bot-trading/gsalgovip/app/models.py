from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal


Side = Literal["BUY", "SELL"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class TradingViewSignal:
    source: str
    strategy: str
    strategy_version: str
    event_type: str
    side: Side
    symbol: str
    timeframe: str
    entry: float
    sl: float
    tp: float
    sl_value: float
    tp_value: float
    bar_time_ms: int
    is_confirmed: bool
    config_key: str
    nonce: str
    mode: str | None = None

    def idempotency_key(self) -> str:
        raw = "|".join(
            [
                self.strategy,
                self.strategy_version,
                self.symbol,
                self.timeframe,
                self.side,
                str(self.bar_time_ms),
                self.config_key,
            ]
        )
        return sha256(raw.encode("utf-8")).hexdigest()

    def raw_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass(slots=True)
class ExecutionResult:
    signal_id: int
    status: str
    mt5_ticket: str = ""
    side: str = ""
    volume: float = 0.0
    symbol: str = ""
    requested_entry: float = 0.0
    executed_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    mt5_retcode: str = ""
    error: str = ""
    created_at: str = ""

    def with_created_at(self) -> "ExecutionResult":
        self.created_at = self.created_at or utc_now_iso()
        return self

