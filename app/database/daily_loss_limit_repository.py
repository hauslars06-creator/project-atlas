# ==========================================================
# Project Atlas
# File: app/database/daily_loss_limit_repository.py
# Zweck: Datenbankzugriffe fuer das Tages-Verlustlimit
#        (Circuit Breaker fuer neue Signale)
# ==========================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database.database import SessionLocal
from app.database.models import DailyLossLimitSetting, TradeHistory


def _today_utc_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _get_or_create_row(db) -> DailyLossLimitSetting:
    row = db.query(DailyLossLimitSetting).filter(
        DailyLossLimitSetting.id == 1
    ).first()

    if row is None:
        row = DailyLossLimitSetting(
            id=1,
            limit_usdt=None,
            tripped_at=None,
            tripped_on_date=None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    return row


def get_daily_loss_limit_status() -> dict[str, Any]:
    """
    Liefert den aktuellen Stand: konfiguriertes Limit,
    heutiger realisierter PnL (nur geschlossene Trades,
    ohne manuelle/externe Trades), ob die Sperre aktiv ist,
    und setzt eine veraltete (gestrige) Sperre automatisch
    zurueck.
    """

    db = SessionLocal()

    try:
        row = _get_or_create_row(db)

        today_str = _today_utc_str()

        # Automatischer Reset: eine Sperre von einem
        # vergangenen Tag gilt nicht mehr fuer heute.
        if (
            row.tripped_on_date is not None
            and row.tripped_on_date != today_str
        ):
            row.tripped_at = None
            row.tripped_on_date = None
            db.commit()

        today_start = datetime.fromisoformat(
            today_str + "T00:00:00+00:00"
        )

        today_pnl = (
            db.query(TradeHistory)
            .filter(
                TradeHistory.closed_at >= today_start,
                TradeHistory.signal_id != "EXTERNAL",
            )
            .with_entities(TradeHistory.pnl_usdt)
            .all()
        )

        today_realized_pnl = sum(
            float(p[0]) for p in today_pnl if p[0] is not None
        )

        is_tripped = row.tripped_at is not None

        return {
            "limit_usdt": row.limit_usdt,
            "today_realized_pnl": round(today_realized_pnl, 4),
            "is_tripped": is_tripped,
            "tripped_at": (
                row.tripped_at.isoformat()
                if row.tripped_at
                else None
            ),
        }

    finally:
        db.close()


def set_daily_loss_limit(limit_usdt: float | None) -> dict[str, Any]:
    db = SessionLocal()

    try:
        row = _get_or_create_row(db)
        row.limit_usdt = limit_usdt
        db.commit()
    finally:
        db.close()

    return get_daily_loss_limit_status()


def release_daily_loss_limit() -> dict[str, Any]:
    """
    Hebt eine aktive Sperre manuell und sofort auf (auch
    innerhalb desselben Tages).
    """

    db = SessionLocal()

    try:
        row = _get_or_create_row(db)
        row.tripped_at = None
        row.tripped_on_date = None
        db.commit()
    finally:
        db.close()

    return get_daily_loss_limit_status()


def check_and_maybe_trip_daily_loss_limit() -> bool:
    """
    Wird vor dem Eroeffnen eines neuen, signalbasierten
    Trades aufgerufen. Prueft, ob das konfigurierte Tages-
    Verlustlimit erreicht ist, und sperrt bei Bedarf neue
    Signale fuer den Rest des Tages.

    Rueckgabe: True = gesperrt (Signal ablehnen),
               False = nicht gesperrt (Signal verarbeiten).
    """

    status = get_daily_loss_limit_status()

    if status["is_tripped"]:
        return True

    limit = status["limit_usdt"]

    if limit is None:
        return False

    if status["today_realized_pnl"] > float(limit):
        return False

    # Limit erreicht/unterschritten - jetzt sperren.
    db = SessionLocal()

    try:
        row = _get_or_create_row(db)
        row.tripped_at = datetime.now(timezone.utc)
        row.tripped_on_date = _today_utc_str()
        db.commit()
    finally:
        db.close()

    return True
