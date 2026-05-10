#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.settings import settings  # noqa: E402


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _connect():
    return psycopg2.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        application_name="spider-tradingview-signal-setup",
        cursor_factory=RealDictCursor,
    )


def _load_metadata(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("metadata_json_must_be_object")
    return payload


def _base_url(value: str) -> str:
    raw = str(value or "").strip() or str(settings.PUBLIC_BASE_URL or settings.BACKEND_URL or "").strip()
    if not raw:
        raw = "http://127.0.0.1:8001"
    return raw.rstrip("/")


def _signal_rows(cur, signal_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            s.id AS subscription_id,
            s.account_id,
            s.signal_id,
            s.bot_code,
            s.volume_override,
            s.priority,
            s.enabled,
            ba.login,
            ba.broker,
            ba.server,
            ba.is_active AS account_active,
            d.id AS deployment_id,
            d.bot_code,
            d.status AS deployment_status,
            d.desired_state,
            d.is_active AS deployment_active,
            d.runner_id,
            d.slot_id,
            d.updated_at AS deployment_updated_at
        FROM tradingview_signal_subscriptions s
        JOIN broker_accounts ba ON ba.id = s.account_id
        LEFT JOIN LATERAL (
            SELECT *
            FROM bot_deployments d
            WHERE d.account_id = s.account_id
              AND (COALESCE(s.bot_code, '') = '' OR d.bot_code = s.bot_code)
            ORDER BY d.updated_at DESC, d.id DESC
            LIMIT 1
        ) d ON TRUE
        WHERE s.signal_id = %s
        ORDER BY s.priority DESC, s.id ASC
        """,
        (signal_id,),
    )
    return [dict(row) for row in cur.fetchall() or []]


def _dispatchable_rows(cur, signal_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            s.id AS subscription_id,
            s.account_id,
            s.bot_code AS subscription_bot_code,
            d.id AS deployment_id,
            d.bot_code,
            d.runner_id,
            d.slot_id,
            s.volume_override,
            s.priority
        FROM tradingview_signal_subscriptions s
        JOIN broker_accounts ba ON ba.id = s.account_id
        JOIN bot_deployments d ON d.account_id = s.account_id
        WHERE s.signal_id = %s
          AND s.enabled = TRUE
          AND ba.is_active = TRUE
          AND d.status = 'running'
          AND d.is_active = TRUE
          AND d.runner_id IS NOT NULL
          AND d.slot_id IS NOT NULL
          AND (COALESCE(s.bot_code, '') = '' OR d.bot_code = s.bot_code)
        ORDER BY s.priority DESC, s.id ASC
        """,
        (signal_id,),
    )
    return [dict(row) for row in cur.fetchall() or []]


def cmd_subscribe(args: argparse.Namespace) -> int:
    metadata = _load_metadata(args.metadata_json)
    enabled = not bool(args.disabled)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, broker, server, login, is_active
                FROM broker_accounts
                WHERE id = %s
                """,
                (args.account_id,),
            )
            account = cur.fetchone()
            if not account:
                print(_json_dump({"ok": False, "error": "account_not_found", "account_id": args.account_id}))
                return 2
            if not bool(account["is_active"]) and not args.allow_inactive_account:
                print(_json_dump({"ok": False, "error": "account_inactive", "account": dict(account)}))
                return 2

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
                          created_at, updated_at
                """,
                (
                    args.account_id,
                    args.signal_id,
                    str(args.bot_code or "").strip() or None,
                    args.volume,
                    args.priority,
                    enabled,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            subscription = dict(cur.fetchone() or {})
            dispatchable = _dispatchable_rows(cur, args.signal_id)
        conn.commit()

    print(
        _json_dump(
            {
                "ok": True,
                "subscription": subscription,
                "account": dict(account),
                "dispatchable_subscribers": len(dispatchable),
                "ready_for_live_signal": bool(dispatchable),
                "hint": "Bot deployment must be status=running before TradingView fan-out sends orders.",
            }
        )
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            subscriptions = _signal_rows(cur, args.signal_id)
            dispatchable = _dispatchable_rows(cur, args.signal_id)
            runner_ids = sorted(
                {
                    str(row.get("runner_id") or "").strip()
                    for row in subscriptions + dispatchable
                    if str(row.get("runner_id") or "").strip()
                }
            )
            runners: list[dict[str, Any]] = []
            if runner_ids:
                cur.execute(
                    """
                    SELECT runner_id, status, last_heartbeat_at, metadata_json
                    FROM runner_nodes
                    WHERE runner_id = ANY(%s)
                    ORDER BY runner_id
                    """,
                    (runner_ids,),
                )
                runners = [dict(row) for row in cur.fetchall() or []]

    print(
        _json_dump(
            {
                "ok": True,
                "signal_id": args.signal_id,
                "subscriptions_total": len(subscriptions),
                "dispatchable_subscribers": len(dispatchable),
                "ready_for_live_signal": bool(dispatchable),
                "subscriptions": subscriptions,
                "dispatchable": dispatchable,
                "runners": runners,
                "requirements": [
                    "subscription.enabled=true",
                    "broker_accounts.is_active=true",
                    "bot_deployments.status=running",
                    "bot_deployments.is_active=true",
                    "runner_id and slot_id are assigned",
                ],
            }
        )
    )
    return 0


def _alert_payload(
    *,
    secret: str,
    signal_id: str,
    bot_code: str,
    action: str,
    symbol: str,
    volume: float | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "secret": secret or "<TRADINGVIEW_WEBHOOK_SECRET>",
        "signal_id": signal_id,
        "action": action,
        "symbol": symbol,
    }
    if bot_code:
        payload["bot_code"] = bot_code
    if action != "CLOSE" and volume is not None:
        payload["default_volume"] = volume
    return payload


def cmd_alert_json(args: argparse.Namespace) -> int:
    secret = str(settings.TRADINGVIEW_WEBHOOK_SECRET or "").strip() if args.include_secret else ""
    symbol = str(args.symbol or "{{ticker}}").strip()
    result = {
        "webhook_url": f"{_base_url(args.base_url)}/api/v2/public/tradingview/broadcast",
        "alerts": {
            "BUY": _alert_payload(
                secret=secret,
                signal_id=args.signal_id,
                bot_code=args.bot_code,
                action="BUY",
                symbol=symbol,
                volume=args.volume,
            ),
            "SELL": _alert_payload(
                secret=secret,
                signal_id=args.signal_id,
                bot_code=args.bot_code,
                action="SELL",
                symbol=symbol,
                volume=args.volume,
            ),
            "CLOSE": _alert_payload(
                secret=secret,
                signal_id=args.signal_id,
                bot_code=args.bot_code,
                action="CLOSE",
                symbol=symbol,
                volume=None,
            ),
        },
        "note": "Create 3 TradingView alerts with the same webhook_url and paste one JSON message per alert.",
    }
    print(_json_dump(result))
    return 0


def cmd_test_broadcast(args: argparse.Namespace) -> int:
    secret = str(args.secret or settings.TRADINGVIEW_WEBHOOK_SECRET or "").strip()
    payload = {
        "alert_id": args.alert_id or f"manual-{int(time.time())}-{args.action.lower()}",
        "signal_id": args.signal_id,
        "action": args.action,
        "symbol": args.symbol,
    }
    if args.bot_code:
        payload["bot_code"] = args.bot_code
    if secret:
        payload["secret"] = secret
    if args.action != "CLOSE":
        payload["default_volume"] = args.volume

    url = f"{_base_url(args.base_url)}/api/v2/public/tradingview/broadcast"
    if not args.send:
        print(_json_dump({"ok": True, "dry_run": True, "url": url, "payload": payload, "hint": "Add --send to POST it."}))
        return 0

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout_sec) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(_json_dump({"ok": False, "status": exc.code, "response": body, "url": url, "payload": payload}))
        return 1
    except Exception as exc:
        print(_json_dump({"ok": False, "error": str(exc), "url": url, "payload": payload}))
        return 1

    try:
        parsed: Any = json.loads(body)
    except Exception:
        parsed = body
    print(_json_dump({"ok": 200 <= status < 300, "status": status, "response": parsed, "url": url}))
    return 0 if 200 <= status < 300 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set up TradingView signal subscriptions and generate webhook payloads."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    subscribe = sub.add_parser("subscribe", help="Upsert account -> signal_id subscription.")
    subscribe.add_argument("--account-id", type=int, required=True)
    subscribe.add_argument("--signal-id", required=True)
    subscribe.add_argument("--bot-code", default="", help="Optional bot code guard, e.g. gsalgovip.")
    subscribe.add_argument("--volume", type=float, default=None, help="Per-account lot override. Omit to use alert/deployment volume.")
    subscribe.add_argument("--priority", type=int, default=60)
    subscribe.add_argument("--disabled", action="store_true")
    subscribe.add_argument("--allow-inactive-account", action="store_true")
    subscribe.add_argument("--metadata-json", default="")
    subscribe.set_defaults(func=cmd_subscribe)

    doctor = sub.add_parser("doctor", help="Show whether a signal_id can dispatch orders now.")
    doctor.add_argument("--signal-id", required=True)
    doctor.set_defaults(func=cmd_doctor)

    alert_json = sub.add_parser("alert-json", help="Print 3 TradingView JSON messages: BUY/SELL/CLOSE.")
    alert_json.add_argument("--signal-id", required=True)
    alert_json.add_argument("--bot-code", default="", help="Optional bot code to include in TradingView JSON.")
    alert_json.add_argument("--symbol", default="{{ticker}}")
    alert_json.add_argument(
        "--volume",
        type=float,
        default=None,
        help="Optional fallback lot in TradingView JSON. Omit to use the Mini App deployment lot.",
    )
    alert_json.add_argument("--base-url", default="")
    alert_json.add_argument("--include-secret", action="store_true", help="Print the configured webhook secret into the JSON.")
    alert_json.set_defaults(func=cmd_alert_json)

    test_broadcast = sub.add_parser("test-broadcast", help="Dry-run or send one broadcast request to the backend.")
    test_broadcast.add_argument("--signal-id", required=True)
    test_broadcast.add_argument("--bot-code", default="")
    test_broadcast.add_argument("--action", choices=["BUY", "SELL", "CLOSE"], required=True)
    test_broadcast.add_argument("--symbol", required=True)
    test_broadcast.add_argument(
        "--volume",
        type=float,
        default=None,
        help="Optional fallback lot. Omit to use the Mini App deployment lot.",
    )
    test_broadcast.add_argument("--alert-id", default="")
    test_broadcast.add_argument("--base-url", default="")
    test_broadcast.add_argument("--secret", default="")
    test_broadcast.add_argument("--timeout-sec", type=float, default=15.0)
    test_broadcast.add_argument("--send", action="store_true", help="Actually POST to webhook endpoint.")
    test_broadcast.set_defaults(func=cmd_test_broadcast)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
