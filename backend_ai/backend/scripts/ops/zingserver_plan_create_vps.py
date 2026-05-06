#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.providers.zingserver import ZingServerClient, ZingServerError, build_zingserver_create_vps_plan
from app.providers.zingserver.probe import scrub_zingserver_payload
from app.settings import settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run a ZingServer create-VPS plan for a runner node.")
    parser.add_argument("--datacenter", default=str(getattr(settings, "ZINGSERVER_DEFAULT_DATACENTER", "") or ""))
    parser.add_argument("--plan-id", default=str(getattr(settings, "ZINGSERVER_DEFAULT_PLAN_ID", "") or ""))
    parser.add_argument("--os-id", type=int, default=int(getattr(settings, "ZINGSERVER_DEFAULT_OS_ID", 0) or 0))
    parser.add_argument("--location-id", default=str(getattr(settings, "ZINGSERVER_DEFAULT_LOCATION_ID", "") or ""))
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--period", default=str(getattr(settings, "ZINGSERVER_DEFAULT_PERIOD", "monthly") or "monthly"))
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--coupon", default="")
    parser.add_argument("--auto-renew", action="store_true")
    parser.add_argument("--no-install-chrome", action="store_true")
    parser.add_argument("--install-firefox", action="store_true")
    parser.add_argument(
        "--max-active-clouds",
        type=int,
        default=int(getattr(settings, "ZINGSERVER_MAX_ACTIVE_CLOUDS", 3) or 3),
    )
    parser.add_argument(
        "--max-create-quantity",
        type=int,
        default=int(getattr(settings, "ZINGSERVER_MAX_CREATE_QUANTITY", 1) or 1),
    )
    parser.add_argument(
        "--max-total-cost-vnd",
        type=int,
        default=int(getattr(settings, "ZINGSERVER_MAX_CREATE_COST_VND", 2000000) or 2000000),
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser.parse_args(argv)


def _missing_token_report() -> dict[str, object]:
    return {
        "ok": False,
        "mode": "dry_run",
        "post_called": False,
        "would_call": "POST /cloud/create-vps",
        "ok_to_create": False,
        "blockers": ["zingserver_api_token_missing"],
        "hint": "Set ZINGSERVER_API_TOKEN in backend env/.env. Do not paste it into logs.",
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = str(getattr(settings, "ZINGSERVER_API_TOKEN", "") or "").strip()
    if not token:
        print(json.dumps(_missing_token_report(), ensure_ascii=False), file=sys.stderr)
        return 2

    client = ZingServerClient(
        base_url=str(getattr(settings, "ZINGSERVER_API_BASE_URL", "") or "https://api.zingserver.com"),
        access_token=token,
        timeout_sec=float(getattr(settings, "ZINGSERVER_API_TIMEOUT_SEC", 15.0) or 15.0),
    )
    try:
        report = build_zingserver_create_vps_plan(
            client,
            datacenter=args.datacenter,
            plan_ref=args.plan_id,
            os_id=args.os_id,
            location_id=args.location_id,
            runner_id=args.runner_id,
            period=args.period,
            quantity=args.quantity,
            auto_renew=args.auto_renew,
            coupon=args.coupon,
            install_chrome=not args.no_install_chrome,
            install_firefox=args.install_firefox,
            max_active_clouds=args.max_active_clouds,
            max_create_quantity=args.max_create_quantity,
            max_total_cost_vnd=args.max_total_cost_vnd,
        )
    except ZingServerError as exc:
        report = {
            "ok": False,
            "error": str(exc),
            "mode": "dry_run",
            "post_called": False,
            "would_call": "POST /cloud/create-vps",
            "ok_to_create": False,
        }
        print(json.dumps(scrub_zingserver_payload(report), ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(scrub_zingserver_payload(report), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
