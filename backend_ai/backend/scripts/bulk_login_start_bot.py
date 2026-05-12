"""Bulk connect MT5 accounts + start bot deployments (đa luồng asyncio).

Input: 1 file CSV, mỗi dòng `telegram_id,broker,server,login,password`.
Dòng bắt đầu bằng `#` hoặc rỗng được skip.

Flow giống Mini App: với mỗi account
  1) `connect_account` (sync) — ghi credential vào Postgres, account ở `pending_verification`.
  2) `start_deployment` (async) — dispatch START_BOT xuống Windows runner.
     Runner sẽ login MT5 thật bằng credential vừa ghi rồi start bot.

Chạy trong container spider-app (cần DB + Redis):

    docker compose exec spider-app bash -lc \
      'cd /app/backend_ai/backend && python -m scripts.bulk_login_start_bot \
         --file /tmp/accounts.txt --bot-name gsalgo --lot-size 0.01 --concurrency 5'

Hoặc PM2 host:

    cd /root/spider-ai/backend_ai/backend && \
      venv/bin/python -m scripts.bulk_login_start_bot --file accounts.txt
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.risk.orchestration_policy import OrchestrationPolicyError  # noqa: E402
from app.services.control_plane_service import get_control_plane_service  # noqa: E402


@dataclass
class AccountInput:
    line_no: int
    telegram_id: str
    broker: str
    server: str
    login: str
    password: str


@dataclass
class AccountResult:
    line_no: int
    telegram_id: str
    login: str
    ok: bool
    stage: str  # "connect" | "start" | "done"
    account_id: Optional[int] = None
    deployment_id: Optional[int] = None
    error: Optional[str] = None
    elapsed_ms: Optional[int] = None


def parse_accounts(path: Path) -> list[AccountInput]:
    rows: list[AccountInput] = []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        for idx, row in enumerate(reader, start=1):
            if not row:
                continue
            first = (row[0] or "").strip()
            if not first or first.startswith("#"):
                continue
            if len(row) < 5:
                raise ValueError(
                    f"line {idx}: cần đủ 5 cột telegram_id,broker,server,login,password (có {len(row)})"
                )
            telegram_id, broker, server, login, password = (c.strip() for c in row[:5])
            if not all([telegram_id, broker, server, login, password]):
                raise ValueError(f"line {idx}: có cột trống")
            rows.append(
                AccountInput(
                    line_no=idx,
                    telegram_id=telegram_id,
                    broker=broker,
                    server=server,
                    login=login,
                    password=password,
                )
            )
    return rows


async def process_one(
    sem: asyncio.Semaphore,
    service,
    item: AccountInput,
    bot_name: str,
    lot_size: float,
    mode: str,
) -> AccountResult:
    async with sem:
        t0 = time.perf_counter()
        # Step 1: connect_account (sync — chạy trong thread để không block loop)
        try:
            account = await asyncio.to_thread(
                service.connect_account,
                telegram_id=item.telegram_id,
                username=None,
                broker=item.broker,
                server=item.server,
                login=item.login,
                password=item.password,
                label=None,
            )
            account_id = int(account.get("id"))
            print(
                f"[connect ok] line={item.line_no} tg={item.telegram_id} login={item.login} "
                f"account_id={account_id} status={account.get('status')}",
                flush=True,
            )
        except OrchestrationPolicyError as exc:
            return AccountResult(
                line_no=item.line_no,
                telegram_id=item.telegram_id,
                login=item.login,
                ok=False,
                stage="connect",
                error=f"policy: {exc}",
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 — surface anything else as failure
            return AccountResult(
                line_no=item.line_no,
                telegram_id=item.telegram_id,
                login=item.login,
                ok=False,
                stage="connect",
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )

        # Step 2: start_deployment (async). lot_size đi qua bot_config_overrides theo schema gsalgo.
        try:
            start_result = await service.start_deployment(
                telegram_id=item.telegram_id,
                username=None,
                account_id=account_id,
                bot_name=bot_name,
                bot_config_overrides={"trading": {"lot_size": lot_size}},
                mode=mode,
            )
            deployment = (start_result or {}).get("deployment") or {}
            deployment_id = deployment.get("id")
            print(
                f"[start ok]   line={item.line_no} tg={item.telegram_id} login={item.login} "
                f"deployment_id={deployment_id} status={deployment.get('status')}",
                flush=True,
            )
            return AccountResult(
                line_no=item.line_no,
                telegram_id=item.telegram_id,
                login=item.login,
                ok=True,
                stage="done",
                account_id=account_id,
                deployment_id=int(deployment_id) if deployment_id is not None else None,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
        except OrchestrationPolicyError as exc:
            return AccountResult(
                line_no=item.line_no,
                telegram_id=item.telegram_id,
                login=item.login,
                ok=False,
                stage="start",
                account_id=account_id,
                error=f"policy: {exc}",
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return AccountResult(
                line_no=item.line_no,
                telegram_id=item.telegram_id,
                login=item.login,
                ok=False,
                stage="start",
                account_id=account_id,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )


async def run(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        print(f"[error] file không tồn tại: {path}", file=sys.stderr, flush=True)
        return 2

    items = parse_accounts(path)
    if not items:
        print("[error] file rỗng hoặc toàn comment", file=sys.stderr, flush=True)
        return 2

    print(
        f"[start] file={path} accounts={len(items)} bot={args.bot_name} lot={args.lot_size} "
        f"mode={args.mode} concurrency={args.concurrency}",
        flush=True,
    )

    service = get_control_plane_service()
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    tasks = [
        asyncio.create_task(
            process_one(
                sem=sem,
                service=service,
                item=item,
                bot_name=args.bot_name,
                lot_size=float(args.lot_size),
                mode=args.mode,
            )
        )
        for item in items
    ]
    results: list[AccountResult] = await asyncio.gather(*tasks)

    ok_count = sum(1 for r in results if r.ok)
    fail_count = len(results) - ok_count
    print("\n=== SUMMARY ===", flush=True)
    for r in results:
        if r.ok:
            print(
                f"  OK    line={r.line_no} tg={r.telegram_id} login={r.login} "
                f"account_id={r.account_id} deployment_id={r.deployment_id} "
                f"elapsed_ms={r.elapsed_ms}",
                flush=True,
            )
        else:
            print(
                f"  FAIL  line={r.line_no} tg={r.telegram_id} login={r.login} "
                f"stage={r.stage} account_id={r.account_id} error={r.error}",
                flush=True,
            )
    print(f"\n[done] ok={ok_count} fail={fail_count} total={len(results)}", flush=True)

    if args.json_out:
        out_path = Path(args.json_out).expanduser().resolve()
        out_path.write_text(
            json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[done] wrote {out_path}", flush=True)

    return 0 if fail_count == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bulk connect MT5 accounts + start bot deployments.")
    p.add_argument("--file", required=True, help="Path tới CSV: telegram_id,broker,server,login,password")
    p.add_argument("--bot-name", default="gsalgo", help="Bot name trong catalog (default: gsalgo)")
    p.add_argument("--lot-size", default="0.01", help="Lot size cho từng deployment (default: 0.01)")
    p.add_argument("--mode", default="live", choices=["live", "paper"], help="Deployment mode (default: live)")
    p.add_argument("--concurrency", type=int, default=5, help="Số account xử lý song song (default: 5)")
    p.add_argument("--json-out", default=None, help="Optional: ghi kết quả ra JSON file")
    return p


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(build_parser().parse_args())))
