#!/usr/bin/env python3
"""Controlled chaos checks for CNTx MT5 control plane.

Default mode is dry-run/read-only. Destructive scenarios require --execute so
they are hard to trigger by accident on the main production VPS.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def request_json(method: str, url: str, *, headers: dict[str, str] | None = None, body: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
            return {"ok": 200 <= resp.status < 300, "status": resp.status, "body": json.loads(payload or "{}")}
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(payload or "{}")
        except Exception:
            parsed = {"raw": payload}
        return {"ok": False, "status": exc.code, "body": parsed}
    except Exception as exc:
        return {"ok": False, "status": 0, "body": {"error": f"{exc.__class__.__name__}:{str(exc)[:200]}"}}


def run_compose(args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    cmd = ["docker", "compose", "--env-file", ".env", "-f", "docker-compose.yml", *args]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def wait_ready(base_url: str, *, timeout_sec: int = 60) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = request_json("GET", f"{base_url.rstrip('/')}/ready", timeout=10)
        if last.get("ok") and (last.get("body") or {}).get("ready"):
            return last
        time.sleep(2)
    return last or {"ok": False, "body": {"error": "ready_timeout"}}


def headers(env: dict[str, str]) -> dict[str, str]:
    api_key = env.get("BACKEND_API_KEY", "")
    return {"X-Backend-Api-Key": api_key} if api_key else {}


def scenario_readonly(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    base = args.backend_url.rstrip("/")
    return {
        "ready": request_json("GET", f"{base}/ready"),
        "ops_summary": request_json("GET", f"{base}/api/v2/system/ops-summary", headers=headers(env)),
        "command_delivery": request_json(
            "GET",
            f"{base}/api/v2/system/command-delivery-observability?window_sec=3600&stale_sec=300&limit=25",
            headers=headers(env),
        ),
    }


def scenario_backend_restart(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    if not args.execute:
        return {"dry_run": True, "would_run": "docker compose restart cntx-lab"}
    before = scenario_readonly(args, env)
    restart = run_compose(["restart", "cntx-lab"], timeout=180)
    ready = wait_ready(args.backend_url, timeout_sec=90)
    after = scenario_readonly(args, env)
    return {"before": before, "restart": restart, "ready_after_restart": ready, "after": after}


def scenario_webhook_close(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    if not args.execute:
        return {
            "dry_run": True,
            "warning": "This sends CLOSE_ORDER to all active subscribers. Use only on demo/no-position tests.",
        }
    secret = env.get("TRADINGVIEW_WEBHOOK_SECRET", "")
    if not secret:
        return {"ok": False, "error": "TRADINGVIEW_WEBHOOK_SECRET_missing"}
    alert_id = f"chaos-close-{int(time.time())}"
    body = {
        "alert_id": alert_id,
        "action": "CLOSE",
        "symbol": args.symbol,
        "bot_code": args.bot_code,
        "max_subscribers": args.max_subscribers,
    }
    response = request_json(
        "POST",
        f"{args.backend_url.rstrip('/')}/api/v2/public/tradingview/broadcast/{args.signal_id}",
        headers={"X-TradingView-Secret": secret},
        body=body,
        timeout=30,
    )
    time.sleep(args.settle_sec)
    verify = run_compose(
        [
            "exec",
            "-T",
            "db",
            "sh",
            "-lc",
            (
                "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -c "
                f"\"SELECT account_id,deployment_id,slot_id,delivery_status,last_error "
                f"FROM execution_commands WHERE payload_json->>'broadcast_alert_id'='{alert_id}' "
                "ORDER BY account_id;\""
            ),
        ],
        timeout=60,
    )
    return {"alert_id": alert_id, "dispatch": response, "delivery_rows": verify}


def scenario_windows_kill_slot_plan(args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:  # noqa: ARG001
    slot_id = args.slot_id
    return {
        "dry_run": True,
        "windows_side_plan": [
            f"Pick active {slot_id} on Windows runner.",
            "Kill only the terminal64.exe owned by that slot, not all MT5 terminals.",
            "Watch runner emit SLOT_DEGRADED/SLOT_BROKEN or heartbeat inventory update.",
            "Expected Linux result: bot state becomes degraded/failed or auto-recovered; no stale active slot after SLA.",
            "After test, run this script with scenario readonly to verify queue=0 and no stale delivery commands.",
        ],
    }


SCENARIOS = {
    "readonly": scenario_readonly,
    "backend-restart": scenario_backend_restart,
    "webhook-close": scenario_webhook_close,
    "windows-kill-slot-plan": scenario_windows_kill_slot_plan,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=sorted(SCENARIOS), default="readonly", nargs="?")
    parser.add_argument("--execute", action="store_true", help="Actually run destructive scenarios.")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--backend-url", default="http://127.0.0.1:8001")
    parser.add_argument("--signal-id", default="gsalgovip-xauusd")
    parser.add_argument("--bot-code", default="gsalgovip")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--max-subscribers", type=int, default=20)
    parser.add_argument("--settle-sec", type=int, default=8)
    parser.add_argument("--slot-id", default="slot-01")
    args = parser.parse_args()

    env = {**load_env(Path(args.env_file)), **{k: v for k, v in os.environ.items() if k.startswith("CNTX_")}}
    result = SCENARIOS[args.scenario](args, env)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok", True) is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
