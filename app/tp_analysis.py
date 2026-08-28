# ==========================================================
# Project Atlas
# File: app/tp_analysis.py
# Zweck: Hintergrund-Job, der abgeschlossene Take-Profit-
#        Trades automatisch (7 Tage nach Schluss) darauf
#        prueft, wie weit der Preis danach noch guenstig
#        weitergelaufen waere - Grundlage fuer den TP-
#        Erhoehungsvorschlag (TP-Optimierung).
# ==========================================================

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime as _dt, timezone

from app.exchanges.bitunix import BitunixClient
from app.mae_analysis import analyze_post_take_profit
from app.database.tp_analysis_repository import (
    get_tp_trades_ready_for_analysis,
    save_tp_post_analysis,
)


LOOKBACK_DAYS = 7
BATCH_SIZE = 20
LOOP_INTERVAL_SECONDS = 86400  # 1x pro Tag pruefen


async def _process_one_trade(client: BitunixClient, trade) -> None:
    closed_at = trade.closed_at

    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=timezone.utc)

    direction = str(trade.direction or "").strip().upper()

    if direction not in ("LONG", "SHORT"):
        return

    analysis = await analyze_post_take_profit(
        client=client,
        symbol=str(trade.symbol),
        direction=direction,
        entry_price=float(trade.entry_price),
        sl_price=float(trade.sl_price),
        closed_at=closed_at,
        lookback_days=LOOKBACK_DAYS,
    )

    if not analysis.get("checked"):
        return

    sl_hit_at_str = analysis.get("sl_hit_at")
    sl_hit_at_dt = None

    if sl_hit_at_str:
        sl_hit_at_dt = _dt.fromisoformat(sl_hit_at_str)

    save_tp_post_analysis(
        position_id=str(trade.position_id),
        signal_id=str(trade.signal_id),
        signal_name=str(trade.signal_name),
        symbol=str(trade.symbol),
        direction=direction,
        entry_price=float(trade.entry_price),
        sl_price=float(trade.sl_price),
        tp_price=float(trade.tp_price),
        original_closed_at=closed_at,
        sl_would_have_been_hit=bool(
            analysis.get("sl_would_have_been_hit")
        ),
        sl_hit_at=sl_hit_at_dt,
        best_price_before_sl=float(
            analysis.get("best_price_before_sl")
        ),
        extended_mfe_percent_from_entry=float(
            analysis.get("extended_mfe_percent_from_entry")
        ),
    )


async def tp_analysis_loop() -> None:
    """
    Laeuft dauerhaft im Hintergrund (wie sl_analysis_loop).
    Prueft taeglich, ob neue TP-Trades bereit fuer die
    7-Tage-Rueckblick-Analyse sind, und arbeitet sie in
    kleinen Batches ab, um die BitUnix-API nicht zu
    ueberlasten.
    """

    while True:
        try:
            ready_trades = get_tp_trades_ready_for_analysis(
                lookback_days=LOOKBACK_DAYS,
                limit=BATCH_SIZE,
            )

            if ready_trades:
                client = BitunixClient()

                for trade in ready_trades:
                    try:
                        await _process_one_trade(client, trade)
                    except Exception:
                        print(
                            "TP-Post-Analyse fehlgeschlagen "
                            f"fuer position_id={trade.position_id}:"
                        )
                        traceback.print_exc()

                    # Kleine Pause zwischen einzelnen Trades,
                    # um die BitUnix-API nicht zu fluten.
                    await asyncio.sleep(0.5)

                print(
                    f"TP-Post-Analyse: {len(ready_trades)} "
                    "Trade(s) verarbeitet."
                )

        except Exception:
            print("TP-Post-Analyse-Loop Fehler:")
            traceback.print_exc()

        await asyncio.sleep(LOOP_INTERVAL_SECONDS)
