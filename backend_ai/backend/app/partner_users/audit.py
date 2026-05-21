"""Structured audit log cho partner_users module.

Mọi event của khách (login, link, start, stop, force_stop) đều log qua đây.
Output đi vào `logs/backend/api.jsonl` chuẩn của Spider AI (qua logger
`api.partner_user` được structured-log handler pick up).

Khi cần điều tra: `grep -h '"event":"partner_user.' logs/backend/api*.jsonl | jq -c`
"""
from __future__ import annotations

import logging
from typing import Any


log = logging.getLogger("api.partner_user")


def _emit(event: str, **fields: Any) -> None:
    """Log 1 dòng JSON tới audit channel.

    Field naming convention: snake_case, prefix event với `partner_user.`.
    Stdlib logger được mirror sang JSONL bởi log_filters (structured_log_file).
    """
    extra = {"event": f"partner_user.{event}"}
    extra.update({k: v for k, v in fields.items() if v is not None})
    log.info("partner_user.%s", event, extra=extra)


def login_ok(*, jti: str, partner_id: str, end_user_label: str | None) -> None:
    _emit("login_ok", jti=jti, partner_id=partner_id, end_user_label=end_user_label)


def link_account(*, jti: str, account_id: int, partner_id: str, end_user_label: str | None) -> None:
    _emit("link_account", jti=jti, account_id=account_id, partner_id=partner_id, end_user_label=end_user_label)


def link_denied(*, jti: str, requested_account_id: int, existing_account_id: int) -> None:
    _emit("link_denied", jti=jti, requested_account_id=requested_account_id, existing_account_id=existing_account_id)


def bot_start(*, jti: str, account_id: int, bot_id: str, partner_id: str, action: str) -> None:
    _emit("bot_start", jti=jti, account_id=account_id, bot_id=bot_id, partner_id=partner_id, action=action)


def bot_stop(*, jti: str, account_id: int, bot_id: str, partner_id: str, action: str) -> None:
    _emit("bot_stop", jti=jti, account_id=account_id, bot_id=bot_id, partner_id=partner_id, action=action)


def force_stop(*, jti: str, account_id: int | None, reason: str, action: str, dm_sent: bool = False) -> None:
    _emit("force_stop", jti=jti, account_id=account_id, reason=reason, action=action, dm_sent=dm_sent)
