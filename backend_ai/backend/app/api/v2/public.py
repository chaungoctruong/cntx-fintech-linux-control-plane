"""
Public read-only overview endpoints for external landing pages such as cmpshift.
No user-specific data, no bot controls, no secrets.
"""
from __future__ import annotations

import copy
import re
import time
from urllib.parse import quote
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status

from app.schemas.ctrader import CTraderExchangeRequest
from app.services.broker import CTraderBrokerApiClient
from app.services.store_service import get_store
from app.settings import settings

router = APIRouter(prefix="/public", tags=["public-v2"])

_OVERVIEW_CACHE: tuple[float, dict[str, Any]] | None = None


def _translate_ctrader_public_bridge_error(exc: Exception) -> HTTPException:
    detail = str(exc) or exc.__class__.__name__
    if detail == "ctrader_backend_url_required":
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ctrader_service_unavailable")
    if detail == "ctrader_backend_timeout":
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="ctrader_service_timeout")
    if detail == "ctrader_backend_unreachable":
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ctrader_service_unavailable")
    if detail.startswith("ctrader_backend_http_"):
        prefix, _, downstream_detail = detail.partition(":")
        try:
            code = int(prefix.rsplit("_", 1)[-1])
        except ValueError:
            code = status.HTTP_502_BAD_GATEWAY
        if code < 400 or code > 599:
            code = status.HTTP_502_BAD_GATEWAY
        public_detail = _normalize_ctrader_public_error_detail(downstream_detail or prefix, code)
        return HTTPException(status_code=code, detail=public_detail)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _normalize_ctrader_public_error_detail(detail: str, status_code: int) -> str:
    normalized = (detail or "").strip()
    if not normalized:
        return "temporary_unavailable"
    if normalized in {"ctrader_backend_timeout", "ctrader_service_timeout"}:
        return "ctrader_service_timeout"
    if normalized in {
        "ctrader_backend_url_required",
        "ctrader_backend_unreachable",
        "ctrader_service_unavailable",
    }:
        return "ctrader_service_unavailable"
    if normalized.startswith("ctrader_backend_"):
        return "ctrader_service_unavailable"
    if status_code == status.HTTP_504_GATEWAY_TIMEOUT:
        return "ctrader_service_timeout"
    if status_code >= 500:
        return "ctrader_service_unavailable"
    return normalized


def _overview_cache_ttl_sec() -> float:
    try:
        return max(1.0, float(getattr(settings, "PUBLIC_OVERVIEW_CACHE_TTL_SEC", 15.0) or 15.0))
    except (TypeError, ValueError):
        return 15.0


def _featured_bots_limit() -> int:
    try:
        return max(1, min(12, int(getattr(settings, "PUBLIC_OVERVIEW_FEATURED_BOTS_MAX", 6) or 6)))
    except (TypeError, ValueError):
        return 6


def _public_base_url() -> str:
    return str((settings.PUBLIC_BASE_URL or "").strip()).rstrip("/")


def _asset_url(path: str) -> str:
    base = _public_base_url()
    rel = "/" + str(path or "").lstrip("/")
    if base:
        return f"{base}{rel}"
    return rel


