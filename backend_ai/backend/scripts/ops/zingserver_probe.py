#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.providers.zingserver import ZingServerClient, ZingServerError, build_zingserver_probe_report
from app.providers.zingserver.probe import scrub_zingserver_payload
from app.settings import settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only ZingServer API probe for runner VPS planning.")
    parser.add_argument("--datacenter", default="", help="Optional datacenter code, e.g. sgdc1 or sgdc3.")
    parser.add_argument("--country", default="", help="Optional country filter for datacenters, e.g. sg.")
    parser.add_argument("--state", choices=["running", "expiring", "cancelled", "all"], default="running")
    parser.add_argument("--match-ip", default="", help="Current runner VPS IP to match in cloud list.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = str(getattr(settings, "ZINGSERVER_API_TOKEN", "") or "").strip()
    if not token:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "zingserver_api_token_missing",
                    "hint": "Set ZINGSERVER_API_TOKEN in backend env/.env. Do not paste it into logs.",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    client = ZingServerClient(
        base_url=str(getattr(settings, "ZINGSERVER_API_BASE_URL", "") or "https://api.zingserver.com"),
        access_token=token,
        timeout_sec=float(getattr(settings, "ZINGSERVER_API_TIMEOUT_SEC", 15.0) or 15.0),
    )
    try:
        report = build_zingserver_probe_report(
            client,
            datacenter=args.datacenter,
            country=args.country,
            cloud_state=args.state,
            match_ip=args.match_ip,
        )
    except ZingServerError as exc:
        report = {
            "ok": False,
            "error": str(exc),
            "mode": "read_only",
        }
        print(json.dumps(scrub_zingserver_payload(report), ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(scrub_zingserver_payload(report), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
