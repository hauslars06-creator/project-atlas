# ==========================================================
# Project Atlas
# File: app/api/v1/risk_settings.py
# Zweck: API-Routen fuer das Tages-Verlustlimit
#        (Circuit Breaker fuer neue Signale)
# ==========================================================

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database.daily_loss_limit_repository import (
    get_daily_loss_limit_status,
    set_daily_loss_limit,
    release_daily_loss_limit,
)
from app.database.position_limit_repository import (
    get_position_limit_status,
    set_position_limits,
    set_position_limits_stocks,
)


router = APIRouter(
    prefix="/risk",
    tags=["risk"],
)


class SetDailyLossLimitRequest(BaseModel):
    limit_usdt: float | None = None


class SetPositionLimitsRequest(BaseModel):
    max_long_trades: int | None = None
    max_short_trades: int | None = None


class SetPositionLimitsStocksRequest(BaseModel):
    max_long_trades_stocks: int | None = None
    max_short_trades_stocks: int | None = None


@router.get("/daily-loss-limit")
async def get_daily_loss_limit():
    try:
        return {
            "success": True,
            **get_daily_loss_limit_status(),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Tages-Verlustlimit-Status konnte nicht "
                "geladen werden."
            ),
        ) from exc


@router.post("/daily-loss-limit")
async def update_daily_loss_limit(
    payload: SetDailyLossLimitRequest,
):
    try:
        result = set_daily_loss_limit(payload.limit_usdt)
        return {
            "success": True,
            **result,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Tages-Verlustlimit konnte nicht "
                "gespeichert werden."
            ),
        ) from exc


@router.post("/daily-loss-limit/release")
async def release_daily_loss_limit_route():
    try:
        result = release_daily_loss_limit()
        return {
            "success": True,
            **result,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Sperre konnte nicht aufgehoben werden."
            ),
        ) from exc


@router.get("/position-limits")
async def get_position_limits():
    try:
        return {
            "success": True,
            **get_position_limit_status(),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Positionslimit-Status konnte nicht "
                "geladen werden."
            ),
        ) from exc


@router.post("/position-limits")
async def update_position_limits(
    payload: SetPositionLimitsRequest,
):
    try:
        result = set_position_limits(
            payload.max_long_trades,
            payload.max_short_trades,
        )
        return {
            "success": True,
            **result,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Positionslimit konnte nicht gespeichert "
                "werden."
            ),
        ) from exc


@router.post("/position-limits-stocks")
async def update_position_limits_stocks(
    payload: SetPositionLimitsStocksRequest,
):
    try:
        result = set_position_limits_stocks(
            payload.max_long_trades_stocks,
            payload.max_short_trades_stocks,
        )
        return {
            "success": True,
            **result,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Aktien-Positionslimit konnte nicht "
                "gespeichert werden."
            ),
        ) from exc
