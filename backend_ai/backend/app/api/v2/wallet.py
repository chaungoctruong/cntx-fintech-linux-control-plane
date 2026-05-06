"""
Wallet API v2: balance, equity, deposit address, transactions, withdrawals.
All routes require Telegram Mini App auth and upsert the current user.
Uses control-plane account snapshots for balance/equity.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.v2.control_plane_deps import user_dep
from app.core.wallet_store import add_withdrawal_request, list_transactions
from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_store

router = APIRouter(prefix="/wallet", tags=["wallet-v2"])


def _control_plane_repo(request: Request) -> ControlPlaneRepository:
    return ControlPlaneRepository(get_store(request))


def _safe_float(value: Any) -> float:
    try:
        if value is None or str(value).strip() == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _get_balance_equity(telegram_id: str, repo: ControlPlaneRepository) -> tuple[float, float]:
    """Read aggregate account metrics from control-plane snapshots."""
    summary = repo.get_user_runtime_summary(telegram_id)
    if not isinstance(summary, dict):
        return 0.0, 0.0
    return _safe_float(summary.get("balance")), _safe_float(summary.get("equity"))


def _deposit_address(telegram_id: str) -> str:
    """Persistent per-user deposit identifier (USDT-BEP20 placeholder / Internal ID)."""
    return f"internal_tg_{telegram_id}"


@router.get("/info")
def get_wallet_info(
    tg_user: dict[str, Any] = Depends(user_dep),
    repo: ControlPlaneRepository = Depends(_control_plane_repo),
):
    """GET /info: balance, equity, persistent deposit address."""
    telegram_id = tg_user["telegram_id"]
    balance, equity = _get_balance_equity(telegram_id, repo)
    return {
        "balance": balance,
        "equity": equity,
        "deposit_address": _deposit_address(telegram_id),
        "currency": "USD",
    }


@router.get("/transactions")
def get_wallet_transactions(
    tg_user: dict[str, Any] = Depends(user_dep),
    limit: int = 50,
):
    """GET /transactions: recent deposits and withdrawals from DB."""
    if limit < 1 or limit > 100:
        limit = 50
    items = list_transactions(tg_user["telegram_id"], limit=limit)
    return {"transactions": items}


@router.post("/withdraw")
def post_withdraw(
    tg_user: dict[str, Any] = Depends(user_dep),
    repo: ControlPlaneRepository = Depends(_control_plane_repo),
    amount: float = Body(..., gt=0),
    wallet_address: str = Body(..., min_length=1),
):
    """POST /withdraw: validate amount and create withdrawal request (pending). Body: { amount, wallet_address }."""
    telegram_id = tg_user["telegram_id"]
    balance, _ = _get_balance_equity(telegram_id, repo)
    if amount > balance:
        raise HTTPException(400, "Insufficient balance")
    record = add_withdrawal_request(telegram_id, amount, wallet_address)
    return {"success": True, "withdrawal": record}
