#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCOUNTS_FILE = ROOT / "ops" / "stress" / "accounts_exness_demo_39.csv"


INNER_RUNNER = r'''
from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.services.control_plane_service import MT5ControlPlaneService


PAYLOAD = json.loads(base64.b64decode("__PAYLOAD_B64__").decode("utf-8"))
ACTIVE_DEPLOYMENT_STATUSES = {"start_requested", "starting", "running", "stop_requested"}
TERMINAL_LOGIN_SLOT_STATUSES = {"verified", "failed", "expired", "released", "claimed"}


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
    if PAYLOAD["config"].get("json"):
        print(json.dumps({"event": event, **_jsonable(fields)}, ensure_ascii=False), flush=True)
        return
    detail = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    print(f"[stress] {event}" + (f" {detail}" if detail else ""), flush=True)


def _mask_login(login: Any) -> str:
    text = str(login or "").strip()
    if len(text) <= 5:
        return "***"
    return f"{text[:3]}***{text[-2:]}"


def _split_ids(raw: Any) -> list[str]:
    text = str(raw or "")
    return [part.strip() for part in re.split(r"[,;\s]+", text) if part.strip()]


def _first_admin_telegram_id() -> str:
    from app.settings import settings

    for raw in (getattr(settings, "ADMIN_TELEGRAM_IDS", ""), getattr(settings, "DEV_CHAT_ID", "")):
        ids = _split_ids(raw)
        if ids:
            return ids[0]
    raise RuntimeError("missing_telegram_id: pass --telegram-id or set ADMIN_TELEGRAM_IDS")


def _identity(account: dict[str, Any]) -> tuple[str, str]:
    return (str(account.get("server") or "").strip().lower(), str(account.get("login") or "").strip())


def _owner_for_index(cfg: dict[str, Any], index: int) -> tuple[str, str]:
    mode = str(cfg.get("owner_mode") or "per-account").strip().lower()
    if mode == "single":
        telegram_id = str(cfg.get("telegram_id") or _first_admin_telegram_id()).strip()
        username = str(cfg.get("username") or "runner_stress").strip()
        return telegram_id, username
    base_raw = str(cfg.get("telegram_id_base") or "900000000000").strip()
    try:
        telegram_id = str(int(base_raw) + int(index))
    except Exception as exc:
        raise RuntimeError("invalid_telegram_id_base") from exc
    username_base = str(cfg.get("username") or "runner_stress").strip() or "runner_stress"
    return telegram_id, f"{username_base}_{index:02d}"


def _active_deployment(account: dict[str, Any]) -> dict[str, Any] | None:
    dep_id = account.get("active_deployment_id")
    status = str(account.get("active_deployment_status") or "").strip().lower()
    if dep_id and status in ACTIVE_DEPLOYMENT_STATUSES:
        return {
            "id": dep_id,
            "status": status,
            "runner_id": account.get("runner_id"),
            "slot_id": account.get("slot_id"),
        }
    return None


def _runner_snapshot(service: MT5ControlPlaneService, runner_id: str) -> dict[str, Any]:
    health = service.get_runner_health(runner_id=runner_id) or {}
    runner = dict(health.get("runner") or {})
    if not runner:
        raise RuntimeError(f"runner_not_found:{runner_id}")
    return runner


def _runner_brief(runner: dict[str, Any]) -> dict[str, Any]:
    queue = dict(runner.get("queue_depth") or {})
    return {
        "runner_id": runner.get("runner_id"),
        "status": runner.get("status"),
        "operational_status": runner.get("operational_status"),
        "is_stale": bool(runner.get("is_stale")),
        "accepts_new_work": bool(runner.get("accepts_new_work")),
        "total_slots": runner.get("total_slots"),
        "available_slots": runner.get("available_slots"),
        "allocated_slots": runner.get("allocated_slots"),
        "healthy_slots": runner.get("healthy_slots"),
        "login_reserved_slots": runner.get("login_reserved_slots"),
        "running_deployments": runner.get("running_deployments"),
        "failed_deployments": runner.get("failed_deployments"),
        "queue_depth": {
            "commands": queue.get("commands"),
            "commands_processing": queue.get("commands_processing"),
        },
        "last_heartbeat_at": runner.get("last_heartbeat_at"),
        "heartbeat_age_sec": runner.get("heartbeat_age_sec"),
    }


def _assert_runner_can_accept(runner: dict[str, Any], *, required_starts: int, allow_not_empty: bool) -> None:
    queue = dict(runner.get("queue_depth") or {})
    command_backlog = int(queue.get("commands") or 0) + int(queue.get("commands_processing") or 0)
    allocated = int(runner.get("allocated_slots") or 0)
    available = int(runner.get("available_slots") or 0)
    status = str(runner.get("status") or "").strip().lower()
    if status != "online" or bool(runner.get("is_stale")):
        raise RuntimeError(f"runner_not_online:status={status}:stale={bool(runner.get('is_stale'))}")
    if not bool(runner.get("accepts_new_work")):
        raise RuntimeError("runner_not_accepting_new_work")
    if not allow_not_empty and (allocated > 0 or command_backlog > 0):
        raise RuntimeError(
            f"runner_not_empty:allocated={allocated}:command_backlog={command_backlog}:use --allow-runner-not-empty"
        )
    if required_starts > 0 and available < required_starts and not allow_not_empty:
        raise RuntimeError(f"not_enough_available_slots:required={required_starts}:available={available}")


async def _request_login_slots(
    *,
    service: MT5ControlPlaneService,
    selected: list[dict[str, Any]],
    execute: bool,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for item in selected:
        account = item["account"]
        telegram_id = item["telegram_id"]
        username = item["username"]
        account_id = int(account["id"])
        active = _active_deployment(account)
        if active:
            _emit("login_slot_skip_active_deployment", account_id=account_id, deployment_id=active.get("id"))
            continue
        if not execute:
            _emit("login_slot_plan", account_id=account_id, login=_mask_login(account.get("login")))
            continue
        result = await service.request_account_login_slot(
            telegram_id=telegram_id,
            username=username,
            account_id=account_id,
        )
        reservation_id = result.get("login_reservation_id") or result.get("id")
        jobs.append(
            {
                "login_reservation_id": int(reservation_id),
                "account_id": account_id,
                "login": account.get("login"),
                "telegram_id": telegram_id,
                "username": username,
            }
        )
        _emit(
            "login_slot_submitted",
            account_id=account_id,
            login_reservation_id=reservation_id,
            runner_id=result.get("runner_id"),
            slot_id=result.get("slot_id"),
            status=result.get("status"),
        )
    return jobs


async def _poll_login_slots(
    *,
    service: MT5ControlPlaneService,
    jobs: list[dict[str, Any]],
    timeout_sec: int,
    poll_sec: float,
) -> dict[int, dict[str, Any]]:
    if not jobs:
        return {}
    deadline = time.monotonic() + max(1, int(timeout_sec))
    pending = {int(job["login_reservation_id"]): dict(job) for job in jobs}
    completed: dict[int, dict[str, Any]] = {}
    last_status: dict[int, str] = {}
    while pending and time.monotonic() < deadline:
        for job_id in list(pending):
            owner = pending[job_id]
            job = service.get_account_login_slot(
                telegram_id=str(owner["telegram_id"]),
                username=str(owner["username"]),
                reservation_id=job_id,
            )
            status = str((job or {}).get("status") or "").strip().lower()
            if status and last_status.get(job_id) != status:
                _emit("login_slot_status", login_reservation_id=job_id, account_id=pending[job_id]["account_id"], status=status)
                last_status[job_id] = status
            if status in TERMINAL_LOGIN_SLOT_STATUSES:
                completed[pending[job_id]["account_id"]] = dict(job or {})
                pending.pop(job_id, None)
        if pending:
            await asyncio.sleep(max(1.0, float(poll_sec)))
    for job in pending.values():
        completed[int(job["account_id"])] = {"status": "timeout", "login_reservation_id": job["login_reservation_id"]}
        _emit("login_slot_timeout", account_id=job["account_id"], login_reservation_id=job["login_reservation_id"])
    return completed


async def _start_accounts(
    *,
    service: MT5ControlPlaneService,
    items: list[dict[str, Any]],
    execute: bool,
    bot_name: str,
    mode: str,
    trading_config: dict[str, Any],
    start_delay_sec: float,
    start_retry_attempts: int,
    start_retry_delay_sec: float,
) -> dict[str, Any]:
    started: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in items:
        account = item["account"]
        telegram_id = item["telegram_id"]
        username = item["username"]
        account_id = int(account["id"])
        active = _active_deployment(account)
        if active:
            skipped.append({"account_id": account_id, "reason": "active_deployment", **active})
            _emit("start_skip_active", account_id=account_id, deployment_id=active.get("id"), status=active.get("status"))
            continue
        if not execute:
            _emit("start_plan", account_id=account_id, login=_mask_login(account.get("login")), bot=bot_name)
            continue
        result = None
        last_exc: Exception | None = None
        attempts = max(0, int(start_retry_attempts or 0)) + 1
        for attempt in range(1, attempts + 1):
            try:
                result = await service.start_deployment(
                    telegram_id=telegram_id,
                    username=username,
                    account_id=account_id,
                    bot_name=bot_name,
                    bot_config_overrides=trading_config,
                    mode=mode,
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                error_text = str(exc)
                retryable = error_text in {
                    "runner_queue_backlog",
                    "no_scheduler_candidate",
                    "no_healthy_slot_available",
                } or "runner_queue_backlog" in error_text
                if retryable and attempt < attempts:
                    _emit(
                        "start_retry_wait",
                        account_id=account_id,
                        attempt=attempt,
                        max_attempts=attempts,
                        reason=error_text,
                        sleep_sec=start_retry_delay_sec,
                    )
                    await asyncio.sleep(max(1.0, float(start_retry_delay_sec)))
                    continue
                failed.append({"account_id": account_id, "error": error_text, "type": exc.__class__.__name__})
                _emit("start_failed", account_id=account_id, error=error_text, error_type=exc.__class__.__name__)
                break
        if result is None:
            if last_exc is None:
                failed.append({"account_id": account_id, "error": "unknown_start_failure", "type": "Unknown"})
            continue
        deployment = dict(result.get("deployment") or {})
        command = dict(result.get("command") or {})
        scheduler = dict(result.get("scheduler") or {})
        started.append(
            {
                "account_id": account_id,
                "deployment_id": deployment.get("id"),
                "command_id": command.get("command_id") or command.get("id"),
                "runner_id": scheduler.get("runner_id"),
                "slot_id": scheduler.get("slot_id"),
                "status": deployment.get("status"),
            }
        )
        _emit(
            "start_submitted",
            account_id=account_id,
            deployment_id=deployment.get("id"),
            runner_id=scheduler.get("runner_id"),
            slot_id=scheduler.get("slot_id"),
            status=deployment.get("status"),
        )
        if start_delay_sec > 0:
            await asyncio.sleep(float(start_delay_sec))
    return {"started": started, "skipped": skipped, "failed": failed}


async def main() -> int:
    cfg = PAYLOAD["config"]
    accounts = PAYLOAD["accounts"]
    execute = bool(cfg["execute"])
    service = MT5ControlPlaneService()
    runner_id = str(cfg["runner_id"]).strip()
    start_enabled = not bool(cfg.get("skip_start"))
    connect_enabled = not bool(cfg.get("skip_connect"))
    login_slot_sample = 0 if bool(cfg.get("skip_login_slot")) else max(0, int(cfg.get("login_slot_sample") or 0))
    start_count = len(accounts) if start_enabled else 0

    runner_before = _runner_snapshot(service, runner_id)
    queue_before = dict(runner_before.get("queue_depth") or {})
    _emit(
        "runner_before",
        runner_id=runner_before.get("runner_id"),
        status=runner_before.get("status"),
        operational_status=runner_before.get("operational_status"),
        total_slots=runner_before.get("total_slots"),
        available_slots=runner_before.get("available_slots"),
        allocated_slots=runner_before.get("allocated_slots"),
        login_reserved_slots=runner_before.get("login_reserved_slots"),
        commands=queue_before.get("commands"),
    )
    _assert_runner_can_accept(
        runner_before,
        required_starts=start_count,
        allow_not_empty=bool(cfg.get("allow_runner_not_empty")),
    )

    _emit(
        "owner_mode",
        mode=cfg.get("owner_mode"),
        base=cfg.get("telegram_id_base") if cfg.get("owner_mode") != "single" else None,
    )

    prepared: list[dict[str, Any]] = []
    created_count = 0
    existing_count = 0
    account_cache: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for index, raw in enumerate(accounts, start=1):
        telegram_id, username = _owner_for_index(cfg, index)
        user = service.ensure_user(telegram_id=telegram_id, username=username)
        if telegram_id not in account_cache:
            existing_accounts = service.list_accounts(telegram_id=telegram_id, username=username)
            account_cache[telegram_id] = {_identity(account): dict(account) for account in existing_accounts}
        by_identity = account_cache[telegram_id]
        account_key = (str(raw["server"]).strip().lower(), str(raw["login"]).strip())
        existing = by_identity.get(account_key)
        if existing:
            existing_count += 1
            prepared.append(
                {
                    "raw": raw,
                    "account": existing,
                    "index": index,
                    "created": False,
                    "telegram_id": telegram_id,
                    "username": username,
                    "user_id": user.get("id"),
                }
            )
            _emit("connect_existing", index=index, user_id=user.get("id"), account_id=existing.get("id"), login=_mask_login(raw["login"]))
            continue
        if not connect_enabled:
            raise RuntimeError(f"account_missing_and_connect_disabled:index={index}:login={raw['login']}")
        if not execute:
            prepared.append(
                {
                    "raw": raw,
                    "account": {"id": 0, **raw},
                    "index": index,
                    "created": False,
                    "telegram_id": telegram_id,
                    "username": username,
                    "user_id": user.get("id"),
                }
            )
            _emit("connect_plan", index=index, user_id=user.get("id"), login=_mask_login(raw["login"]), server=raw["server"])
            continue
        account = service.connect_account(
            telegram_id=telegram_id,
            username=username,
            broker=str(raw.get("broker") or cfg["broker"]),
            server=str(raw["server"]),
            login=str(raw["login"]),
            password=str(cfg.get("password") or ""),
            label=f"{cfg['label_prefix']} {index:02d}",
        )
        fetched = service.get_account(telegram_id=telegram_id, username=username, account_id=int(account["id"])) or account
        created_count += 1
        by_identity[account_key] = dict(fetched)
        prepared.append(
            {
                "raw": raw,
                "account": dict(fetched),
                "index": index,
                "created": True,
                "telegram_id": telegram_id,
                "username": username,
                "user_id": user.get("id"),
            }
        )
        _emit("connect_created", index=index, user_id=user.get("id"), account_id=account.get("id"), login=_mask_login(raw["login"]))

    login_slot_items = prepared[: min(login_slot_sample, len(prepared))]
    start_after_login_slot_indexes = {int(item["index"]) for item in login_slot_items}
    immediate_start_items = [item for item in prepared if int(item["index"]) not in start_after_login_slot_indexes]

    login_slot_jobs = await _request_login_slots(
        service=service,
        selected=login_slot_items,
        execute=execute,
    )

    start_results = {"started": [], "skipped": [], "failed": []}
    if start_enabled:
        immediate = await _start_accounts(
            service=service,
            items=immediate_start_items,
            execute=execute,
            bot_name=str(cfg["bot_name"]),
            mode=str(cfg["mode"]),
            trading_config=dict(cfg["trading_config"]),
            start_delay_sec=float(cfg["start_delay_sec"]),
            start_retry_attempts=int(cfg["start_retry_attempts"]),
            start_retry_delay_sec=float(cfg["start_retry_delay_sec"]),
        )
        for key in start_results:
            start_results[key].extend(immediate.get(key, []))

    login_slot_results = {}
    if login_slot_jobs and execute:
        login_slot_results = await _poll_login_slots(
            service=service,
            jobs=login_slot_jobs,
            timeout_sec=int(cfg["login_slot_timeout_sec"]),
            poll_sec=float(cfg["login_slot_poll_sec"]),
        )

    if start_enabled and login_slot_items:
        refreshed: list[dict[str, Any]] = []
        for item in login_slot_items:
            account_id = int(item["account"].get("id") or 0)
            result = login_slot_results.get(account_id) if execute else {"status": "dry_run"}
            status = str((result or {}).get("status") or "").strip().lower()
            if execute and status not in {"verified"}:
                start_results["skipped"].append({"account_id": account_id, "reason": f"login_slot_{status or 'missing'}"})
                _emit("start_skip_login_slot", account_id=account_id, login_slot_status=status or "missing")
                continue
            if account_id:
                account = service.get_account(
                    telegram_id=str(item["telegram_id"]),
                    username=str(item["username"]),
                    account_id=account_id,
                ) or item["account"]
            else:
                account = item["account"]
            refreshed.append({**item, "account": dict(account)})
        delayed = await _start_accounts(
            service=service,
            items=refreshed,
            execute=execute,
            bot_name=str(cfg["bot_name"]),
            mode=str(cfg["mode"]),
            trading_config=dict(cfg["trading_config"]),
            start_delay_sec=float(cfg["start_delay_sec"]),
            start_retry_attempts=int(cfg["start_retry_attempts"]),
            start_retry_delay_sec=float(cfg["start_retry_delay_sec"]),
        )
        for key in start_results:
            start_results[key].extend(delayed.get(key, []))

    monitor_sec = int(cfg.get("monitor_sec") or 0)
    monitor_poll_sec = max(2, int(cfg.get("monitor_poll_sec") or 10))
    monitor_samples: list[dict[str, Any]] = []
    if execute and monitor_sec > 0:
        deadline = time.monotonic() + monitor_sec
        while time.monotonic() < deadline:
            runner = _runner_snapshot(service, runner_id)
            queue = dict(runner.get("queue_depth") or {})
            sample = {
                "ts": int(time.time()),
                "available_slots": runner.get("available_slots"),
                "allocated_slots": runner.get("allocated_slots"),
                "running_deployments": runner.get("running_deployments"),
                "failed_deployments": runner.get("failed_deployments"),
                "command_queue": queue.get("commands"),
                "command_processing": queue.get("commands_processing"),
            }
            monitor_samples.append(sample)
            _emit("monitor", **sample)
            await asyncio.sleep(monitor_poll_sec)

    runner_after = _runner_snapshot(service, runner_id)
    queue_after = dict(runner_after.get("queue_depth") or {})
    _emit(
        "runner_after",
        runner_id=runner_after.get("runner_id"),
        available_slots=runner_after.get("available_slots"),
        allocated_slots=runner_after.get("allocated_slots"),
        running_deployments=runner_after.get("running_deployments"),
        failed_deployments=runner_after.get("failed_deployments"),
        commands=queue_after.get("commands"),
        commands_processing=queue_after.get("commands_processing"),
    )

    summary = {
        "execute": execute,
        "accounts_loaded": len(accounts),
        "accounts_existing": existing_count,
        "accounts_created": created_count,
        "login_slots_submitted": len(login_slot_jobs),
        "login_slots_verified": sum(1 for item in login_slot_results.values() if str(item.get("status") or "").lower() == "verified"),
        "login_slots_failed_or_timeout": sum(
            1
            for item in login_slot_results.values()
            if str(item.get("status") or "").lower() in {"failed", "timeout", "cancelled"}
        ),
        "start_submitted": len(start_results["started"]),
        "start_skipped": len(start_results["skipped"]),
        "start_failed": len(start_results["failed"]),
        "runner_before": _runner_brief(runner_before),
        "runner_after": _runner_brief(runner_after),
        "starts": start_results,
        "monitor_samples": monitor_samples,
    }
    print("STRESS_SUMMARY_JSON=" + json.dumps(_jsonable(summary), ensure_ascii=False, sort_keys=True), flush=True)
    return 1 if execute and start_results["failed"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        _emit("fatal", error=str(exc), error_type=exc.__class__.__name__)
        raise
'''


