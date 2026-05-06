# -*- coding: utf-8 -*-
"""Shutdown hooks and single-instance locking."""

from app.lifecycle.shutdown import on_shutdown
from app.lifecycle.runtime import build_runtime_hooks
from app.lifecycle.error_handlers import build_error_handlers
from app.lifecycle.single_instance import acquire_single_instance_lock, release_single_instance_lock
from app.lifecycle.alerts import (
    configure_runtime_alerts,
    maybe_send_update_ops_alert,
    notify_started,
    notify_main_crash,
)

__all__ = [
    "on_shutdown",
    "build_runtime_hooks",
    "build_error_handlers",
    "acquire_single_instance_lock",
    "release_single_instance_lock",
    "configure_runtime_alerts",
    "maybe_send_update_ops_alert",
    "notify_started",
    "notify_main_crash",
]
