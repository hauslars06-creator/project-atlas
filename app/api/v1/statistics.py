# ==========================================================
# Project Atlas
# File: app/api/v1/statistics.py
# Milestone: M5.1
# ==========================================================

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.services.statistics_service import (
    get_period_comparison,
    get_statistics,
)


StatisticsPeriod = Literal[
    "today",
    "week",
    "month",
    "last_7_days",
    "last_14_days",
    "last_30_days",
    "all",
]


router = APIRouter(
    prefix="/statistics",
    tags=["Statistics"],
)


@router.get("/summary")
async def statistics_summary(
    period: StatisticsPeriod = Query(
        default="last_30_days",
    ),
    lock_scope: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    exchange: str | None = Query(default=None),
):
    try:
        result = get_statistics(
            period,
            lock_scope=lock_scope,
            signal_type=signal_type,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            exchange=exchange,
        )

        return {
            "success": True,
            **result,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Statistik konnte nicht berechnet werden."
            ),
        ) from exc


@router.get("/performance-by-signal")
async def performance_by_signal(
    period: StatisticsPeriod = Query(
        default="last_30_days",
    ),
    lock_scope: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
):
    try:
        result = get_statistics(
            period,
            lock_scope=lock_scope,
            signal_type=signal_type,
        )

        return {
            "success": True,
            "period": result["period"],
            "signals": (
                result["performance_by_signal"]
            ),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Signal-Performance konnte nicht "
                "berechnet werden."
            ),
        ) from exc


@router.get("/performance-by-symbol")
async def performance_by_symbol(
    period: StatisticsPeriod = Query(
        default="last_30_days",
    ),
    lock_scope: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
):
    try:
        result = get_statistics(
            period,
            lock_scope=lock_scope,
            signal_type=signal_type,
        )

        return {
            "success": True,
            "period": result["period"],
            "symbols": (
                result["performance_by_symbol"]
            ),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Symbol-Performance konnte nicht "
                "berechnet werden."
            ),
        ) from exc


@router.get("/signal-correlations")
async def signal_correlations(
    period: StatisticsPeriod = Query(
        default="last_30_days",
    ),
    lock_scope: str | None = Query(default=None),
):
    try:
        result = get_statistics(
            period,
            lock_scope=lock_scope,
        )

        return {
            "success": True,
            "period": result["period"],
            "correlations": (
                result["signal_correlations"]
            ),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Korrelationen konnten nicht "
                "berechnet werden."
            ),
        ) from exc


@router.get("/sl-optimization")
async def sl_optimization_summary():
    """
    Aggregierte SL-Optimierungsvorschlaege pro Signal,
    basierend auf den automatisch (Hintergrund-Job,
    7 Tage nach SL-Exit) gesammelten Post-Stop-Loss-
    Analysen.
    """

    from app.database.sl_analysis_repository import (
        get_sl_analysis_summary_by_signal,
    )

    try:
        summary = get_sl_analysis_summary_by_signal()

        return {
            "success": True,
            "signals": summary,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "SL-Optimierungsvorschlaege konnten nicht "
                "geladen werden."
            ),
        ) from exc


@router.get("/tp-optimization")
async def tp_optimization_summary():
    """
    Aggregierte TP-Erhoehungsvorschlaege pro Signal,
    basierend auf den automatisch (Hintergrund-Job,
    7 Tage nach TP-Exit) gesammelten Post-Take-Profit-
    Analysen. Risikobewusst: Signale mit zu hohem SL-
    Risikoanteil bekommen keinen Vorschlag.
    """

    from app.database.tp_analysis_repository import (
        get_tp_analysis_summary_by_signal,
    )

    try:
        summary = get_tp_analysis_summary_by_signal()

        return {
            "success": True,
            "signals": summary,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "TP-Optimierungsvorschlaege konnten nicht "
                "geladen werden."
            ),
        ) from exc


@router.get("/performance-by-timeframe")
async def performance_by_timeframe(
    period: StatisticsPeriod = Query(
        default="last_30_days",
    ),
    lock_scope: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
):
    try:
        result = get_statistics(
            period,
            lock_scope=lock_scope,
            signal_type=signal_type,
        )

        return {
            "success": True,
            "period": result["period"],
            "timeframes": (
                result["performance_by_timeframe"]
            ),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Timeframe-Performance konnte nicht "
                "berechnet werden."
            ),
        ) from exc


@router.get("/performance-by-direction")
async def performance_by_direction(
    period: StatisticsPeriod = Query(
        default="last_30_days",
    ),
    lock_scope: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
):
    try:
        result = get_statistics(
            period,
            lock_scope=lock_scope,
            signal_type=signal_type,
        )

        return {
            "success": True,
            "period": result["period"],
            "directions": (
                result["performance_by_direction"]
            ),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Long/Short-Vergleich konnte nicht "
                "berechnet werden."
            ),
        ) from exc


@router.get("/performance-by-weekday")
async def performance_by_weekday(
    period: StatisticsPeriod = Query(
        default="last_30_days",
    ),
    lock_scope: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
):
    try:
        result = get_statistics(
            period,
            lock_scope=lock_scope,
            signal_type=signal_type,
        )

        return {
            "success": True,
            "period": result["period"],
            "weekdays": (
                result["performance_by_weekday"]
            ),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Wochentag-Performance konnte nicht "
                "berechnet werden."
            ),
        ) from exc


@router.get("/performance-by-hour")
async def performance_by_hour(
    period: StatisticsPeriod = Query(
        default="last_30_days",
    ),
    lock_scope: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
):
    try:
        result = get_statistics(
            period,
            lock_scope=lock_scope,
            signal_type=signal_type,
        )

        return {
            "success": True,
            "period": result["period"],
            "hours": (
                result["performance_by_hour"]
            ),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Stunden-Performance konnte nicht "
                "berechnet werden."
            ),
        ) from exc


@router.get("/daily-pnl")
async def daily_pnl(
    period: StatisticsPeriod = Query(
        default="last_30_days",
    ),
    lock_scope: str | None = Query(default=None),
):
    try:
        result = get_statistics(
            period,
            lock_scope=lock_scope,
        )

        return {
            "success": True,
            "period": result["period"],
            "daily_pnl": result["daily_pnl"],
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Tägliche PnL-Daten konnten nicht "
                "berechnet werden."
            ),
        ) from exc


@router.get("/comparison")
async def statistics_comparison():
    try:
        return {
            "success": True,
            "periods": get_period_comparison(),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Zeitraumvergleich konnte nicht "
                "berechnet werden."
            ),
        ) from exc
