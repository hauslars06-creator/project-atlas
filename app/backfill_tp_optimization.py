# ==========================================================
# Project Atlas
# File: app/backfill_tp_optimization.py
# Zweck: Einmaliges Nachrechnen von mfe_percent_own_window
#        fuer bereits vorhandene sl_post_analysis-Eintraege,
#        die vor der TP-Optimierung gespeichert wurden.
#
# Aufruf (innerhalb des Containers):
#   python3 -m app.backfill_tp_optimization
# ==========================================================

from __future__ import annotations

import asyncio
import traceback

from app.database.database import SessionLocal
from app.database.models import SlPostAnalysis, TradeHistory
from app.exchanges.bitunix import BitunixClient
from app.mae_analysis import analyze_own_window_mae_mfe


async def run() -> None:
    db = SessionLocal()

    try:
        rows = (
            db.query(SlPostAnalysis)
            .filter(SlPostAnalysis.mfe_percent_own_window.is_(None))
            .all()
        )

        position_ids = [row.position_id for row in rows]

        trades_by_position_id = {}

        if position_ids:
            trades = (
                db.query(TradeHistory)
                .filter(
                    TradeHistory.position_id.in_(position_ids)
                )
                .all()
            )

            trades_by_position_id = {
                str(t.position_id): t for t in trades
            }

    finally:
        db.close()

    total = len(rows)
    print(f"Zu aktualisieren: {total} Eintraege ohne mfe_percent_own_window.")

    if total == 0:
        return

    client = BitunixClient()
    updated = 0
    skipped = 0

    for row in rows:
        trade = trades_by_position_id.get(str(row.position_id))

        if trade is None or trade.opened_at is None:
            skipped += 1
            print(
                f"  uebersprungen (kein Original-Trade/opened_at): "
                f"{row.position_id}"
            )
            continue

        try:
            result = await analyze_own_window_mae_mfe(
                client=client,
                symbol=str(row.symbol),
                direction=str(row.direction),
                entry_price=float(row.entry_price),
                opened_at=trade.opened_at,
                closed_at=row.original_closed_at,
            )
        except Exception:
            print(
                f"  Fehler bei {row.position_id}:"
            )
            traceback.print_exc()
            await asyncio.sleep(0.5)
            continue

        if result is None:
            skipped += 1
            print(
                f"  uebersprungen (keine Kerzendaten): "
                f"{row.position_id}"
            )
            await asyncio.sleep(0.5)
            continue

        db2 = SessionLocal()
        try:
            fresh_row = (
                db2.query(SlPostAnalysis)
                .filter(SlPostAnalysis.id == row.id)
                .first()
            )
            if fresh_row is not None:
                fresh_row.mfe_percent_own_window = result[
                    "mfe_percent"
                ]
                db2.commit()
                updated += 1
        finally:
            db2.close()

        # Kleine Pause, um die BitUnix-API nicht zu fluten.
        await asyncio.sleep(0.5)

    print(
        f"Fertig: {updated} aktualisiert, {skipped} uebersprungen "
        f"(von {total} gesamt)."
    )


if __name__ == "__main__":
    asyncio.run(run())
