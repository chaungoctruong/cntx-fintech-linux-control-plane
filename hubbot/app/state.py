# -*- coding: utf-8 -*-
"""User state for main.py flow."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from telegram.ext import ContextTypes


@dataclass
class UserState:
    profile_id: Optional[str] = None
    last_ai_ts: float = 0.0
    last_button_ts: float = 0.0


def st(context: ContextTypes.DEFAULT_TYPE) -> UserState:
    ud = context.user_data
    if not isinstance(ud.get("state"), UserState):
        ud["state"] = UserState()
    return ud["state"]
