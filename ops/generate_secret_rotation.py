#!/usr/bin/env python3
"""Generate a go-live secret rotation bundle.

This script intentionally does not apply changes to production. It creates
patch files that operators can review, push to Linux/Windows/Vercel, then roll
out in a planned maintenance window.
"""
from __future__ import annotations

import argparse
import os
import secrets
import stat
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROTATION_DIR = ROOT / "ops" / "rotation"


def token_urlsafe(size: int = 48) -> str:
    return secrets.token_urlsafe(size)


def token_hex(size: int = 32) -> str:
    return secrets.token_hex(size)


def rotation_values() -> dict[str, str]:
    redis_password = token_hex(32)
    postgres_password = token_urlsafe(24)
    return {
        "APP_SECRET_KEY": token_urlsafe(64),
        "BACKEND_API_KEY": token_urlsafe(48),
        "PARTNER_USER_JWT_SECRET": token_urlsafe(48),
        "PARTNER_USER_INTERNAL_KEY": token_urlsafe(32),
        "TRADINGVIEW_WEBHOOK_SECRET": token_hex(32),
        "TELEGRAM_WEBHOOK_SECRET_TOKEN": token_urlsafe(32),
        "LOCAL_REDIS_PASSWORD": redis_password,
        "POSTGRES_PASSWORD": postgres_password,
        "LOCAL_POSTGRES_PASSWORD": postgres_password,
    }


def linux_env_patch(values: dict[str, str]) -> str:
    lines = [
        "# Generated secret rotation patch for Linux backend/root .env.",
        "# Review, apply during maintenance, then restart backend stack.",
    ]
    for key, value in values.items():
        lines.append(f"{key}={value}")
    lines.extend(
        [
            "",
            "# External provider secrets must be rotated at the provider first, then pasted manually:",
            "# TELEGRAM_BOT_TOKEN=<new BotFather token>",
            "# SYSTEM_BOT_TOKEN=<new BotFather token if still used>",
            "# GEMINI_API_KEY=<new Google AI Studio/API key>",
            "",
            "# Redis URLs derived from LOCAL_REDIS_PASSWORD:",
            f"REDIS_URL=redis://:{values['LOCAL_REDIS_PASSWORD']}@redis:6379/0",
            f"REDIS_WRITE_URL=redis://:{values['LOCAL_REDIS_PASSWORD']}@redis:6379/0",
            f"BOT_COMMAND_QUEUE_REDIS_URL=redis://:{values['LOCAL_REDIS_PASSWORD']}@redis:6379/0",
        ]
    )
    return "\n".join(lines) + "\n"


def windows_env_patch(values: dict[str, str]) -> str:
    redis_password = values["LOCAL_REDIS_PASSWORD"]
    return "\n".join(
        [
            "# Generated secret rotation patch for Windows runner env.",
            "# Apply after Linux backend accepts the new keys.",
            f"BACKEND_API_KEY={values['BACKEND_API_KEY']}",
            f"REDIS_PASSWORD={redis_password}",
            f"REDIS_URL=redis://:{redis_password}@127.0.0.1:6380/0",
            f"BOT_COMMAND_QUEUE_REDIS_URL=redis://:{redis_password}@127.0.0.1:6380/0",
            "",
        ]
    )


def rollout_checklist() -> str:
    return "\n".join(
        [
            "# Secret Rotation Rollout Checklist",
            "",
            "1. Create new external secrets first: BotFather tokens, Gemini/API keys, Vercel env vars if used.",
            "2. Apply Linux .env patch during maintenance.",
            "3. If rotating Postgres password, ALTER USER in Postgres before restarting app containers.",
            "4. Restart Redis with the new requirepass and update Linux Redis URLs.",
            "5. Update Windows runner env with BACKEND_API_KEY and Redis password/URL.",
            "6. Restart Linux backend stack.",
            "7. Restart Windows runner services.",
            "8. Run: python ops/chaos_test_control_plane.py readonly",
            "9. Test one login, one START_BOT, one STOP_BOT, and one webhook CLOSE on demo.",
            "10. Delete old secrets from shell history, chat notes, dashboards, and unused env stores.",
            "",
        ]
    )


def write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROTATION_DIR))
    parser.add_argument("--print-paths", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    values = rotation_values()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    linux_path = out_dir / f"{stamp}.linux.env.patch"
    windows_path = out_dir / f"{stamp}.windows-runner.env.patch"
    checklist_path = out_dir / f"{stamp}.rollout-checklist.md"
    write_private(linux_path, linux_env_patch(values))
    write_private(windows_path, windows_env_patch(values))
    checklist_path.write_text(rollout_checklist(), encoding="utf-8")
    checklist_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"linux_env_patch={linux_path}")
    print(f"windows_env_patch={windows_path}")
    print(f"rollout_checklist={checklist_path}")
    if not args.print_paths:
        print("No production files were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
