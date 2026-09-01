# ==========================================================
# Project Atlas
# File: app/blofin_sync.py
# Zweck: Hintergrund-Job, der offene Blofin-Trades
#        (exchange="BLOFIN") laufend mit den tatsaechlichen
#        Blofin-Positionen abgleicht - Live-PnL/ROI
#        aktualisieren und geschlossene Positionen sauber
#        in die Trade-Historie verschieben.
#
# Bewusst als EIGENSTAENDIGER Loop getrennt von
# trade_sync.py (Bitunix), damit beide Boersen unabhaengig
# voneinander funktionieren und sich nicht gegenseitig
# beeinflussen koennen.
#
# Phase 1: einfacher Abgleich, kein Multi-TP/SL-Tracking
# (Blofin-Trades haben aktuell nur ein TP und ein SL).
# ==========================================================

from __future__ import annotations

import asyncio
import logging

from app.database.trade_repository import (
    get_all_open_trades,
    move_open_trade_to_history,
    update_open_trade_live_data,
)
from app.exchanges.blofin import BlofinClient

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 20

REQUIRED_MISSING_CHECKS = 2

_missing_checks: dict[str, int] = {}


async def synchronize_blofin_trades() -> dict:
    """
    Vergleicht alle offenen Atlas-Trades mit exchange="BLOFIN"
    gegen die tatsaechlichen offenen Blofin-Positionen.
    """
    client = BlofinClient()

    positions_response = await client.get_positions()
    if str(positions_response.get("code")) != "0":
        raise RuntimeError(
            f"Blofin-Positionsabfrage fehlgeschlagen: "
            f"{positions_response}"
        )

    positions = positions_response.get("data") or []
    position_lookup = {
        str(position["positionId"]): position
        for position in positions
        if position.get("positionId")
    }
    blofin_position_ids = set(position_lookup.keys())

    all_trades = get_all_open_trades()
    blofin_trades = [
        trade
        for trade in all_trades
        if str(
            getattr(trade, "exchange", "BITUNIX") or "BITUNIX"
        ).strip().upper()
        == "BLOFIN"
    ]

    updated_count = 0
    archived_positions: list[str] = []

    for trade in blofin_trades:
        position_id = str(trade.position_id)

        if position_id in blofin_position_ids:
            position = position_lookup[position_id]

            mark_price = position.get("markPrice")
            unrealized = position.get("unrealizedPnl")
            realized = position.get("realizedPnl")
            margin = position.get("initialMargin")
            liquidation_raw = position.get("liquidationPrice")

            current_price = (
                float(mark_price)
                if mark_price not in (None, "")
                else float(trade.entry_price)
            )
            unrealized_pnl = (
                float(unrealized)
                if unrealized not in (None, "")
                else 0.0
            )
            realized_pnl = (
                float(realized)
                if realized not in (None, "")
                else 0.0
            )
            current_margin = (
                float(margin)
                if margin not in (None, "")
                else float(trade.margin_usdt)
            )
            liquidation_price = (
                float(liquidation_raw)
                if liquidation_raw not in (None, "")
                else None
            )
            pnl_percent = (
                unrealized_pnl / current_margin * 100
                if current_margin > 0
                else 0.0
            )

            update_open_trade_live_data(
                position_id,
                current_price=current_price,
                liquidation_price=liquidation_price,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=realized_pnl,
                pnl_percent=pnl_percent,
                current_margin=current_margin,
            )
            updated_count += 1
            _missing_checks.pop(position_id, None)
            continue

        missing_count = (
            _missing_checks.get(position_id, 0) + 1
        )
        _missing_checks[position_id] = missing_count

        if missing_count < REQUIRED_MISSING_CHECKS:
            continue

        history_entry = move_open_trade_to_history(
            position_id,
            close_reason="BLOFIN_POSITION_CLOSED",
        )

        _missing_checks.pop(position_id, None)

        if history_entry is not None:
            archived_positions.append(position_id)
            logger.info(
                "BLOFIN TRADE ARCHIVIERT | position_id=%s "
                "symbol=%s",
                position_id,
                trade.symbol,
            )

    return {
        "checked": len(blofin_trades),
        "updated": updated_count,
        "archived": archived_positions,
    }


async def blofin_sync_loop() -> None:
    """
    Laeuft dauerhaft im Hintergrund und synchronisiert
    Blofin-Trades in einem festen Intervall.
    """
    while True:
        try:
            result = await synchronize_blofin_trades()

            if result["archived"]:
                logger.info(
                    "Blofin-Sync: %s geprueft, %s "
                    "aktualisiert, %s archiviert.",
                    result["checked"],
                    result["updated"],
                    len(result["archived"]),
                )
        except Exception:
            logger.exception(
                "Fehler bei der Blofin-Trade-Synchronisierung."
            )

        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
