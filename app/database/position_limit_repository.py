# ==========================================================
# Project Atlas
# File: app/database/position_limit_repository.py
# Zweck: Datenbankzugriffe fuer das Long/Short-Positions-
#        limit (max. gleichzeitig offene Trades je Richtung),
#        getrennt fuer Krypto-Perpetuals und Aktien-Futures.
# ==========================================================

from __future__ import annotations

from typing import Any

from app.database.database import SessionLocal
from app.database.models import OpenTrade, PositionLimitSetting


# Symbole, die als "Aktien-Futures" gelten (alle anderen
# gelten als Krypto-Perpetual). Bei Bedarf hier ergaenzen,
# wenn weitere Aktien-Symbole in "Signale anlegen" dazukommen.
STOCK_SYMBOLS = {
    "PYPLUSDT",
    "METAUSDT",
    "TSLAUSDT",
    "NVDAUSDT",
    "AMZNUSDT",
    "MSFTUSDT",
    "AMDUSDT",
    "GOOGLUSDT",
    "AAPLUSDT",
    "HOODUSDT",
    "CRCLUSDT",
    "AVGOUSDT",
    "MUUSDT",
    "INTCUSDT",
    "SPCXUSDT",
    "SNDKUSDT",
    "DELLUSDT",
    "PLTRUSDT",
    "NBISUSDT",
}


def _is_stock_symbol(symbol: str) -> bool:
    return str(symbol or "").strip().upper() in STOCK_SYMBOLS


def _get_or_create_row(db) -> PositionLimitSetting:
    row = db.query(PositionLimitSetting).filter(
        PositionLimitSetting.id == 1
    ).first()

    if row is None:
        row = PositionLimitSetting(
            id=1,
            max_long_trades=None,
            max_short_trades=None,
            max_long_trades_stocks=None,
            max_short_trades_stocks=None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    return row


def _count_open(
    db, *, direction: str, stocks: bool
) -> int:
    query = db.query(OpenTrade).filter(
        OpenTrade.status == "OPEN",
        OpenTrade.direction == direction,
        OpenTrade.signal_id != "EXTERNAL",
        OpenTrade.is_locked.is_(False),
    )

    if stocks:
        query = query.filter(
            OpenTrade.symbol.in_(STOCK_SYMBOLS)
        )
    else:
        query = query.filter(
            OpenTrade.symbol.notin_(STOCK_SYMBOLS)
        )

    return query.count()


def get_position_limit_status() -> dict[str, Any]:
    """
    Liefert die konfigurierten Limits sowie die aktuell
    offenen Long-/Short-Positionen (nur signalbasiert, ohne
    manuelle/externe Trades), getrennt fuer Krypto-
    Perpetuals und Aktien-Futures.
    """

    db = SessionLocal()

    try:
        row = _get_or_create_row(db)

        open_long_count = _count_open(
            db, direction="LONG", stocks=False
        )
        open_short_count = _count_open(
            db, direction="SHORT", stocks=False
        )

        open_long_count_stocks = _count_open(
            db, direction="LONG", stocks=True
        )
        open_short_count_stocks = _count_open(
            db, direction="SHORT", stocks=True
        )

        return {
            "max_long_trades": row.max_long_trades,
            "max_short_trades": row.max_short_trades,
            "open_long_count": open_long_count,
            "open_short_count": open_short_count,
            "is_long_blocked": (
                row.max_long_trades is not None
                and open_long_count >= row.max_long_trades
            ),
            "is_short_blocked": (
                row.max_short_trades is not None
                and open_short_count >= row.max_short_trades
            ),
            "max_long_trades_stocks": (
                row.max_long_trades_stocks
            ),
            "max_short_trades_stocks": (
                row.max_short_trades_stocks
            ),
            "open_long_count_stocks": open_long_count_stocks,
            "open_short_count_stocks": (
                open_short_count_stocks
            ),
            "is_long_blocked_stocks": (
                row.max_long_trades_stocks is not None
                and open_long_count_stocks
                >= row.max_long_trades_stocks
            ),
            "is_short_blocked_stocks": (
                row.max_short_trades_stocks is not None
                and open_short_count_stocks
                >= row.max_short_trades_stocks
            ),
        }

    finally:
        db.close()


def set_position_limits(
    max_long_trades: int | None,
    max_short_trades: int | None,
) -> dict[str, Any]:
    """Setzt das Krypto-Perpetual-Limit."""

    db = SessionLocal()

    try:
        row = _get_or_create_row(db)
        row.max_long_trades = max_long_trades
        row.max_short_trades = max_short_trades
        db.commit()
    finally:
        db.close()

    return get_position_limit_status()


def set_position_limits_stocks(
    max_long_trades_stocks: int | None,
    max_short_trades_stocks: int | None,
) -> dict[str, Any]:
    """Setzt das Aktien-Futures-Limit."""

    db = SessionLocal()

    try:
        row = _get_or_create_row(db)
        row.max_long_trades_stocks = max_long_trades_stocks
        row.max_short_trades_stocks = max_short_trades_stocks
        db.commit()
    finally:
        db.close()

    return get_position_limit_status()


def check_position_limit_blocked(
    direction: str, symbol: str | None = None
) -> bool:
    """
    Wird vor dem Eroeffnen eines neuen, signalbasierten
    Trades aufgerufen. Prueft, ob die konfigurierte Maximal-
    anzahl offener Trades fuer die angegebene Richtung
    (LONG/SHORT) bereits erreicht ist - getrennt fuer
    Krypto-Perpetuals und Aktien-Futures je nach `symbol`.

    Rueckgabe: True = gesperrt (Signal ablehnen),
               False = nicht gesperrt (Signal verarbeiten).
    """

    normalized_direction = str(direction or "").strip().upper()

    if normalized_direction not in ("LONG", "SHORT"):
        return False

    status = get_position_limit_status()
    is_stock = _is_stock_symbol(symbol) if symbol else False

    if is_stock:
        if normalized_direction == "LONG":
            return status["is_long_blocked_stocks"]
        return status["is_short_blocked_stocks"]

    if normalized_direction == "LONG":
        return status["is_long_blocked"]

    return status["is_short_blocked"]
