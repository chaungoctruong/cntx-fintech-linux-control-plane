#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class Target:
    login: str
    server: str
    account_id: int
    telegram_id: str
    username: str
    note: str


TARGETS: list[Target] = [
    Target("463422165", "Exness-MT5Trial17", 132, "5573261363", "cntruong", "primary-current"),
    Target("433600611", "Exness-MT5Trial7", 40, "900000000001", "runner_stress_01", "stress-01"),
    Target("433600608", "Exness-MT5Trial7", 41, "900000000002", "runner_stress_02", "stress-02"),
    Target("463420484", "Exness-MT5Trial17", 42, "900000000003", "runner_stress_03", "stress-03"),
    Target("413776415", "Exness-MT5Trial6", 43, "900000000004", "runner_stress_04", "stress-04"),
    Target("433600607", "Exness-MT5Trial7", 44, "900000000005", "runner_stress_05", "stress-05"),
    Target("463420482", "Exness-MT5Trial17", 45, "900000000006", "runner_stress_06", "stress-06"),
    Target("413776411", "Exness-MT5Trial6", 46, "900000000007", "runner_stress_07", "stress-07"),
    Target("415724885", "Exness-MT5Trial14", 48, "900000000009", "runner_stress_09", "stress-09"),
    Target("463420479", "Exness-MT5Trial17", 51, "900000000012", "runner_stress_12", "stress-12"),
    Target("433600596", "Exness-MT5Trial7", 52, "900000000013", "runner_stress_13", "stress-13"),
    Target("433600595", "Exness-MT5Trial7", 53, "900000000014", "runner_stress_14", "stress-14"),
    Target("463420478", "Exness-MT5Trial17", 54, "900000000015", "runner_stress_15", "stress-15"),
    Target("463420476", "Exness-MT5Trial17", 55, "900000000016", "runner_stress_16", "stress-16"),
    Target("433600592", "Exness-MT5Trial7", 56, "900000000017", "runner_stress_17", "stress-17"),
]


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def tma_header(*, bot_token: str, telegram_id: str, username: str) -> str:
    user = {
        "id": int(telegram_id),
        "first_name": username or "stress",
        "username": username or None,
        "language_code": "vi",
    }
    params = {
        "auth_date": str(int(time.time())),
        "query_id": f"codex_stress_{telegram_id}_{int(time.time())}",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return "tma " + parse.urlencode(params)


class Api:
    def __init__(self, *, base_url: str, bot_token: str, backend_api_key: str):
        self.base_url = base_url.rstrip("/")
        self.bot_token = bot_token
        self.backend_api_key = backend_api_key

    def _request(
        self,
        method: str,
        path: str,
        *,
        target: Target | None = None,
        admin: bool = False,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        if query:
            url += "?" + parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if admin:
            headers["X-Backend-Api-Key"] = self.backend_api_key
        elif target is not None:
            headers["Authorization"] = tma_header(
                bot_token=self.bot_token,
                telegram_id=target.telegram_id,
                username=target.username,
            )
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": raw}
            return {
                "ok": False,
                "status": exc.code,
                "error": payload.get("detail") or payload.get("error") or payload.get("message") or raw[:200],
                "payload": payload,
            }
        except Exception as exc:
            return {"ok": False, "status": 0, "error": str(exc), "payload": {}}
        if not raw:
            return {"ok": True}
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw}
        if isinstance(payload, dict):
            payload.setdefault("ok", True)
            return payload
        return {"ok": True, "payload": payload}

    def get(self, path: str, *, target: Target, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, target=target, query=query)

    def post(self, path: str, *, target: Target | None = None, admin: bool = False, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", path, target=target, admin=admin, body=body or {})


def summarize_target(target: Target) -> str:
    return f"{target.note} account_id={target.account_id} login={target.login} server={target.server}"


def active_deployments(api: Api, target: Target) -> list[dict[str, Any]]:
    resp = api.get("/api/v2/deployments", target=target)
    if not resp.get("ok"):
        return []
    items = resp.get("items") or []
    return [
        item for item in items
        if str(item.get("status") or "").lower() in {"start_requested", "starting", "running", "stop_requested"}
        and int(item.get("account_id") or 0) == int(target.account_id)
    ]


def read_password_from_stdin() -> str:
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        raise RuntimeError("missing_password_on_stdin")
    return password


def ensure_entitlement(api: Api, target: Target, *, partner_id: str, duration_days: int) -> str:
    ent = api.get("/api/v2/miniapp/bot-token/entitlements", target=target, query={"account_id": target.account_id})
    if ent.get("ok"):
        for item in ent.get("items") or []:
            if str(item.get("bot_code") or "").lower() == "gsalgovip" and str(item.get("status") or "").lower() == "active":
                entitlement_id = str(item.get("entitlement_id") or "").strip()
                if entitlement_id:
                    return entitlement_id

    issued = api.post(
        "/api/v2/admin/maintenance/bot-tokens/issue",
        admin=True,
        body={
            "partner_id": partner_id,
            "bot_code": "gsalgovip",
            "duration_days": duration_days,
            "issued_by_telegram_id": "5573261363",
            "issued_to_note": f"codex stress start {target.note} {target.login}",
            "metadata": {
                "source": "codex_stress_login_start",
                "account_id": target.account_id,
                "login": target.login,
                "server": target.server,
            },
        },
    )
    if not issued.get("ok"):
        raise RuntimeError(f"issue_token_failed {target.note}: {issued.get('error')}")
    raw_token = str(issued.get("raw_token") or "").strip()
    if not raw_token:
        raise RuntimeError(f"issue_token_missing_raw {target.note}")

    claimed = api.post(
        "/api/v2/miniapp/bot-token/claim",
        target=target,
        body={
            "account_id": target.account_id,
            "bot_name": "gsalgovip",
            "token": raw_token,
        },
    )
    if not claimed.get("ok"):
        raise RuntimeError(f"claim_token_failed {target.note}: {claimed.get('error')}")
    entitlement_id = str((claimed.get("entitlement") or {}).get("entitlement_id") or "").strip()
    if not entitlement_id:
        raise RuntimeError(f"claim_token_missing_entitlement {target.note}")
    return entitlement_id


def request_login_slot(api: Api, target: Target) -> dict[str, Any]:
    resp = api.post(f"/api/v2/accounts/{target.account_id}/login-slot", target=target)
    if not resp.get("ok"):
        raise RuntimeError(f"login_slot_failed {target.note}: {resp.get('error')}")
    return resp


def poll_login_slots(api: Api, reservations: dict[int, tuple[Target, int]], *, timeout_sec: int) -> dict[int, dict[str, Any]]:
    deadline = time.time() + timeout_sec
    final: dict[int, dict[str, Any]] = {}
    while time.time() < deadline and len(final) < len(reservations):
        for account_id, (target, reservation_id) in reservations.items():
            if account_id in final:
                continue
            state = api.get(f"/api/v2/accounts/login-slots/{reservation_id}", target=target)
            status = str(state.get("status") or state.get("login_state") or "").strip().lower()
            if status in {"verified", "ready", "failed", "expired", "released", "cancelled"}:
                final[account_id] = state
        pending = len(reservations) - len(final)
        if pending:
            print(f"login poll: {len(final)}/{len(reservations)} final, {pending} pending")
            time.sleep(5)
    for account_id, (target, reservation_id) in reservations.items():
        if account_id not in final:
            final[account_id] = {
                "ok": False,
                "status": "timeout",
                "login_reservation_id": reservation_id,
                "account_id": target.account_id,
            }
    return final


def start_bot(api: Api, target: Target, entitlement_id: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "account_id": target.account_id,
        "bot_name": "gsalgovip",
        "mode": "live",
        "lot_size": 0.01,
        "stop_loss": 5,
        "take_profit": 5,
        "trading_unit": "price_distance",
        "dca_enabled": True,
    }
    if entitlement_id:
        body["entitlement_id"] = entitlement_id
    return api.post("/api/v2/deployments/start", target=target, body=body)


def connect_targets(api: Api, *, password: str) -> dict[int, tuple[Target, int]]:
    reservations: dict[int, tuple[Target, int]] = {}
    for target in TARGETS:
        if active_deployments(api, target):
            print(f"connect skip active: {summarize_target(target)}")
            continue
        resp = api.post(
            "/api/v2/accounts/connect",
            target=target,
            body={
                "broker": "Exness",
                "server": target.server,
                "login": target.login,
                "password": password,
                "label": f"stress {target.note}",
            },
        )
        if not resp.get("ok"):
            print(f"connect rejected: {summarize_target(target)} status={resp.get('status')} error={resp.get('error')}")
            continue
        account_id = int(resp.get("account_id") or target.account_id)
        reservation_id = int(resp.get("login_reservation_id") or 0)
        if reservation_id > 0:
            reservations[account_id] = (
                Target(target.login, target.server, account_id, target.telegram_id, target.username, target.note),
                reservation_id,
            )
            print(
                f"connect/login requested: {target.note} account_id={account_id} login={target.login} "
                f"reservation={reservation_id} runner={resp.get('runner_id')} slot={resp.get('slot_id')}"
            )
        else:
            print(f"connect ok without reservation: {target.note} account_id={account_id} login={target.login}")
    return reservations


def _wanted_pairs() -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for target in TARGETS:
        key = (target.login, target.server)
        if key not in seen:
            seen.add(key)
            pairs.append(key)
    return pairs


def _stress_config() -> dict[str, Any]:
    return {
        "trading": {
            "lot_size": 0.01,
            "stop_loss": 5,
            "take_profit": 5,
            "trading_unit": "price_distance",
            "dca_enabled": True,
            "schema_version": 1,
        }
    }


def _select_accounts_for_internal_stress(repo: Any) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []

    def _do(_con: Any, cur: Any) -> list[dict[str, Any]]:
        rows_out: list[dict[str, Any]] = []
        for login, server in _wanted_pairs():
            cur.execute(
                """
                SELECT
                    ba.id AS account_id,
                    ba.user_id,
                    u.telegram_id,
                    u.username,
                    ba.login,
                    ba.server,
                    ba.status,
                    ba.is_active,
                    COALESCE(NULLIF(BTRIM(ace.password_encrypted), ''), '') <> '' AS has_credential,
                    d.id AS active_deployment_id,
                    d.status AS active_deployment_status
                FROM broker_accounts ba
                JOIN users u ON u.id = ba.user_id
                LEFT JOIN account_credentials_encrypted ace ON ace.account_id = ba.id
                LEFT JOIN LATERAL (
                    SELECT id, status
                    FROM bot_deployments
                    WHERE account_id = ba.id
                      AND status IN ('start_requested', 'starting', 'running', 'stop_requested')
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                ) d ON TRUE
                WHERE TRIM(ba.login) = %s
                  AND ba.server = %s
                ORDER BY
                    (ba.status = 'connected' AND ba.is_active = TRUE AND COALESCE(NULLIF(BTRIM(ace.password_encrypted), ''), '') <> '') DESC,
                    (d.id IS NOT NULL) DESC,
                    ba.updated_at DESC,
                    ba.id DESC
                """,
                (login, server),
            )
            candidates = [dict(row) for row in (cur.fetchall() or [])]
            chosen = candidates[0] if candidates else {
                "login": login,
                "server": server,
                "missing": True,
            }
            rows_out.append(chosen)
        return rows_out

    selected = repo._store._with_retry_read(_do)
    return selected or []


async def run_internal_stress_bypass() -> None:
    from app.services.control_plane_service import get_control_plane_service

    service = get_control_plane_service()
    repo = service._repo
    bot = service.get_bot(bot_name="gsalgovip", force_sync=False)
    if not bot:
        raise RuntimeError("bot_not_found:gsalgovip")

    rows = _select_accounts_for_internal_stress(repo)
    started = 0
    active = 0
    skipped = 0
    for row in rows:
        login = str(row.get("login") or "")
        server = str(row.get("server") or "")
        prefix = f"login={login} server={server}"
        if row.get("missing"):
            print(f"stress skip missing account: {prefix}")
            skipped += 1
            continue
        account_id = int(row.get("account_id") or 0)
        user_id = int(row.get("user_id") or 0)
        if row.get("active_deployment_id"):
            print(
                f"stress already active: account_id={account_id} {prefix} "
                f"deployment={row.get('active_deployment_id')} status={row.get('active_deployment_status')}"
            )
            active += 1
            continue
        if not bool(row.get("has_credential")):
            print(f"stress skip no credential: account_id={account_id} {prefix}")
            skipped += 1
            continue
        if str(row.get("status") or "").lower() != "connected" or not bool(row.get("is_active")):
            print(f"stress skip not connected: account_id={account_id} {prefix} status={row.get('status')} active={row.get('is_active')}")
            skipped += 1
            continue

        repo.release_login_reservation(account_id=account_id, reason="codex_stress_bypass_start")
        account = repo.get_account(account_id=account_id, user_id=user_id)
        if not account:
            print(f"stress skip account unreadable: account_id={account_id} {prefix}")
            skipped += 1
            continue
        service._ensure_tradingview_subscription_for_start(account_id=account_id, bot=bot)
        try:
            result = await service._deployment_manager.start_deployment(
                user_id=user_id,
                account=account,
                bot_name="gsalgovip",
                bot_config_overrides=_stress_config(),
                mode="live",
            )
        except Exception as exc:
            print(f"stress start error: account_id={account_id} {prefix} error={exc}")
            skipped += 1
            continue
        deployment = result.get("deployment") or {}
        scheduler = result.get("scheduler") or {}
        command = result.get("command") or {}
        print(
            f"stress start queued: account_id={account_id} {prefix} "
            f"deployment={deployment.get('id')} status={deployment.get('status')} "
            f"runner={scheduler.get('runner_id')} slot={scheduler.get('slot_id')} command={command.get('command_id')}"
        )
        started += 1
    print(f"stress bypass summary: active={active} started={started} skipped={skipped}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate Mini App users: login MT5 accounts and start gsalgovip.")
    parser.add_argument("--execute", action="store_true", help="Actually issue/claim tokens, request login slots, and start bots.")
    parser.add_argument("--connect-with-password-stdin", action="store_true", help="Read one MT5 password from stdin and reconnect targets through /accounts/connect.")
    parser.add_argument("--stress-bypass-guards", action="store_true", help="Start via internal stress path, bypassing token and one-bot-per-user guards only.")
    parser.add_argument("--only-login", action="append", default=[], help="Limit the run to one or more MT5 login IDs.")
    parser.add_argument("--base-url", default=os.environ.get("BACKEND_BASE_URL", "http://127.0.0.1:8001"))
    parser.add_argument("--partner-id", default=os.environ.get("STRESS_PARTNER_ID", "p_5288715045"))
    parser.add_argument("--duration-days", type=int, default=1)
    parser.add_argument("--login-timeout-sec", type=int, default=240)
    args = parser.parse_args()

    env = read_env()
    bot_token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    backend_api_key = env.get("BACKEND_API_KEY") or os.environ.get("BACKEND_API_KEY", "")
    if not bot_token or not backend_api_key:
        print("missing TELEGRAM_BOT_TOKEN or BACKEND_API_KEY", file=sys.stderr)
        return 2

    if args.only_login:
        wanted = {str(item).strip() for item in args.only_login if str(item).strip()}
        globals()["TARGETS"] = [target for target in TARGETS if target.login in wanted]
        if not TARGETS:
            print(f"no targets matched --only-login values: {sorted(wanted)}", file=sys.stderr)
            return 2

    print("plan:")
    for target in TARGETS:
        print(" - " + summarize_target(target))
    if not args.execute:
        print("\ndry-run only. Re-run with --execute to perform the user-flow API actions.")
        return 0

    api = Api(base_url=args.base_url, bot_token=bot_token, backend_api_key=backend_api_key)

    if args.connect_with_password_stdin:
        password = read_password_from_stdin()
        reservations = connect_targets(api, password=password)
        login_states = poll_login_slots(api, reservations, timeout_sec=args.login_timeout_sec) if reservations else {}
        for account_id, state in login_states.items():
            target = reservations[account_id][0]
            status = str(state.get("status") or state.get("login_state") or "").strip().lower()
            if status in {"verified", "ready"}:
                print(f"connect login verified: {summarize_target(target)}")
            else:
                print(f"connect login not verified: {summarize_target(target)} status={status} error={state.get('last_error') or state.get('error')}")

    if args.stress_bypass_guards:
        asyncio.run(run_internal_stress_bypass())
        return 0

    to_login: list[Target] = []
    already_active: list[tuple[Target, list[dict[str, Any]]]] = []
    for target in TARGETS:
        active = active_deployments(api, target)
        if active:
            already_active.append((target, active))
            print(f"already active: {summarize_target(target)} deployments={[item.get('id') for item in active]}")
        else:
            to_login.append(target)

    reservations: dict[int, tuple[Target, int]] = {}
    for target in to_login:
        try:
            resp = request_login_slot(api, target)
            reservation_id = int(resp.get("login_reservation_id") or resp.get("id") or 0)
            if reservation_id <= 0:
                raise RuntimeError(f"missing reservation id: {resp}")
            reservations[target.account_id] = (target, reservation_id)
            print(f"login requested: {summarize_target(target)} reservation={reservation_id} runner={resp.get('runner_id')} slot={resp.get('slot_id')}")
        except Exception as exc:
            print(f"login request error: {summarize_target(target)} error={exc}")

    login_states = poll_login_slots(api, reservations, timeout_sec=args.login_timeout_sec) if reservations else {}
    verified: list[Target] = []
    for account_id, state in login_states.items():
        target = reservations[account_id][0]
        status = str(state.get("status") or state.get("login_state") or "").strip().lower()
        if status in {"verified", "ready"}:
            verified.append(target)
            print(f"login verified: {summarize_target(target)}")
        else:
            print(f"login not verified: {summarize_target(target)} status={status} error={state.get('last_error') or state.get('error')}")

    started: list[dict[str, Any]] = []
    for target in verified:
        try:
            entitlement_id = ensure_entitlement(api, target, partner_id=args.partner_id, duration_days=args.duration_days)
            resp = start_bot(api, target, entitlement_id)
            if not resp.get("ok"):
                print(f"start rejected: {summarize_target(target)} status={resp.get('status')} error={resp.get('error')}")
                continue
            deployment = resp.get("deployment") or {}
            scheduler = resp.get("scheduler") or {}
            started.append(resp)
            print(
                "start queued: "
                f"{summarize_target(target)} deployment={deployment.get('id') or resp.get('deployment_id')} "
                f"status={deployment.get('status') or resp.get('status')} runner={scheduler.get('runner_id')} slot={scheduler.get('slot_id')}"
            )
        except Exception as exc:
            print(f"start error: {summarize_target(target)} error={exc}")

    print(f"summary: active_before={len(already_active)} login_requested={len(reservations)} verified={len(verified)} started={len(started)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
