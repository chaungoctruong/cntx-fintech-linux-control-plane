"""Notification preferences per user.

Stored trong `users.metadata_json.notification_preferences`. Cau truc:

  {
    "channels": {"telegram": true, "webhook": true, "email": false},
    "events": {
      "BOT_STARTED": ["telegram"],
      "BOT_STOPPED": ["telegram", "webhook"],
      "ORDER_FILLED": [],
      "ORDER_REJECTED": ["telegram", "webhook"],
      "SLOT_BROKEN": ["telegram", "webhook"],
      "circuit_breaker.trigger": ["telegram", "webhook"]
    }
  }

Mac dinh khi user chua set: tat ca event -> [telegram, webhook] (email tat default).
"""
from __future__ import annotations

from typing import Any


_KNOWN_CHANNELS = ("telegram", "webhook", "email")
_KNOWN_EVENTS = (
    "BOT_STARTED",
    "BOT_STOPPED",
    "ORDER_FILLED",
    "ORDER_REJECTED",
    "POSITION_UPDATED",
    "SLOT_DEGRADED",
    "SLOT_BROKEN",
    "circuit_breaker.trigger",
    "login_slot.completed",
    "login_slot.failed",
)


def default_preferences() -> dict[str, Any]:
    """Mac dinh: telegram + webhook bat, email tat. Tat ca event di qua telegram + webhook."""
    return {
        "channels": {"telegram": True, "webhook": True, "email": False},
        "events": {evt: ["telegram", "webhook"] for evt in _KNOWN_EVENTS},
    }


def normalize_preferences(raw: Any) -> dict[str, Any]:
    """Normalize input -> structure on dinh.

    - Drop unknown channels / unknown events.
    - Default fields neu thieu.
    - channels values: bool.
    - events values: list[str] cua channel name (chi giu nhung channel co trong _KNOWN_CHANNELS).
    """
    out = default_preferences()
    if not isinstance(raw, dict):
        return out
    raw_channels = raw.get("channels") if isinstance(raw.get("channels"), dict) else {}
    for ch in _KNOWN_CHANNELS:
        if ch in raw_channels:
            out["channels"][ch] = bool(raw_channels[ch])
    raw_events = raw.get("events") if isinstance(raw.get("events"), dict) else {}
    for evt in _KNOWN_EVENTS:
        if evt in raw_events:
            channels_list = raw_events[evt]
            if isinstance(channels_list, list):
                cleaned = []
                seen: set[str] = set()
                for c in channels_list:
                    cs = str(c or "").strip().lower()
                    if cs in _KNOWN_CHANNELS and cs not in seen:
                        cleaned.append(cs)
                        seen.add(cs)
                out["events"][evt] = cleaned
    return out


def channels_for_event(prefs: dict[str, Any], event_type: str) -> list[str]:
    """Tra ve list channel ENABLED cho 1 event_type cu the.

    Logic:
      - Doc events[event_type] -> list channel duoc bat cho event nay
      - INTERSECT voi channels{} (chi channel duoc enable globally)
      - Tra empty list neu khong co match (FE/worker se skip)
    """
    if not isinstance(prefs, dict):
        return []
    event_channels = (prefs.get("events") or {}).get(event_type) or []
    global_channels = prefs.get("channels") or {}
    if not isinstance(event_channels, list):
        return []
    return [c for c in event_channels if global_channels.get(c)]


def known_events() -> list[str]:
    return list(_KNOWN_EVENTS)


def known_channels() -> list[str]:
    return list(_KNOWN_CHANNELS)
