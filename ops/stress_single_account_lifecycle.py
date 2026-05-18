#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


INNER_RUNNER = r'''
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.services.control_plane_service import MT5ControlPlaneService


PAYLOAD = json.loads(base64.b64decode("__PAYLOAD_B64__").decode("utf-8"))
ACTIVE_DEPLOYMENT_STATUSES = {"start_requested", "starting", "running", "stop_requested"}
LOGIN_FINAL_STATUSES = {"verified", "failed", "expired", "released", "cancelled", "claimed"}
DEPLOYMENT_FAILED_STATUSES = {"failed", "cancelled"}


class LifecycleError(RuntimeError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def _emit(event: str, **fields: Any) -> None:
    item = {"event": event, **_jsonable(fields)}
    if PAYLOAD["config"].get("json"):
        print(json.dumps(item, ensure_ascii=False, sort_keys=True), flush=True)
        return
    detail = " ".join(f"{key}={value}" for key, value in item.items() if key != "event" and value is not None)
    print(f"[single-account-lifecycle] {event}" + (f" {detail}" if detail else ""), flush=True)


def _mask_login(login: Any) -> str:
    text = str(login or "").strip()
    if len(text) <= 5:
        return "***"
    return f"{text[:3]}***{text[-2:]}"


def _repo_read(service: MT5ControlPlaneService, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
        cur.execute(query, params)
        return [dict(row) for row in (cur.fetchall() or [])]

    return service._repo._store._with_retry_read(_do)


def _resolve_account(service: MT5ControlPlaneService, cfg: dict[str, Any]) -> dict[str, Any]:
    telegram_id = str(cfg["telegram_id"]).strip()
    username = str(cfg.get("username") or "").strip() or None
    account_id = int(cfg.get("account_id") or 0)
    if account_id:
        account = service.get_account(telegram_id=telegram_id, username=username, account_id=account_id)
        if not account:
            raise LifecycleError(f"account_not_found:{account_id}")
        return dict(account)

    login = str(cfg.get("login") or "").strip()
    server = str(cfg.get("server") or "").strip()
    broker = str(cfg.get("broker") or "").strip().lower()
    if not login or not server:
        raise LifecycleError("account_id_or_login_server_required")
    matches = []
    for account in service.list_accounts(telegram_id=telegram_id, username=username):
        if str(account.get("login") or "").strip() != login:
            continue
        if str(account.get("server") or "").strip().lower() != server.lower():
            continue
        if broker and str(account.get("broker") or "").strip().lower() != broker:
            continue
        matches.append(dict(account))
    if not matches:
        raise LifecycleError("account_not_found_by_login_server")
    if len(matches) > 1:
        raise LifecycleError("multiple_accounts_match_login_server:pass --account-id")
    return matches[0]


def _active_deployments(service: MT5ControlPlaneService, account_id: int) -> list[dict[str, Any]]:
    return _repo_read(
        service,
        """
        SELECT d.id, d.status, d.health_status, d.runner_id, d.slot_id, d.account_id
        FROM bot_deployments d
        WHERE d.account_id = %s
          AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
        ORDER BY d.id
        """,
        (int(account_id),),
    )


def _active_login_reservations(service: MT5ControlPlaneService, account_id: int) -> list[dict[str, Any]]:
    return _repo_read(
        service,
        """
        SELECT id, status, runner_id, slot_id, account_id, expires_at, completed_at, last_error
        FROM account_login_reservations
        WHERE account_id = %s
          AND status IN ('pending', 'dispatched', 'verified')
        ORDER BY id
        """,
        (int(account_id),),
    )


def _slot_snapshot(service: MT5ControlPlaneService, runner_id: str, slot_id: str) -> dict[str, Any] | None:
    rows = _repo_read(
        service,
        """
        SELECT runner_id, slot_id, status, current_account_id, metadata_json, last_heartbeat_at
        FROM runner_slots
        WHERE runner_id = %s AND slot_id = %s
        LIMIT 1
        """,
        (str(runner_id), str(slot_id)),
    )
    return rows[0] if rows else None


def _runner_brief(service: MT5ControlPlaneService, runner_id: str) -> dict[str, Any]:
    health = service.get_runner_health(runner_id=runner_id) or {}
    runner = dict(health.get("runner") or {})
    queue = dict(runner.get("queue_depth") or {})
    return {
        "runner_id": runner.get("runner_id"),
        "status": runner.get("status"),
        "operational_status": runner.get("operational_status"),
        "accepts_new_work": runner.get("accepts_new_work"),
        "total_slots": runner.get("total_slots"),
        "available_slots": runner.get("available_slots"),
        "allocated_slots": runner.get("allocated_slots"),
        "running_deployments": runner.get("running_deployments"),
        "queue_commands": queue.get("commands"),
        "queue_processing": queue.get("commands_processing"),
        "heartbeat_age_sec": runner.get("heartbeat_age_sec"),
    }


async def _cleanup_account(
    *,
    service: MT5ControlPlaneService,
    telegram_id: str,
    username: str | None,
    account_id: int,
    reason: str,
    timeout_sec: int,
    poll_sec: float,
) -> dict[str, Any]:
    stopped: list[dict[str, Any]] = []
    released: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for deployment in _active_deployments(service, account_id):
        deployment_id = int(deployment["id"])
        try:
            result = await service.stop_deployment(
                telegram_id=telegram_id,
                username=username,
                deployment_id=deployment_id,
                reason=reason,
            )
            row = dict(result.get("deployment") or {})
            stopped.append(
                {
                    "deployment_id": deployment_id,
                    "from_status": deployment.get("status"),
                    "to_status": row.get("status"),
                    "runner_id": row.get("runner_id") or deployment.get("runner_id"),
                    "slot_id": row.get("slot_id") or deployment.get("slot_id"),
                }
            )
        except Exception as exc:
            errors.append({"deployment_id": deployment_id, "error": str(exc)[:240]})

    deadline = time.monotonic() + max(1, int(timeout_sec))
    while time.monotonic() < deadline:
        active = _active_deployments(service, account_id)
        if not active:
            break
        await asyncio.sleep(max(1.0, float(poll_sec)))

    for reservation in _active_login_reservations(service, account_id):
        try:
            count = service._repo.release_login_reservation(
                account_id=account_id,
                reason=reason,
            )
            released.append({"reservation_id": int(reservation["id"]), "released_count": int(count or 0)})
        except Exception as exc:
            errors.append({"reservation_id": int(reservation["id"]), "error": str(exc)[:240]})

    return {
        "stopped": stopped,
        "released": released,
        "errors": errors,
        "active_deployments": _active_deployments(service, account_id),
        "active_login_reservations": _active_login_reservations(service, account_id),
    }


async def _poll_login_slot(
    *,
    service: MT5ControlPlaneService,
    telegram_id: str,
    username: str | None,
    reservation_id: int,
    timeout_sec: int,
    poll_sec: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, int(timeout_sec))
    last_status = None
    while time.monotonic() < deadline:
        item = service.get_account_login_slot(
            telegram_id=telegram_id,
            username=username,
            reservation_id=reservation_id,
        )
        status = str((item or {}).get("status") or "").strip().lower()
        if status != last_status:
            _emit(
                "login_status",
                reservation_id=reservation_id,
                status=status,
                runner_id=(item or {}).get("runner_id"),
                slot_id=(item or {}).get("slot_id"),
                last_error=(item or {}).get("last_error"),
            )
            last_status = status
        if status == "verified":
            return dict(item or {})
        if status in LOGIN_FINAL_STATUSES and status != "verified":
            raise LifecycleError(f"login_slot_{status}:{(item or {}).get('last_error') or ''}")
        await asyncio.sleep(max(1.0, float(poll_sec)))
    raise LifecycleError(f"login_slot_timeout:{reservation_id}")


async def _poll_deployment(
    *,
    service: MT5ControlPlaneService,
    telegram_id: str,
    username: str | None,
    deployment_id: int,
    target_status: str,
    timeout_sec: int,
    poll_sec: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, int(timeout_sec))
    target = str(target_status).strip().lower()
    last_status = None
    while time.monotonic() < deadline:
        deployment = service.get_deployment(
            telegram_id=telegram_id,
            username=username,
            deployment_id=deployment_id,
        )
        if not deployment:
            raise LifecycleError(f"deployment_not_found:{deployment_id}")
        status = str(deployment.get("status") or "").strip().lower()
        health = str(deployment.get("health_status") or "").strip().lower()
        if status != last_status:
            _emit(
                "deployment_status",
                deployment_id=deployment_id,
                target=target,
                status=status,
                health_status=health,
                runner_id=deployment.get("runner_id"),
                slot_id=deployment.get("slot_id"),
                last_error=deployment.get("last_error"),
            )
            last_status = status
        if status == target:
            return dict(deployment)
        if target == "running" and status in DEPLOYMENT_FAILED_STATUSES | {"stopped"}:
            raise LifecycleError(f"deployment_start_final_{status}:{deployment.get('last_error') or ''}")
        if target == "stopped" and status in DEPLOYMENT_FAILED_STATUSES:
            raise LifecycleError(f"deployment_stop_final_{status}:{deployment.get('last_error') or ''}")
        await asyncio.sleep(max(1.0, float(poll_sec)))
    raise LifecycleError(f"deployment_{target}_timeout:{deployment_id}")


def _assert_expected_runner(*, expected_runner_id: str, phase: str, runner_id: Any, slot_id: Any) -> None:
    expected = str(expected_runner_id or "").strip()
    actual = str(runner_id or "").strip()
    if expected and actual and actual != expected:
        raise LifecycleError(f"{phase}_routed_to_unexpected_runner:expected={expected}:actual={actual}:slot={slot_id}")


async def _run_cycle(
    *,
    service: MT5ControlPlaneService,
    cfg: dict[str, Any],
    account: dict[str, Any],
    cycle: int,
) -> dict[str, Any]:
    telegram_id = str(cfg["telegram_id"]).strip()
    username = str(cfg.get("username") or "").strip() or None
    account_id = int(account["id"])
    expected_runner_id = str(cfg.get("expected_runner_id") or "").strip()
    poll_sec = float(cfg.get("poll_sec") or 3.0)
    started_at = time.monotonic()

    login_started = time.monotonic()
    login_result = await service.request_account_login_slot(
        telegram_id=telegram_id,
        username=username,
        account_id=account_id,
    )
    reservation_id = int(login_result.get("login_reservation_id") or login_result.get("id"))
    _emit(
        "login_submitted",
        cycle=cycle,
        account_id=account_id,
        reservation_id=reservation_id,
        runner_id=login_result.get("runner_id"),
        slot_id=login_result.get("slot_id"),
        status=login_result.get("status"),
    )
    _assert_expected_runner(
        expected_runner_id=expected_runner_id,
        phase="login",
        runner_id=login_result.get("runner_id"),
        slot_id=login_result.get("slot_id"),
    )
    verified = await _poll_login_slot(
        service=service,
        telegram_id=telegram_id,
        username=username,
        reservation_id=reservation_id,
        timeout_sec=int(cfg["login_timeout_sec"]),
        poll_sec=poll_sec,
    )
    _assert_expected_runner(
        expected_runner_id=expected_runner_id,
        phase="login_verified",
        runner_id=verified.get("runner_id"),
        slot_id=verified.get("slot_id"),
    )
    login_sec = time.monotonic() - login_started

    start_started = time.monotonic()
    start_result = await service.start_deployment(
        telegram_id=telegram_id,
        username=username,
        account_id=account_id,
        bot_name=str(cfg["bot_name"]),
        bot_config_overrides=dict(cfg["trading_config"]),
        mode=str(cfg["mode"]),
    )
    deployment = dict(start_result.get("deployment") or {})
    scheduler = dict(start_result.get("scheduler") or {})
    deployment_id = int(deployment["id"])
    runner_id = scheduler.get("runner_id") or deployment.get("runner_id")
    slot_id = scheduler.get("slot_id") or deployment.get("slot_id")
    _emit(
        "start_submitted",
        cycle=cycle,
        account_id=account_id,
        deployment_id=deployment_id,
        runner_id=runner_id,
        slot_id=slot_id,
        status=deployment.get("status"),
    )
    _assert_expected_runner(
        expected_runner_id=expected_runner_id,
        phase="start",
        runner_id=runner_id,
        slot_id=slot_id,
    )
    running = await _poll_deployment(
        service=service,
        telegram_id=telegram_id,
        username=username,
        deployment_id=deployment_id,
        target_status="running",
        timeout_sec=int(cfg["start_timeout_sec"]),
        poll_sec=poll_sec,
    )
    start_sec = time.monotonic() - start_started

    leave_running = bool(cfg.get("leave_running_final")) and cycle == int(cfg["cycles"])
    if leave_running:
        slot_after = _slot_snapshot(service, str(running.get("runner_id") or runner_id), str(running.get("slot_id") or slot_id))
        total_sec = time.monotonic() - started_at
        _emit("leave_running_final", cycle=cycle, deployment_id=deployment_id, runner_id=runner_id, slot_id=slot_id)
        return {
            "cycle": cycle,
            "account_id": account_id,
            "deployment_id": deployment_id,
            "runner_id": running.get("runner_id") or runner_id,
            "slot_id": running.get("slot_id") or slot_id,
            "login_verified_sec": round(login_sec, 3),
            "start_running_sec": round(start_sec, 3),
            "stop_stopped_sec": None,
            "total_sec": round(total_sec, 3),
            "start_health_status": running.get("health_status"),
            "final_status": running.get("status"),
            "left_running": True,
            "slot_after": {
                "status": (slot_after or {}).get("status"),
                "current_account_id": (slot_after or {}).get("current_account_id"),
            },
        }

    hold_sec = max(0.0, float(cfg.get("hold_sec") or 0.0))
    if hold_sec > 0:
        _emit("hold_running", cycle=cycle, deployment_id=deployment_id, seconds=hold_sec)
        await asyncio.sleep(hold_sec)

    stop_started = time.monotonic()
    stop_result = await service.stop_deployment(
        telegram_id=telegram_id,
        username=username,
        deployment_id=deployment_id,
        reason=f"single_account_lifecycle_cycle_{cycle}",
    )
    stop_deployment = dict(stop_result.get("deployment") or {})
    _emit(
        "stop_submitted",
        cycle=cycle,
        deployment_id=deployment_id,
        status=stop_deployment.get("status"),
    )
    stopped = await _poll_deployment(
        service=service,
        telegram_id=telegram_id,
        username=username,
        deployment_id=deployment_id,
        target_status="stopped",
        timeout_sec=int(cfg["stop_timeout_sec"]),
        poll_sec=poll_sec,
    )
    stop_sec = time.monotonic() - stop_started

    cleanup = await _cleanup_account(
        service=service,
        telegram_id=telegram_id,
        username=username,
        account_id=account_id,
        reason=f"single_account_lifecycle_cycle_{cycle}_settle",
        timeout_sec=int(cfg["cleanup_timeout_sec"]),
        poll_sec=poll_sec,
    )
    active_deployments = cleanup.get("active_deployments") or []
    active_reservations = cleanup.get("active_login_reservations") or []
    if active_deployments or active_reservations or cleanup.get("errors"):
        raise LifecycleError(
            "post_cycle_cleanup_not_clean:"
            f"deployments={len(active_deployments)}:"
            f"reservations={len(active_reservations)}:"
            f"errors={len(cleanup.get('errors') or [])}"
        )

    slot_after = _slot_snapshot(service, str(stopped.get("runner_id") or runner_id), str(stopped.get("slot_id") or slot_id))
    total_sec = time.monotonic() - started_at
    return {
        "cycle": cycle,
        "account_id": account_id,
        "deployment_id": deployment_id,
        "runner_id": stopped.get("runner_id") or runner_id,
        "slot_id": stopped.get("slot_id") or slot_id,
        "login_verified_sec": round(login_sec, 3),
        "start_running_sec": round(start_sec, 3),
        "stop_stopped_sec": round(stop_sec, 3),
        "total_sec": round(total_sec, 3),
        "start_health_status": running.get("health_status"),
        "final_status": stopped.get("status"),
        "slot_after": {
            "status": (slot_after or {}).get("status"),
            "current_account_id": (slot_after or {}).get("current_account_id"),
        },
    }


async def main() -> int:
    cfg = PAYLOAD["config"]
    if not cfg.get("execute"):
        _emit("dry_run", message="pass --execute to run login/start/stop cycles")
        return 0

    service = MT5ControlPlaneService()
    telegram_id = str(cfg["telegram_id"]).strip()
    username = str(cfg.get("username") or "").strip() or None
    account = _resolve_account(service, cfg)
    account_id = int(account["id"])
    expected_runner_id = str(cfg.get("expected_runner_id") or "").strip()

    _emit(
        "account_selected",
        account_id=account_id,
        broker=account.get("broker"),
        server=account.get("server"),
        login=_mask_login(account.get("login")),
        status=account.get("status") or account.get("raw_status"),
    )
    if expected_runner_id:
        _emit("runner_before", **_runner_brief(service, expected_runner_id))

    initial_cleanup = await _cleanup_account(
        service=service,
        telegram_id=telegram_id,
        username=username,
        account_id=account_id,
        reason="single_account_lifecycle_initial_cleanup",
        timeout_sec=int(cfg["cleanup_timeout_sec"]),
        poll_sec=float(cfg["poll_sec"]),
    )
    if initial_cleanup.get("errors"):
        raise LifecycleError(f"initial_cleanup_errors:{initial_cleanup['errors']}")
    _emit(
        "initial_cleanup",
        stopped=len(initial_cleanup.get("stopped") or []),
        released=len(initial_cleanup.get("released") or []),
        active_deployments=len(initial_cleanup.get("active_deployments") or []),
        active_login_reservations=len(initial_cleanup.get("active_login_reservations") or []),
    )

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    cycles = max(1, int(cfg["cycles"]))
    for cycle in range(1, cycles + 1):
        _emit("cycle_begin", cycle=cycle, cycles=cycles)
        try:
            result = await _run_cycle(service=service, cfg=cfg, account=account, cycle=cycle)
            results.append(result)
            _emit("cycle_done", **result)
        except Exception as exc:
            failures.append({"cycle": cycle, "error": str(exc), "error_type": exc.__class__.__name__})
            _emit("cycle_failed", cycle=cycle, error=str(exc), error_type=exc.__class__.__name__)
            cleanup = await _cleanup_account(
                service=service,
                telegram_id=telegram_id,
                username=username,
                account_id=account_id,
                reason=f"single_account_lifecycle_failure_cycle_{cycle}",
                timeout_sec=int(cfg["cleanup_timeout_sec"]),
                poll_sec=float(cfg["poll_sec"]),
            )
            _emit(
                "failure_cleanup",
                cycle=cycle,
                stopped=len(cleanup.get("stopped") or []),
                released=len(cleanup.get("released") or []),
                errors=len(cleanup.get("errors") or []),
                active_deployments=len(cleanup.get("active_deployments") or []),
                active_login_reservations=len(cleanup.get("active_login_reservations") or []),
            )
            if bool(cfg.get("fail_fast")):
                break
        settle_sec = max(0.0, float(cfg.get("settle_sec") or 0.0))
        if settle_sec > 0 and cycle < cycles:
            await asyncio.sleep(settle_sec)

    leave_running_final = bool(cfg.get("leave_running_final")) and not failures
    if leave_running_final:
        final_cleanup = {
            "skipped": True,
            "reason": "leave_running_final",
            "active_deployments": _active_deployments(service, account_id),
            "active_login_reservations": _active_login_reservations(service, account_id),
            "errors": [],
            "released": [],
            "stopped": [],
        }
    else:
        final_cleanup = await _cleanup_account(
            service=service,
            telegram_id=telegram_id,
            username=username,
            account_id=account_id,
            reason="single_account_lifecycle_final_cleanup",
            timeout_sec=int(cfg["cleanup_timeout_sec"]),
            poll_sec=float(cfg["poll_sec"]),
        )
    if expected_runner_id:
        _emit("runner_after", **_runner_brief(service, expected_runner_id))

    summary = {
        "account_id": account_id,
        "login": _mask_login(account.get("login")),
        "broker": account.get("broker"),
        "server": account.get("server"),
        "expected_runner_id": expected_runner_id or None,
        "cycles_requested": cycles,
        "cycles_passed": len(results),
        "cycles_failed": len(failures),
        "failures": failures,
        "results": results,
        "final_cleanup": final_cleanup,
    }
    print("SINGLE_ACCOUNT_LIFECYCLE_SUMMARY_JSON=" + json.dumps(_jsonable(summary), ensure_ascii=False, sort_keys=True), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        _emit("fatal", error=str(exc), error_type=exc.__class__.__name__)
        raise
'''


