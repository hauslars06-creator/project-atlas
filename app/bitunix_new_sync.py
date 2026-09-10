# ==========================================================
# Project Atlas
# File: app/bitunix_new_sync.py
# Zweck: Hintergrund-Job, der offene Trades des NEUEN
#        Bitunix-Kontos (exchange="BITUNIX_NEW") laufend
#        mit den tatsaechlichen Positionen abgleicht -
#        Live-PnL/ROI aktualisieren und geschlossene
#        Positionen sauber in die Trade-Historie verschieben.
#
# Bewusst als EIGENSTAENDIGER, einfacher Loop getrennt vom
# grossen trade_sync.py (das weiterhin exklusiv fuer das
# ALTE Konto / manuelle Trades zustaendig bleibt, inkl.
# Multi-TP/SL und automatischem Break-Even). Dieses neue
# Konto handelt ausschliesslich automatisierte Signale mit
# einfachem TP/SL - braucht daher NICHT die volle
# Multi-TP/SL-Raffinesse des Hauptsyncs.
# ==========================================================

from __future__ import annotations

import asyncio
import logging
import os

from app.database.trade_repository import (
    get_all_open_trades,
    move_open_trade_to_history,
    update_open_trade_live_data,
)
from app.exchanges.bitunix import BitunixClient

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 15

REQUIRED_MISSING_CHECKS = 2

_missing_checks: dict[str, int] = {}


def _new_account_client() -> BitunixClient:
    return BitunixClient(
        api_key=os.getenv("BITUNIX_NEW_API_KEY"),
        api_secret=os.getenv("BITUNIX_NEW_API_SECRET"),
    )


async def synchronize_bitunix_new_trades() -> dict:
    """
    Vergleicht alle offenen Atlas-Trades mit
    exchange="BITUNIX_NEW" gegen die tatsaechlichen offenen
    Positionen des neuen Bitunix-Kontos.
    """
    client = _new_account_client()

    positions_response = await client.get_pending_positions()
    if int(positions_response.get("code", -1)) != 0:
        raise RuntimeError(
            f"Bitunix (neues Konto) Positionsabfrage "
            f"fehlgeschlagen: {positions_response}"
        )

    positions = positions_response.get("data") or []
    position_lookup = {
        str(position["positionId"]): position
        for position in positions
        if position.get("positionId")
    }
    new_account_position_ids = set(position_lookup.keys())

    all_trades = get_all_open_trades()
    new_account_trades = [
        trade
        for trade in all_trades
        if str(
            getattr(trade, "exchange", "BITUNIX") or "BITUNIX"
        ).strip().upper()
        == "BITUNIX_NEW"
    ]

    updated_count = 0
    archived_positions: list[str] = []

    for trade in new_account_trades:
        position_id = str(trade.position_id)

        if position_id in new_account_position_ids:
            position = position_lookup[position_id]

            try:
                unrealized_pnl = float(
                    position.get("unrealizedPNL", 0) or 0
                )
                realized_pnl = float(
                    position.get("realizedPNL", 0) or 0
                )
                margin = float(position.get("margin", 0) or 0)
                avg_open_price = float(
                    position.get("avgOpenPrice", 0) or 0
                )
                quantity = float(position.get("qty", 0) or 0)
                exchange_side = str(
                    position.get("side", "")
                ).strip().upper()
                liquidation_raw = position.get("liqPrice")

                if quantity > 0 and avg_open_price > 0:
                    price_difference = unrealized_pnl / quantity
                    if exchange_side == "BUY":
                        current_price = (
                            avg_open_price + price_difference
                        )
                    elif exchange_side == "SELL":
                        current_price = (
                            avg_open_price - price_difference
                        )
                    else:
                        current_price = avg_open_price
                else:
                    current_price = avg_open_price

                pnl_percent = (
                    unrealized_pnl / margin * 100
                    if margin > 0
                    else 0
                )
                liquidation_price = (
                    float(liquidation_raw)
                    if liquidation_raw not in (None, "")
                    else None
                )

                update_open_trade_live_data(
                    position_id,
                    current_price=current_price,
                    liquidation_price=liquidation_price,
                    unrealized_pnl=unrealized_pnl,
                    realized_pnl=realized_pnl,
                    pnl_percent=pnl_percent,
                    current_margin=(
                        margin if margin > 0 else None
                    ),
                )
                updated_count += 1
            except Exception:
                logger.exception(
                    "Bitunix (neues Konto) Live-Sync "
                    "fehlgeschlagen fuer position_id=%s",
                    position_id,
                )

            _missing_checks.pop(position_id, None)
            continue

        missing_count = (
            _missing_checks.get(position_id, 0) + 1
        )
        _missing_checks[position_id] = missing_count

        if missing_count < REQUIRED_MISSING_CHECKS:
            continue

        exact_exit_price = None
        exact_pnl_usdt = None

        try:
            closed_position = await client.get_history_position(
                position_id
            )

            if closed_position is not None:
                close_price_raw = closed_position.get(
                    "closePrice"
                )
                realized_pnl_raw = closed_position.get(
                    "realizedPNL"
                )

                if close_price_raw not in (None, ""):
                    exact_exit_price = float(close_price_raw)
                if realized_pnl_raw not in (None, ""):
                    exact_pnl_usdt = float(realized_pnl_raw)
        except Exception:
            logger.exception(
                "Bitunix (neues Konto) Abschlusswerte fuer "
                "position_id=%s konnten nicht abgerufen "
                "werden - verwende Naeherungswert.",
                position_id,
            )

        history_entry = move_open_trade_to_history(
            position_id,
            exit_price=exact_exit_price,
            pnl_usdt=exact_pnl_usdt,
            close_reason="BITUNIX_NEW_POSITION_CLOSED",
        )

        _missing_checks.pop(position_id, None)

        if history_entry is not None:
            archived_positions.append(position_id)
            logger.info(
                "BITUNIX_NEW TRADE ARCHIVIERT | "
                "position_id=%s symbol=%s",
                position_id,
                trade.symbol,
            )

    return {
        "checked": len(new_account_trades),
        "updated": updated_count,
        "archived": archived_positions,
    }


async def bitunix_new_sync_loop() -> None:
    """
    Laeuft dauerhaft im Hintergrund und synchronisiert die
    Trades des neuen Bitunix-Kontos in einem festen Intervall.
    """
    while True:
        try:
            result = await synchronize_bitunix_new_trades()

            if result["archived"]:
                logger.info(
                    "Bitunix-Neu-Sync: %s geprueft, %s "
                    "aktualisiert, %s archiviert.",
                    result["checked"],
                    result["updated"],
                    len(result["archived"]),
                )
        except Exception:
            logger.exception(
                "Fehler bei der Bitunix-Neu-Konto-"
                "Synchronisierung."
            )

        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
