# ==========================================================
# Project Atlas
# File: app/sl_analysis.py
# Zweck: Hintergrund-Job, der abgeschlossene Stop-Loss-
#        Trades automatisch (7 Tage nach Schluss) darauf
#        prueft, ob der urspruengliche Take-Profit trotzdem
#        noch erreicht worden waere - Grundlage fuer den
#        SL-Optimierungsvorschlag.
# ==========================================================

from __future__ import annotations

import asyncio
import traceback
from datetime import timezone

from app.exchanges.bitunix import BitunixClient
from app.mae_analysis import (
    _analyze_post_stop_loss,
    analyze_own_window_mae_mfe,
)
from app.database.sl_analysis_repository import (
    get_sl_trades_ready_for_analysis,
    save_sl_post_analysis,
)


LOOKBACK_DAYS = 7
BATCH_SIZE = 20
LOOP_INTERVAL_SECONDS = 86400  # 1x pro Tag pruefen


async def _process_one_trade(client: BitunixClient, trade) -> None:
    closed_at = trade.closed_at
    opened_at = trade.opened_at

    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=timezone.utc)
    if opened_at is not None and opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)

    direction = str(trade.direction or "").strip().upper()

    if direction not in ("LONG", "SHORT"):
        return

    analysis = await _analyze_post_stop_loss(
        client=client,
        symbol=str(trade.symbol),
        direction=direction,
        entry_price=float(trade.entry_price),
        tp_price=float(trade.tp_price),
        closed_at=closed_at,
        lookback_days=LOOKBACK_DAYS,
    )

    if not analysis.get("checked"):
        return

    # TP-Optimierung: MFE innerhalb des tatsaechlichen
    # Trade-Zeitraums (Entry bis SL-Exit) - zeigt, wie weit
    # der Kurs vor dem SL-Treffer schon in die Gewinnzone
    # gelaufen war.
    own_window_mfe_percent = None

    if opened_at is not None:
        try:
            own_window_result = await analyze_own_window_mae_mfe(
                client=client,
                symbol=str(trade.symbol),
                direction=direction,
                entry_price=float(trade.entry_price),
                opened_at=opened_at,
                closed_at=closed_at,
            )
            if own_window_result is not None:
                own_window_mfe_percent = own_window_result[
                    "mfe_percent"
                ]
        except Exception:
            print(
                "Eigene-Fenster-MFE-Berechnung fehlgeschlagen "
                f"fuer position_id={trade.position_id}:"
            )
            traceback.print_exc()

    tp_reached_at_str = analysis.get("tp_reached_at")
    tp_reached_at_dt = None

    if tp_reached_at_str:
        from datetime import datetime as _dt
        tp_reached_at_dt = _dt.fromisoformat(tp_reached_at_str)

    save_sl_post_analysis(
        position_id=str(trade.position_id),
        signal_id=str(trade.signal_id),
        signal_name=str(trade.signal_name),
        symbol=str(trade.symbol),
        direction=direction,
        entry_price=float(trade.entry_price),
        sl_price=float(trade.sl_price),
        tp_price=float(trade.tp_price),
        original_closed_at=closed_at,
        lookback_days=LOOKBACK_DAYS,
        candle_interval_used=str(
            analysis.get("candle_interval_used", "1h")
        ),
        candle_count=int(analysis.get("candle_count", 0)),
        tp_would_have_been_reached=bool(
            analysis.get("tp_would_have_been_reached")
        ),
        tp_reached_at=tp_reached_at_dt,
        worst_price_after_sl_exit=float(
            analysis.get("worst_price_after_sl_exit")
        ),
        extended_mae_percent_from_entry=float(
            analysis.get("extended_mae_percent_from_entry")
        ),
        mfe_percent_own_window=own_window_mfe_percent,
    )


async def sl_analysis_loop() -> None:
    """
    Laeuft dauerhaft im Hintergrund (wie trade_sync_loop /
    webhook_worker_loop). Prueft stuendlich, ob neue SL-
    Trades bereit fuer die 7-Tage-Rueckblick-Analyse sind,
    und arbeitet sie in kleinen Batches ab, um die BitUnix-
    API nicht zu ueberlasten.
    """

    while True:
        try:
            ready_trades = get_sl_trades_ready_for_analysis(
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
                            "SL-Post-Analyse fehlgeschlagen "
                            f"fuer position_id={trade.position_id}:"
                        )
                        traceback.print_exc()

                    await asyncio.sleep(0.5)

                print(
                    f"SL-Post-Analyse: {len(ready_trades)} "
                    "Trade(s) verarbeitet."
                )

        except Exception:
            print("SL-Post-Analyse-Loop Fehler:")
            traceback.print_exc()

        await asyncio.sleep(LOOP_INTERVAL_SECONDS)
