#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.settings import settings  # noqa: E402
from scripts.setup_tradingview_signal import (  # noqa: E402
    _base_url,
    _connect,
    _dispatchable_rows,
    _json_dump,
    _signal_rows,
)


def _kind_for_action(action: str) -> str:
    return "CLOSE_ORDER" if action.upper() == "CLOSE" else "PLACE_ORDER"


def _side_for_action(action: str) -> str:
    raw = action.upper()
    if raw in {"BUY", "LONG"}:
        return "buy"
    if raw in {"SELL", "SHORT"}:
        return "sell"
    return ""


def _stable_order_magic(*, account_id: int, deployment_id: int, bot_code: str) -> int:
    seed = f"{int(account_id)}:{int(deployment_id)}:{str(bot_code or '').strip()}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    return int.from_bytes(digest[:4], "big") % 2_000_000_000


def _webhook_url(base_url: str) -> str:
    return f"{_base_url(base_url)}/api/v2/public/tradingview/broadcast"


def _get_json(url: str, *, timeout_sec: float) -> tuple[bool, int | None, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, int(exc.code), body
    except Exception as exc:
        return False, None, str(exc)
    try:
        parsed: Any = json.loads(body)
    except Exception:
        parsed = body
    return 200 <= status < 300, status, parsed


def _post_json(url: str, payload: dict[str, Any], *, timeout_sec: float) -> tuple[bool, int | None, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, int(exc.code), body
    except Exception as exc:
        return False, None, str(exc)
    try:
        parsed: Any = json.loads(body)
    except Exception:
        parsed = body
    return 200 <= status < 300, status, parsed


def _latest_deployments(cur, *, bot_code: str, limit: int) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if bot_code:
        where = "WHERE bot_code = %s"
        params.append(bot_code)
    params.append(limit)
    cur.execute(
        f"""
        SELECT id, account_id, bot_code, status, desired_state, is_active,
               runner_id, slot_id, last_error, updated_at
        FROM bot_deployments
        {where}
        ORDER BY updated_at DESC, id DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return [dict(row) for row in cur.fetchall() or []]


def _hypothetical_dispatchable_rows(cur, *, signal_id: str, bot_code: str, limit: int) -> list[dict[str, Any]]:
    """Latest subscriber deployments, without requiring running/active status.

    This mirrors the real broadcast lookup but deliberately relaxes deployment
    state so operators can answer: "if this MT5 deployment were running, would
    the signal fan out, and what payload would it create?"
    """
    cur.execute(
        """
        SELECT
          s.id              AS subscription_id,
          s.account_id      AS account_id,
          s.signal_id       AS signal_id,
          s.bot_code        AS subscription_bot_code,
          s.volume_override AS volume_override,
          s.priority        AS subscription_priority,
          s.enabled         AS subscription_enabled,
          ba.broker         AS broker,
          ba.server         AS server,
          ba.login          AS login,
          ba.user_id        AS user_id,
          ba.is_active      AS account_active,
          d.id              AS deployment_id,
          d.bot_code        AS bot_code,
          d.status          AS deployment_status,
          d.desired_state   AS desired_state,
          d.is_active       AS deployment_active,
          d.runner_id       AS runner_id,
          d.slot_id         AS slot_id,
          d.config_json     AS deployment_config_json,
          d.last_error      AS deployment_last_error,
          d.updated_at      AS deployment_updated_at
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
          AND (%s = '' OR d.bot_code = %s)
        ORDER BY s.priority DESC, s.id ASC
        LIMIT %s
        """,
        (signal_id, bot_code, bot_code, limit),
    )
    return [dict(row) for row in cur.fetchall() or []]


def _latest_commands(cur, *, signal_id: str, alert_id: str, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, command_id, command_type, account_id, deployment_id, bot_id,
               runner_id, slot_id, delivery_status, trace_id, last_error,
               payload_json, created_at, updated_at
        FROM execution_commands
        WHERE (%s = '' OR payload_json->>'broadcast_signal_id' = %s)
          AND (%s = '' OR payload_json->>'broadcast_alert_id' = %s OR trace_id LIKE %s)
        ORDER BY id DESC
        LIMIT %s
        """,
        (signal_id, signal_id, alert_id, alert_id, f"tv_bcast:{alert_id}:%", limit),
    )
    return [dict(row) for row in cur.fetchall() or []]


def _runner_rows(cur, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runner_ids = sorted(
        {
            str(row.get("runner_id") or "").strip()
            for row in rows
            if str(row.get("runner_id") or "").strip()
        }
    )
    if not runner_ids:
        return []
    cur.execute(
        """
        SELECT runner_id, status, last_heartbeat_at, metadata_json
        FROM runner_nodes
        WHERE runner_id = ANY(%s)
        ORDER BY runner_id
        """,
        (runner_ids,),
    )
    return [dict(row) for row in cur.fetchall() or []]


def _build_payload(args: argparse.Namespace, *, alert_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "secret": str(args.secret or settings.TRADINGVIEW_WEBHOOK_SECRET or "").strip(),
        "signal_id": args.signal_id,
        "bot_code": args.bot_code,
        "action": args.action,
        "symbol": args.symbol,
        "alert_id": alert_id,
    }
    if args.volume is not None and args.action != "CLOSE":
        payload["default_volume"] = args.volume
    return {k: v for k, v in payload.items() if v not in ("", None)}


def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    if redacted.get("secret"):
        redacted["secret"] = "<configured>"
    return redacted


def _deployment_lot(row: dict[str, Any]) -> float:
    raw = row.get("deployment_config_json") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    trading = (raw or {}).get("trading") if isinstance(raw, dict) else {}
    if not isinstance(trading, dict):
        trading = {}
    try:
        return float(trading.get("lot_size") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _resolve_volume(args: argparse.Namespace, row: dict[str, Any]) -> tuple[float, str]:
    if row.get("volume_override") is not None:
        return float(row["volume_override"]), "subscription.volume_override"
    if args.volume is not None:
        return float(args.volume), "test.default_volume"
    return _deployment_lot(row), "deployment.config_json.trading.lot_size"


def _simulate_running_fanout(args: argparse.Namespace, rows: list[dict[str, Any]], *, alert_id: str) -> dict[str, Any]:
    kind = _kind_for_action(args.action)
    side = _side_for_action(args.action)
    items: list[dict[str, Any]] = []
    would_dispatch = 0
    would_fail = 0

    for row in rows:
        errors: list[str] = []
        if not bool(row.get("subscription_enabled")):
            errors.append("subscription_disabled")
        if not bool(row.get("account_active")):
            errors.append("account_inactive")
        if not row.get("deployment_id"):
            errors.append("deployment_missing")
        if not row.get("runner_id"):
            errors.append("runner_id_missing")
        if not row.get("slot_id"):
            errors.append("slot_id_missing")

        volume = 0.0
        volume_source = ""
        if kind == "PLACE_ORDER":
            volume, volume_source = _resolve_volume(args, row)
            if volume <= 0:
                errors.append("no_volume_resolved")

        account_id = int(row["account_id"]) if row.get("account_id") is not None else 0
        deployment_id = int(row["deployment_id"]) if row.get("deployment_id") is not None else 0
        bot_code = str(row.get("bot_code") or row.get("subscription_bot_code") or "")
        magic = _stable_order_magic(account_id=account_id, deployment_id=deployment_id, bot_code=bot_code) if deployment_id else None

        request: dict[str, Any]
        if kind == "PLACE_ORDER":
            request = {"symbol": args.symbol, "side": side, "volume": volume, "magic": magic}
        else:
            request = {"close_kind": "CLOSE", "magic": magic}
            if args.symbol:
                request["symbol"] = args.symbol

        trace_id = f"tv_bcast:{alert_id}:{account_id}:{kind.lower()}" if account_id else ""
        item = {
            "would_dispatch": not errors,
            "errors": errors,
            "account_id": row.get("account_id"),
            "subscription_id": row.get("subscription_id"),
            "broker": row.get("broker"),
            "server": row.get("server"),
            "login": row.get("login"),
            "deployment_id": row.get("deployment_id"),
            "bot_code": bot_code,
            "current_deployment_status": row.get("deployment_status"),
            "current_desired_state": row.get("desired_state"),
            "current_deployment_active": row.get("deployment_active"),
            "runner_id": row.get("runner_id"),
            "slot_id": row.get("slot_id"),
            "command_type": kind,
            "trace_id": trace_id,
            "priority": int(row.get("subscription_priority") or 60),
            "payload": {
                "request": request,
                "broadcast_signal_id": args.signal_id,
                "broadcast_alert_id": alert_id,
                "broadcast_bot_code": args.bot_code,
            },
        }
        if kind == "PLACE_ORDER":
            item["volume_source"] = volume_source
        if errors:
            would_fail += 1
        else:
            would_dispatch += 1
        items.append(item)

    return {
        "enabled": True,
        "note": "Simulation only: DB is not changed, webhook is not called, Redis is not touched.",
        "assumption": "Treat latest deployment rows as if status=running and is_active=true.",
        "would_dispatch": would_dispatch,
        "would_fail": would_fail,
        "items": items,
    }


def cmd_smoke(args: argparse.Namespace) -> int:
    alert_id = args.alert_id or f"manual-smoke-{int(time.time())}-{args.action.lower()}"
    url = _webhook_url(args.base_url)
    ready_url = f"{_base_url(args.base_url)}/ready"
    payload = _build_payload(args, alert_id=alert_id)

    ready_ok, ready_status, ready_body = _get_json(ready_url, timeout_sec=args.timeout_sec)

    with _connect() as conn:
        with conn.cursor() as cur:
            subscriptions = _signal_rows(cur, args.signal_id)
            dispatchable = _dispatchable_rows(cur, args.signal_id)
            hypothetical_rows = (
                _hypothetical_dispatchable_rows(
                    cur,
                    signal_id=args.signal_id,
                    bot_code=args.bot_code,
                    limit=args.limit,
                )
                if args.simulate_running
                else []
            )
            runners = _runner_rows(cur, subscriptions + dispatchable)
            deployments = _latest_deployments(cur, bot_code=args.bot_code, limit=args.limit)
            commands_before = _latest_commands(
                cur,
                signal_id=args.signal_id,
                alert_id="" if not args.alert_id else alert_id,
                limit=args.limit,
            )

    result: dict[str, Any] = {
        "ok": True,
        "sent": False,
        "base_url": _base_url(args.base_url),
        "webhook_url": url,
        "ready": {"ok": ready_ok, "status": ready_status, "response": ready_body},
        "signal_id": args.signal_id,
        "bot_code": args.bot_code,
        "action": args.action,
        "kind": _kind_for_action(args.action),
        "alert_id": alert_id,
        "secret_configured": bool(payload.get("secret")),
        "payload": _redact_payload(payload),
        "subscriptions_total": len(subscriptions),
        "dispatchable_subscribers": len(dispatchable),
        "ready_for_live_signal": bool(dispatchable),
        "subscriptions": subscriptions,
        "dispatchable": dispatchable,
        "runners": runners,
        "latest_deployments": deployments,
        "latest_matching_commands_before": commands_before,
    }

    if args.simulate_running:
        result["simulation"] = _simulate_running_fanout(args, hypothetical_rows, alert_id=alert_id)
        if args.send:
            result["ok"] = False
            result["error"] = "simulate_running_cannot_send"
            result["hint"] = "--simulate-running is dry-run only."
            print(_json_dump(result))
            return 2

    if args.require_ready and not dispatchable:
        result["ok"] = False
        result["error"] = "signal_not_dispatchable"
        print(_json_dump(result))
        return 2

    if not args.send:
        result["dry_run"] = True
        result["hint"] = "Add --send --confirm-live-order-risk to POST this test signal."
        print(_json_dump(result))
        return 0

    if not args.confirm_live_order_risk:
        result["ok"] = False
        result["error"] = "missing_confirm_live_order_risk"
        result["hint"] = "This can create a live PLACE_ORDER if subscribers are dispatchable."
        print(_json_dump(result))
        return 2

    post_ok, post_status, post_body = _post_json(url, payload, timeout_sec=args.timeout_sec)
    result["sent"] = True
    result["webhook_response"] = {"ok": post_ok, "status": post_status, "response": post_body}

    if args.poll_sec > 0:
        time.sleep(args.poll_sec)

    with _connect() as conn:
        with conn.cursor() as cur:
            result["latest_matching_commands_after"] = _latest_commands(
                cur,
                signal_id=args.signal_id,
                alert_id=alert_id,
                limit=args.limit,
            )

    if not post_ok:
        result["ok"] = False
        print(_json_dump(result))
        return 1

    response_dict = post_body if isinstance(post_body, dict) else {}
    dispatched = int(response_dict.get("dispatched") or 0)
    if args.expect_dispatch and dispatched <= 0:
        result["ok"] = False
        result["error"] = "webhook_accepted_but_no_dispatch"
        print(_json_dump(result))
        return 3

    print(_json_dump(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test the TradingView broadcast webhook. Defaults to dry-run; "
            "sending may create live orders when deployments are running."
        )
    )
    parser.add_argument("--signal-id", default="gsalgovip-xauusd")
    parser.add_argument("--bot-code", default="gsalgovip")
    parser.add_argument("--action", choices=["BUY", "SELL", "CLOSE"], default="BUY")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument(
        "--volume",
        type=float,
        default=None,
        help="Optional fallback lot. Omit to use the Mini App/deployment lot.",
    )
    parser.add_argument("--alert-id", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--secret", default="", help="Override TRADINGVIEW_WEBHOOK_SECRET.")
    parser.add_argument("--timeout-sec", type=float, default=15.0)
    parser.add_argument("--poll-sec", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--require-ready", action="store_true", help="Exit non-zero if there is no dispatchable subscriber.")
    parser.add_argument("--expect-dispatch", action="store_true", help="After --send, require dispatched > 0.")
    parser.add_argument(
        "--simulate-running",
        action="store_true",
        help="Dry-run what would dispatch if the latest matching deployment were running/active.",
    )
    parser.add_argument("--send", action="store_true", help="Actually POST the test signal.")
    parser.add_argument(
        "--confirm-live-order-risk",
        action="store_true",
        help="Required with --send because a running product deployment can place a live order.",
    )
    parser.set_defaults(func=cmd_smoke)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