def _bool_arg(value: str | None) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected boolean: 1/0, true/false, on/off")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run repeated production-safe login -> start bot -> stop bot cycles for one existing MT5 account. "
            "This does not bypass the one-active-deployment-per-account guard."
        )
    )
    parser.add_argument("--compose-env-file", default=str(ROOT / ".env"), help="Docker Compose env file.")
    parser.add_argument("--compose-file", default=str(ROOT / "docker-compose.yml"), help="Docker Compose file.")
    parser.add_argument("--service", default="cntx-lab", help="Backend Compose service name.")
    parser.add_argument("--account-id", type=int, default=0, help="Existing broker_accounts.id. Preferred.")
    parser.add_argument("--login", default="", help="Account login if --account-id is omitted.")
    parser.add_argument("--server", default="", help="Account server if --account-id is omitted.")
    parser.add_argument("--broker", default="", help="Optional broker filter if --account-id is omitted.")
    parser.add_argument("--telegram-id", default=os.getenv("STRESS_TELEGRAM_ID", "5573261363"), help="Owner Telegram id.")
    parser.add_argument("--username", default=os.getenv("STRESS_USERNAME", "ngtruong360500"), help="Owner username/audit label.")
    parser.add_argument("--bot-name", default="gsalgovip", help="Bot catalog name/code to start.")
    parser.add_argument("--mode", choices=["live", "paper"], default="live", help="Deployment mode.")
    parser.add_argument("--cycles", type=int, default=10, help="Number of sequential user lifecycle cycles.")
    parser.add_argument("--expected-runner-id", default="", help="Fail if login/start routes to a different runner.")
    parser.add_argument(
        "--drain-other-runners",
        action="store_true",
        help="Temporarily put all runners except --expected-runner-id into maintenance for this test, then resume them.",
    )
    parser.add_argument("--lot-size", type=float, default=0.01)
    parser.add_argument("--stop-loss", type=float, default=5.0)
    parser.add_argument("--take-profit", type=float, default=5.0)
    parser.add_argument("--trading-unit", choices=["price_distance", "points"], default="price_distance")
    parser.add_argument("--dca-enabled", type=_bool_arg, default=None)
    parser.add_argument("--login-timeout-sec", type=int, default=360)
    parser.add_argument("--start-timeout-sec", type=int, default=240)
    parser.add_argument("--stop-timeout-sec", type=int, default=180)
    parser.add_argument("--cleanup-timeout-sec", type=int, default=180)
    parser.add_argument("--poll-sec", type=float, default=3.0)
    parser.add_argument("--hold-sec", type=float, default=3.0, help="Seconds to keep each bot running before STOP.")
    parser.add_argument("--leave-running-final", action="store_true", help="Leave the final cycle running for manual observation.")
    parser.add_argument("--settle-sec", type=float, default=2.0, help="Pause between cycles.")
    parser.add_argument("--fail-fast", type=_bool_arg, default=True, help="Stop after the first failed cycle.")
    parser.add_argument("--execute", action="store_true", help="Actually submit login/start/stop commands.")
    parser.add_argument("--json", action="store_true", help="Emit progress as JSON lines.")
    return parser.parse_args(argv)


