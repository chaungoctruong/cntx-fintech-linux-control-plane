from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v2.control_plane_deps import service_dep, translate_control_plane_error, user_dep
from app.schemas.control_plane import BotSelectRequest
from app.services.control_plane_service import MT5ControlPlaneService

router = APIRouter(prefix="/bots", tags=["mt5-bots"])


@router.get("")
async def list_bots(
    force_sync: bool = Query(default=False),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Tra ve catalog bot day du de FE render rich card.

    Moi item co cac field user-facing duoi day (FE Mini App nen render het):
      - bot_code, bot_name, display_name, language, version
      - profile_class ("light" | "normal" | "heavy") -> badge muc nang
      - risk_profile (JSON: max_drawdown, max_volume, max_concurrent_orders, ...)
      - strategy_tags ([str]) -> pill mau theo nhom strategy
      - indicator_requirements ([str]) -> hien thi list indicator
      - resource_hints (JSON) -> doi chieu voi runner capability
      - supports_demo / supports_live (bool)
      - default_config_path, runtime_entry, runtime_env (chu yeu cho debugging)
      - checksum, source_path (de hien version + signature trust)
    """
    return {"items": service.list_bots(force_sync=force_sync)}


@router.get("/{bot_name}")
async def get_bot(
    bot_name: str,
    force_sync: bool = Query(default=False),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    """Tra ve 1 bot voi cau truc giong list_bots[i]. 404 bot_not_found neu khong ton tai."""
    bot = service.get_bot(bot_name=bot_name, force_sync=force_sync)
    if not bot:
        raise translate_control_plane_error(ValueError("bot_not_found"))
    return bot


@router.post("/select")
async def select_bot(
    payload: BotSelectRequest,
    user: dict = Depends(user_dep),
    service: MT5ControlPlaneService = Depends(service_dep),
) -> dict:
    try:
        draft = service.select_bot(
            telegram_id=str(user["telegram_id"]),
            username=user.get("username"),
            account_id=payload.account_id,
            bot_name=payload.bot_name,
            bot_config_overrides=payload.merged_bot_config_overrides(),
        )
    except Exception as exc:
        raise translate_control_plane_error(exc) from exc
    return draft