def _telegram_entry() -> dict[str, str]:
    username = str(getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
    if not username:
        return {"url": "", "label_vi": "", "entry_type": ""}
    short_name = str(getattr(settings, "TELEGRAM_MINI_APP_SHORT_NAME", "") or "").strip().strip("/")
    start = str(getattr(settings, "CONNECT_RETURN_STARTAPP", "") or "").strip() or "bot_connected"
    if short_name:
        return {
            "url": f"https://t.me/{username}/{short_name}?startapp={start}",
            "label_vi": "Mở Telegram Mini App",
            "entry_type": "mini_app",
        }
    return {
        "url": f"https://t.me/{username}?startapp={start}",
        "label_vi": "Mở bot trên Telegram",
        "entry_type": "bot_chat",
    }


def _dashboard_url() -> str:
    base = _public_base_url()
    if not base:
        return "/"
    return f"{base}/"


def _title_token(token: str) -> str:
    token_s = str(token or "").strip()
    if not token_s:
        return ""
    if token_s.isupper() or token_s.upper() in {"XAUUSD", "BTCUSD", "ETHUSD", "EURUSD", "GBPUSD", "USDJPY", "HDF"}:
        return token_s.upper()
    if token_s.islower():
        return token_s.capitalize()
    return token_s


def _split_code_tokens(code: str) -> list[str]:
    return [part for part in re.split(r"[-_]+", str(code or "").strip()) if part]


def _market_label_vi(code: str) -> str:
    known_markets = {
        "xauusd_trading_bot": "Vàng (XAUUSD)",
        "gold_default_v1": "Vàng (XAUUSD)",
        "legacy_live_trading_bot": "Chiến lược legacy",
        "drl_xauusd_bot": "Vàng (XAUUSD)",
        "xau_ai_trading_bot": "Vàng (XAUUSD)",
        "xaubot_ai": "Vàng (XAUUSD)",
        "ai_gold_scalper": "Vàng (XAUUSD)",
        "reinforcement_learning_for_gold_trading": "Vàng (XAUUSD)",
        "rl_algo_trading": "Vàng (XAUUSD)",
        "tradingbot": "Vàng (XAUUSD)",
    }
    special = known_markets.get(str(code or "").strip().lower())
    if special:
        return special
    tokens = _split_code_tokens(code)
    if not tokens:
        return "Thị trường tự động"
    head = tokens[0].upper()
    common = {
        "XAUUSD": "Vàng (XAUUSD)",
        "BTCUSD": "Bitcoin (BTCUSD)",
        "ETHUSD": "Ethereum (ETHUSD)",
        "EURUSD": "EUR/USD",
        "GBPUSD": "GBP/USD",
        "USDJPY": "USD/JPY",
    }
    if head in common:
        return common[head]
    if len(head) == 6 and head.isalpha():
        return f"Cặp tiền {head[:3]}/{head[3:]}"
    return head


def _strategy_label_vi(code: str, strategy: str) -> str:
    strategy_s = str(strategy or "").strip()
    if strategy_s:
        return strategy_s
    tokens = _split_code_tokens(code)
    if len(tokens) <= 1:
        return "Bot giao dịch tự động"
    rest = [_title_token(token) for token in tokens[1:] if token]
    return " ".join(part for part in rest if part) or "Bot giao dịch tự động"


def _bot_label_vi(name: str, code: str) -> str:
    name_s = str(name or "").strip()
    market = _market_label_vi(code)
    strategy = _strategy_label_vi(code, "")
    if name_s and name_s != code:
        return name_s
    if strategy and strategy != "Bot giao dịch tự động":
        return f"Bot {strategy} cho {market}"
    return f"Bot giao dịch cho {market}"


def _public_tags(raw_tags: list[str], code: str) -> list[str]:
    tags: list[str] = []
    for tag in raw_tags:
        tag_s = str(tag or "").strip()
        if not tag_s:
            continue
        # Legacy provider tags are collapsed into a generic public-facing label.
        if tag_s.upper() in {"CTRADER", "TRADING API", "TRADING"}:
            tags.append("Giao dịch tự động")
        else:
            tags.append(tag_s)
    market = _market_label_vi(code)
    if market and market not in tags:
        tags.append(market)
    # Preserve order while removing duplicates for the public payload.
    seen: set[str] = set()
    out: list[str] = []
    for item in tags:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _bot_summary_vi(code: str, strategy: str) -> str:
    market = _market_label_vi(code)
    strategy_label = _strategy_label_vi(code, strategy)
    if strategy_label and strategy_label != "Bot giao dịch tự động":
        return f"Bot {strategy_label} dành cho {market}. Xem overview tại đây và mở dashboard CNTx labs để sử dụng thật."
    return f"Bot giao dịch tự động dành cho {market}. Xem overview tại đây và mở dashboard CNTx labs để sử dụng thật."


def _bot_payload(*, code: str, name: str, strategy: str, status: str, raw_tags: list[str], dashboard_url: str) -> dict[str, Any]:
    label_vi = _bot_label_vi(name, code)
    strategy_label_vi = _strategy_label_vi(code, strategy)
    market_label_vi = _market_label_vi(code)
    tags = _public_tags(raw_tags, code)
    icon_url = _asset_url("/static/img/cntx-labs-logo.svg")
    cover_image_url = _asset_url("/static/img/cntx-labs-logo.svg")
    dashboard_href = dashboard_url
    if code and dashboard_url.startswith("http"):
        dashboard_href = f"{dashboard_url}?bot={quote(code)}"
    return {
        "code": code,
        "slug": str(code or "").strip().lower(),
        "name": str(name or code).strip() or code,
        "label_vi": label_vi,
        "strategy": str(strategy or "").strip(),
        "strategy_label_vi": strategy_label_vi,
        "status": str(status or "ACTIVE").strip().upper(),
        "status_label_vi": "Sẵn sàng" if str(status or "").strip().upper() == "ACTIVE" else "Tạm ẩn",
        "market_label_vi": market_label_vi,
        "category_vi": "Bot giao dịch tự động",
        "summary_vi": _bot_summary_vi(code, strategy),
        "tags": tags,
        "raw_tags": [str(tag).strip() for tag in raw_tags if str(tag).strip()],
        "icon_url": icon_url,
        "cover_image_url": cover_image_url,
        "dashboard_url": dashboard_href,
    }


def _health_badge(service_health: dict[str, Any]) -> dict[str, Any]:
    service_online = bool(service_health.get("service_online"))
    capacity = bool(service_health.get("service_capacity_available"))
    running_bots = int(service_health.get("running_bot_runs") or 0)
    stale_runs = int(service_health.get("stale_heartbeat_runs") or 0)

    if service_online and capacity:
        status = "online"
        label_vi = "Sẵn sàng"
        description_vi = "Backend và trading runtime đang online và sẵn sàng phục vụ."
    elif service_online:
        status = "busy"
        label_vi = "Đang bận"
        description_vi = "Hệ thống vẫn online nhưng có runtime cần được kiểm tra thêm."
    else:
        status = "offline"
        label_vi = "Tạm gián đoạn"
        description_vi = "Backend chưa sẵn sàng. Vui lòng thử lại sau."

    return {
        "status": status,
        "label_vi": label_vi,
        "description_vi": description_vi,
        "running_bot_runs": running_bots,
        "stale_heartbeat_runs": stale_runs,
    }


async def _load_public_overview(request: Request) -> dict[str, Any]:
    service_health = dict(getattr(request.app.state, "service_health_status", {}) or {})
    store = get_store(request)
    rows = store.list_bot_catalog(only_enabled=True, status_filter="ACTIVE")
    dashboard_url = _dashboard_url()
    telegram_entry = _telegram_entry()
    logo_url = _asset_url("/static/img/cntx-labs-logo.svg")
    cover_image_url = _asset_url("/static/img/cntx-labs-logo.svg")
    bots: list[dict[str, Any]] = []
    for row in rows or []:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        bots.append(
            _bot_payload(
                code=code,
                name=str(row.get("name") or code).strip() or code,
                strategy=str(row.get("strategy") or "").strip(),
                status=str(row.get("status") or "ACTIVE").strip().upper(),
                raw_tags=[str(tag).strip() for tag in tags if str(tag).strip()],
                dashboard_url=dashboard_url,
            )
        )

    featured_limit = _featured_bots_limit()
    featured_bots = bots[:featured_limit]
    service_online = bool(service_health.get("service_online"))
    capacity_available = bool(service_health.get("service_capacity_available"))

    return {
        "ok": True,
        "updated_at": int(time.time()),
        "meta": {
            "read_only": True,
            "surface": "landing_overview",
            "cache_ttl_sec": int(round(_overview_cache_ttl_sec())),
            "recommended_refresh_sec": 30,
        },
        "product": {
            "name": "CNTx labs",
            "label_vi": "Trung tâm bot giao dịch CNTx labs",
            "description_vi": "Tổng quan hệ thống bot và cụm vận hành. Khi muốn sử dụng thật, user sẽ chuyển sang dashboard Mini App.",
            "icon_url": logo_url,
            "cover_image_url": cover_image_url,
            "telegram_bot_username": str(getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@"),
        },
        "system": {
            "service_online": service_online,
            "service_capacity_available": capacity_available,
            "linked_accounts": int(service_health.get("linked_accounts") or 0),
            "active_bot_runs": int(service_health.get("active_bot_runs") or 0),
            "running_bot_runs": int(service_health.get("running_bot_runs") or 0),
            "waiting_bot_runs": int(service_health.get("waiting_bot_runs") or 0),
            "error_bot_runs": int(service_health.get("error_bot_runs") or 0),
            "stale_heartbeat_runs": int(service_health.get("stale_heartbeat_runs") or 0),
            "runtime_heartbeat_grace_sec": int(service_health.get("runtime_heartbeat_grace_sec") or 0),
            "health_badge": _health_badge(service_health),
        },
        "stats": {
            "available_bots": len(bots),
            "active_bots": len(bots),
            "featured_bots": len(featured_bots),
            "linked_accounts": int(service_health.get("linked_accounts") or 0),
            "running_bot_runs": int(service_health.get("running_bot_runs") or 0),
            "service_online": service_online,
        },
        "integrations": {
        },
        "bots": bots,
        "featured_bots": featured_bots,
        "cta": {
            "dashboard_url": dashboard_url,
            "dashboard_label_vi": "Mở bảng điều khiển",
            "telegram_url": telegram_entry["url"],
            "telegram_label_vi": telegram_entry["label_vi"],
            "telegram_entry_type": telegram_entry["entry_type"],
            "telegram_mini_app_url": telegram_entry["url"] if telegram_entry["entry_type"] == "mini_app" else "",
            "telegram_mini_app_label_vi": "Mở Telegram Mini App" if telegram_entry["entry_type"] == "mini_app" else "",
        },
    }


@router.get("/cntx-labs/overview")
async def public_cntx_labs_overview(request: Request) -> dict[str, Any]:
    global _OVERVIEW_CACHE
    now = time.monotonic()
    ttl_sec = _overview_cache_ttl_sec()
    if _OVERVIEW_CACHE is not None:
        expiry, payload = _OVERVIEW_CACHE
        if expiry > now:
            return copy.deepcopy(payload)

    payload = await _load_public_overview(request)
    _OVERVIEW_CACHE = (now + ttl_sec, copy.deepcopy(payload))
    return payload


@router.post("/ctrader/callback/complete")
async def public_ctrader_callback_complete(payload: CTraderExchangeRequest) -> dict[str, Any]:
    try:
        client = CTraderBrokerApiClient()
        callback = await client.complete_callback(
            tenant_user_id=None,
            code=payload.code,
            state=payload.state,
            scope=payload.scope,
        )
    except Exception as exc:
        raise _translate_ctrader_public_bridge_error(exc) from exc

    accounts: list[dict[str, Any]] = []
    discover_error: Optional[str] = None
    tenant_user_id = str(callback.get("tenant_user_id") or "").strip()
    connection = callback.get("connection") if isinstance(callback, dict) else {}
    connection_id = str(connection.get("id") or "").strip() if isinstance(connection, dict) else ""

    if tenant_user_id and connection_id:
        try:
            discovered = await client.discover_accounts(
                tenant_user_id=tenant_user_id,
                broker_connection_id=connection_id,
            )
            raw_items = discovered.get("items") if isinstance(discovered, dict) else []
            if isinstance(raw_items, list):
                accounts = [item for item in raw_items if isinstance(item, dict)]
        except Exception as exc:
            raw_detail = str(exc) or exc.__class__.__name__
            if raw_detail.startswith("ctrader_backend_http_"):
                prefix, _, downstream_detail = raw_detail.partition(":")
                try:
                    code = int(prefix.rsplit("_", 1)[-1])
                except ValueError:
                    code = 502
                discover_error = _normalize_ctrader_public_error_detail(downstream_detail or prefix, code)
            else:
                discover_error = _normalize_ctrader_public_error_detail(raw_detail, 502)

    return {
        **callback,
        "accounts": accounts,
        "discover_error": discover_error,
    }
