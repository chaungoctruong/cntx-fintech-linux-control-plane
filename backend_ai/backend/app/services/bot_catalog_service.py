from __future__ import annotations

import asyncio
import json
from typing import Any

from app.repositories.bot_catalog_repository import BotCatalogRepository
from app.services.store_service import get_process_store


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _normalize_tags(raw: Any) -> list[str]:
    if isinstance(raw, dict):
        raw = raw.get("tags") or raw.get("items") or []
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                raw = parsed.get("tags") or parsed.get("items") or []
            else:
                raw = [raw]
        except Exception:
            raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = _norm(item).upper()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def _normalize_catalog_row(row: dict[str, Any]) -> dict[str, Any]:
    tags = _normalize_tags(row.get("tags"))
    return {
        "code": _norm(row.get("code") or row.get("bot_code")),
        "name": _norm(row.get("name") or row.get("bot_name")) or _norm(row.get("code") or row.get("bot_code")),
        "strategy": _norm(row.get("strategy")),
        "tags": tags,
        "enabled": bool(row.get("enabled", True)),
        "status": _norm(row.get("status") or "ACTIVE").upper() or "ACTIVE",
        "superseded_by": _norm(row.get("superseded_by")) or None,
    }


async def list_provider_bots(*, provider_tag: str) -> list[dict[str, Any]]:
    tag = _norm(provider_tag).upper()
    repo = BotCatalogRepository(get_process_store())
    rows = await asyncio.to_thread(repo.list, only_enabled=True)
    out: list[dict[str, Any]] = []
    for row in rows:
        normalized = _normalize_catalog_row(row)
        if not normalized["code"]:
            continue
        if tag and tag not in normalized["tags"]:
            continue
        if normalized["status"] not in {"ACTIVE", "DEPRECATED"}:
            continue
        out.append(normalized)
    out.sort(key=lambda item: (item["name"].lower(), item["code"].lower()))
    return out


async def validate_provider_strategy(
    strategy_key: str,
    *,
    provider_tag: str,
) -> dict[str, Any]:
    tag = _norm(provider_tag).upper()
    requested = _norm(strategy_key)
    store = get_process_store()
    available_bots = await list_provider_bots(provider_tag=tag)
    available_codes = [str(item.get("code") or "").strip() for item in available_bots if item.get("code")]

    if not requested:
        if available_bots:
            return {
                "ok": True,
                "strategy_key": available_bots[0]["code"],
                "bot": available_bots[0],
                "available_bots": available_bots,
            }
        return {
            "ok": False,
            "error": "strategy_offline",
            "detail": f"Chưa có bot nào được cấp phép cho provider {tag or 'runtime hiện tại'}.",
            "message_vi": "Hiện chưa có bot nào khả dụng cho runtime hiện tại. Vui lòng seed bot catalog trước.",
            "available_bots": [],
        }

    raw_row = await asyncio.to_thread(store.get_bot_catalog_row, requested)
    if not raw_row:
        return {
            "ok": False,
            "error": "strategy_offline",
            "detail": "Bot này không tồn tại trong bot_catalog.",
            "message_vi": "Bot này không tồn tại trên hệ thống. Vui lòng chọn bot khác.",
            "available_bots": available_codes,
        }

    row = _normalize_catalog_row(raw_row)
    if tag and tag not in row["tags"]:
        return {
            "ok": False,
            "error": "strategy_offline",
            "detail": f"Bot {requested} chưa được cấp phép cho {tag}.",
            "message_vi": "Bot này chưa được cấp cho runtime hiện tại. Vui lòng chọn bot khác.",
            "available_bots": available_codes,
        }

    if row["status"] == "RETIRED":
        superseded = row.get("superseded_by")
        if superseded:
            return {
                "ok": False,
                "error": "strategy_upgraded",
                "suggested_strategy": superseded,
                "detail": "Chiến thuật này đã có phiên bản mới hơn.",
                "message_vi": "Chiến thuật này đã có phiên bản mới hơn. Vui lòng chuyển sang bot mới.",
                "available_bots": available_codes,
            }
        return {
            "ok": False,
            "error": "strategy_retired",
            "detail": "Chiến thuật này đã ngừng hỗ trợ.",
            "message_vi": "Chiến thuật này đã ngừng hỗ trợ. Vui lòng chọn bot khác.",
            "available_bots": available_codes,
        }

    if not row["enabled"] or row["status"] not in {"ACTIVE", "DEPRECATED"}:
        return {
            "ok": False,
            "error": "strategy_offline",
            "detail": "Bot này đang không khả dụng.",
            "message_vi": "Bot này đang không khả dụng. Vui lòng chọn bot khác.",
            "available_bots": available_codes,
        }

    return {
        "ok": True,
        "strategy_key": row["code"],
        "bot": row,
        "available_bots": available_bots,
    }