def _run_backend_maintenance(
    *,
    action: str,
    target_runner_id: str,
    runner_ids: list[str],
    compose_env_file: str,
    compose_file: str,
    service: str,
) -> list[str]:
    payload = {
        "action": action,
        "target_runner_id": target_runner_id,
        "runner_ids": runner_ids,
    }
    payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    snippet = f"""
import base64, json
from app.services.control_plane_service import MT5ControlPlaneService

payload = json.loads(base64.b64decode({payload_b64!r}).decode("utf-8"))
svc = MT5ControlPlaneService()
action = str(payload["action"])
target = str(payload.get("target_runner_id") or "").strip()
runner_ids = [str(item).strip() for item in payload.get("runner_ids") or [] if str(item).strip()]
if action == "drain":
    if not target:
        raise SystemExit("target_runner_id_required_for_drain")
    if not runner_ids:
        runner_ids = [
            str(item.get("runner_id") or "").strip()
            for item in svc.list_runners()
            if str(item.get("runner_id") or "").strip() and str(item.get("runner_id") or "").strip() != target
        ]
    changed = []
    for runner_id in runner_ids:
        result = svc.enter_runner_maintenance(
            runner_id=runner_id,
            reason="single_account_lifecycle_node_pin",
            actor="ops/stress_single_account_lifecycle.py",
            disable_ready_slots=True,
        )
        changed.append({{
            "runner_id": runner_id,
            "action": "drain",
            "disabled_slots": (result.get("maintenance") or {{}}).get("disabled_slots"),
            "status": ((result.get("runner") or {{}}).get("status") or result.get("status")),
            "operational_status": ((result.get("runner") or {{}}).get("operational_status")),
        }})
    print("RUNNER_MAINTENANCE_JSON=" + json.dumps({{"action": action, "changed": changed}}, ensure_ascii=False, sort_keys=True))
elif action == "resume":
    changed = []
    for runner_id in runner_ids:
        result = svc.exit_runner_maintenance(
            runner_id=runner_id,
            reason="single_account_lifecycle_node_pin_done",
            actor="ops/stress_single_account_lifecycle.py",
            enable_disabled_slots=True,
        )
        changed.append({{
            "runner_id": runner_id,
            "action": "resume",
            "enabled_slots": (result.get("maintenance") or {{}}).get("enabled_slots"),
            "status": ((result.get("runner") or {{}}).get("status") or result.get("status")),
            "operational_status": ((result.get("runner") or {{}}).get("operational_status")),
        }})
    print("RUNNER_MAINTENANCE_JSON=" + json.dumps({{"action": action, "changed": changed}}, ensure_ascii=False, sort_keys=True))
else:
    raise SystemExit("unknown_action:" + action)
"""
    cmd = [
        "docker",
        "compose",
        "--env-file",
        str(Path(compose_env_file).expanduser().resolve()),
        "-f",
        str(Path(compose_file).expanduser().resolve()),
        "exec",
        "-T",
        service,
        "python",
        "-",
    ]
    completed = subprocess.run(cmd, input=snippet, text=True, cwd=str(ROOT), capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="", flush=True)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr, flush=True)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    changed_ids: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.startswith("RUNNER_MAINTENANCE_JSON="):
            continue
        data = json.loads(line.split("=", 1)[1])
        for item in data.get("changed") or []:
            runner_id = str(item.get("runner_id") or "").strip()
            if runner_id:
                changed_ids.append(runner_id)
    return changed_ids


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.drain_other_runners and not args.expected_runner_id:
        raise SystemExit("--drain-other-runners requires --expected-runner-id")
    trading_config: dict[str, Any] = {
        "trading": {
            "lot_size": args.lot_size,
            "stop_loss": args.stop_loss,
            "take_profit": args.take_profit,
            "trading_unit": args.trading_unit,
        }
    }
    if args.dca_enabled is not None:
        trading_config["trading"]["dca_enabled"] = bool(args.dca_enabled)

    payload = {
        "config": {
            "execute": bool(args.execute),
            "json": bool(args.json),
            "account_id": int(args.account_id or 0),
            "login": args.login,
            "server": args.server,
            "broker": args.broker,
            "telegram_id": args.telegram_id,
            "username": args.username,
            "bot_name": args.bot_name,
            "mode": args.mode,
            "cycles": int(args.cycles),
            "expected_runner_id": args.expected_runner_id,
            "trading_config": trading_config,
            "login_timeout_sec": int(args.login_timeout_sec),
            "start_timeout_sec": int(args.start_timeout_sec),
            "stop_timeout_sec": int(args.stop_timeout_sec),
            "cleanup_timeout_sec": int(args.cleanup_timeout_sec),
            "poll_sec": float(args.poll_sec),
            "hold_sec": float(args.hold_sec),
            "leave_running_final": bool(args.leave_running_final),
            "settle_sec": float(args.settle_sec),
            "fail_fast": bool(args.fail_fast),
        }
    }
    payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    inner = INNER_RUNNER.replace("__PAYLOAD_B64__", payload_b64)

    cmd = [
        "docker",
        "compose",
        "--env-file",
        str(Path(args.compose_env_file).expanduser().resolve()),
        "-f",
        str(Path(args.compose_file).expanduser().resolve()),
        "exec",
        "-T",
        args.service,
        "python",
        "-",
    ]
    drained_runner_ids: list[str] = []
    try:
        if args.execute and args.drain_other_runners:
            drained_runner_ids = _run_backend_maintenance(
                action="drain",
                target_runner_id=args.expected_runner_id,
                runner_ids=[],
                compose_env_file=args.compose_env_file,
                compose_file=args.compose_file,
                service=args.service,
            )
        completed = subprocess.run(cmd, input=inner, text=True, cwd=str(ROOT))
        return int(completed.returncode or 0)
    finally:
        if args.execute and drained_runner_ids:
            _run_backend_maintenance(
                action="resume",
                target_runner_id=args.expected_runner_id,
                runner_ids=drained_runner_ids,
                compose_env_file=args.compose_env_file,
                compose_file=args.compose_file,
                service=args.service,
            )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
