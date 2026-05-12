"""Repository methods for TradingView signal fan-out."""
from __future__ import annotations

from typing import Any

from app.repositories.control_plane.query_loader import load_sql


class ControlPlaneTradingViewMixin:
    _SQL_LIST_SUBSCRIBERS_FOR_SIGNAL = load_sql("tradingview/list_subscribers_for_signal.sql")

    def list_subscribers_for_signal(
        self,
        *,
        signal_id: str,
        bot_code: str = "",
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Return all enabled subscribers for `signal_id` whose deployment is running.

        Used by `/api/v2/public/tradingview/broadcast` to fan-out 1 alert to N
        accounts in a single batch dispatch. Caps at `limit` to avoid runaway
        broadcast — adjust via SETTINGS if needed.
        """
        signal_s = str(signal_id or "").strip()
        bot_code_s = str(bot_code or "").strip()
        cap = max(1, min(int(limit or 5000), 50000))
        if not signal_s:
            return []

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(self._SQL_LIST_SUBSCRIBERS_FOR_SIGNAL, (signal_s, bot_code_s, bot_code_s, cap))
            rows = cur.fetchall() or []
            return [dict(r) for r in rows]

        return self._store._with_retry_read(_do)

    def upsert_signal_subscription(
        self,
        *,
        account_id: int,
        signal_id: str,
        bot_code: str = "",
        volume_override: float | None = None,
        priority: int = 50,
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Idempotent upsert helper — convenient for admin scripts seeding subs."""
        signal_s = str(signal_id or "").strip()
        bot_code_s = str(bot_code or "").strip() or None
        if not signal_s:
            raise ValueError("signal_id_required")
        meta_json = metadata or {}

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                INSERT INTO tradingview_signal_subscriptions
                    (account_id, signal_id, bot_code, volume_override, priority, enabled, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (account_id, signal_id) DO UPDATE SET
                    bot_code = EXCLUDED.bot_code,
                    volume_override = EXCLUDED.volume_override,
                    priority = EXCLUDED.priority,
                    enabled = EXCLUDED.enabled,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = NOW()
                RETURNING id, account_id, signal_id, bot_code, volume_override, priority, enabled, metadata_json,
                          created_at, updated_at;
                """,
                (
                    int(account_id),
                    signal_s,
                    bot_code_s,
                    float(volume_override) if volume_override is not None else None,
                    int(priority),
                    bool(enabled),
                    __import__("json").dumps(meta_json, ensure_ascii=False),
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else {}

        return self._store._with_retry_write(_do)