def _load_accounts(path: Path, broker: str, max_accounts: int) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"accounts file not found: {path}")
    accounts: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            login = str(row.get("login") or "").strip()
            server = str(row.get("server") or "").strip()
            if not login or not server:
                continue
            accounts.append({"login": login, "server": server, "broker": str(row.get("broker") or broker).strip() or broker})
            if max_accounts > 0 and len(accounts) >= max_accounts:
                break
    if not accounts:
        raise SystemExit(f"no accounts loaded from {path}")
    return accounts


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
    parser = argparse.ArgumentParser(description="Run a backend-driven MT5 Windows runner stress test.")
    parser.add_argument("--accounts-file", default=str(DEFAULT_ACCOUNTS_FILE), help="CSV with login,server columns.")
    parser.add_argument("--compose-env-file", default=str(ROOT / ".env"), help="Docker Compose env file.")
    parser.add_argument("--compose-file", default=str(ROOT / "docker-compose.yml"), help="Docker Compose file.")
    parser.add_argument("--service", default="spider-app", help="Backend Compose service name.")
    parser.add_argument("--runner-id", default="runner-win-01", help="Target Windows runner id.")
    parser.add_argument("--owner-mode", choices=["per-account", "single"], default=os.getenv("STRESS_OWNER_MODE", "per-account"), help="Use one synthetic user per account, or one owner for all accounts.")
    parser.add_argument("--telegram-id", default=os.getenv("STRESS_TELEGRAM_ID", ""), help="Owner Telegram id for --owner-mode single. Defaults to first ADMIN_TELEGRAM_IDS inside backend.")
    parser.add_argument("--telegram-id-base", default=os.getenv("STRESS_TELEGRAM_ID_BASE", "900000000000"), help="Base Telegram id for --owner-mode per-account.")
    parser.add_argument("--username", default=os.getenv("STRESS_USERNAME", "runner_stress"), help="Synthetic username/audit label.")
    parser.add_argument("--broker", default="Exness", help="Broker name for the account rows.")
    parser.add_argument("--bot-name", default="gsalgovip", help="Bot catalog name/code to start.")
    parser.add_argument("--mode", choices=["live", "paper"], default="live", help="Deployment mode.")
    parser.add_argument("--lot-size", type=float, default=0.01)
    parser.add_argument("--stop-loss", type=float, default=5.0)
    parser.add_argument("--take-profit", type=float, default=5.0)
    parser.add_argument("--trading-unit", choices=["price_distance", "points"], default="price_distance")
    parser.add_argument("--dca-enabled", type=_bool_arg, default=None)
    parser.add_argument("--max-accounts", type=int, default=0, help="Limit accounts loaded from CSV; 0 means all.")
    parser.add_argument("--login-slot-sample", type=int, default=3, help="How many accounts to reserve/login before delayed start.")
    parser.add_argument("--login-slot-timeout-sec", type=int, default=900)
    parser.add_argument("--login-slot-poll-sec", type=float, default=5.0)
    parser.add_argument("--start-delay-sec", type=float, default=5.0, help="Delay between START_BOT submissions.")
    parser.add_argument("--start-retry-attempts", type=int, default=20, help="Retries for transient scheduler backlog.")
    parser.add_argument("--start-retry-delay-sec", type=float, default=30.0, help="Backoff when runner_queue_backlog is hit.")
    parser.add_argument("--monitor-sec", type=int, default=0, help="Poll runner health after submitting commands.")
    parser.add_argument("--monitor-poll-sec", type=int, default=10)
    parser.add_argument("--password-env", default="STRESS_MT5_PASSWORD", help="Environment variable containing the shared MT5 password.")
    parser.add_argument("--label-prefix", default="MT5 stress", help="Account label prefix for newly connected accounts.")
    parser.add_argument("--skip-connect", action="store_true")
    parser.add_argument("--skip-login-slot", action="store_true")
    parser.add_argument("--skip-start", action="store_true")
    parser.add_argument("--allow-runner-not-empty", action="store_true", help="Allow running while slots/command queue are not empty.")
    parser.add_argument("--execute", action="store_true", help="Actually connect accounts, reserve/login slots, and start bots.")
    parser.add_argument("--confirm-start-bots", action="store_true", help="Required with --execute unless --skip-start is set.")
    parser.add_argument("--json", action="store_true", help="Emit progress as JSON lines.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    accounts_file = Path(args.accounts_file).expanduser().resolve()
    accounts = _load_accounts(accounts_file, broker=args.broker, max_accounts=max(0, args.max_accounts))

    password = os.getenv(args.password_env, "")
    if args.execute and not args.skip_connect and not password:
        raise SystemExit(f"missing password: set {args.password_env} before running with --execute")
    if args.execute and not args.skip_start and not args.confirm_start_bots:
        raise SystemExit("--confirm-start-bots is required with --execute when starts are enabled")

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
        "accounts": accounts,
        "config": {
            "execute": bool(args.execute),
            "json": bool(args.json),
            "runner_id": args.runner_id,
            "owner_mode": args.owner_mode,
            "telegram_id": args.telegram_id,
            "telegram_id_base": args.telegram_id_base,
            "username": args.username,
            "broker": args.broker,
            "password": password if args.execute and not args.skip_connect else "",
            "bot_name": args.bot_name,
            "mode": args.mode,
            "trading_config": trading_config,
            "login_slot_sample": args.login_slot_sample,
            "login_slot_timeout_sec": args.login_slot_timeout_sec,
            "login_slot_poll_sec": args.login_slot_poll_sec,
            "start_delay_sec": args.start_delay_sec,
            "start_retry_attempts": args.start_retry_attempts,
            "start_retry_delay_sec": args.start_retry_delay_sec,
            "monitor_sec": args.monitor_sec,
            "monitor_poll_sec": args.monitor_poll_sec,
            "skip_connect": bool(args.skip_connect),
            "skip_login_slot": bool(args.skip_login_slot),
            "skip_start": bool(args.skip_start),
            "allow_runner_not_empty": bool(args.allow_runner_not_empty),
            "label_prefix": args.label_prefix,
        },
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
    completed = subprocess.run(cmd, input=inner, text=True, cwd=str(ROOT))
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
