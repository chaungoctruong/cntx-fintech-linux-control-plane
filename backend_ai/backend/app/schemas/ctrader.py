from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CTraderAuthorizeUrlRequest(BaseModel):
    redirect_uri: str = Field(min_length=1)
    scope: Optional[str] = None
    state: Optional[str] = None


class CTraderExchangeRequest(BaseModel):
    code: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)
    scope: Optional[str] = None
    state: Optional[str] = None


class CTraderDiscoverAccountsRequest(BaseModel):
    broker_connection_id: str = Field(min_length=1)


class CTraderSelectDefaultAccountRequest(BaseModel):
    broker_connection_id: str = Field(min_length=1)
    trading_account_id: str = Field(min_length=1)
    live_risk_confirmed: bool = False


class CTraderStartDeploymentRequest(BaseModel):
    broker_connection_id: str = Field(min_length=1)
    trading_account_id: str = Field(min_length=1)
    bot_code: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)
    live_risk_confirmed: bool = False
    force_reconnect: bool = False
    reason: Optional[str] = Field(default=None, max_length=128)


class CTraderStopDeploymentRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=128)


class CTraderEvaluateDeploymentRequest(BaseModel):
    market: dict[str, Any] = Field(default_factory=dict)
