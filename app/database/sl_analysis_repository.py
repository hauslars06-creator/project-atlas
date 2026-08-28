# ==========================================================
# Project Atlas
# File: app/database/sl_analysis_repository.py
# Zweck: Datenbankzugriffe fuer die automatische
#        SL-Post-Analyse (MAE / SL-Optimierung)
# ==========================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.database.database import SessionLocal
from app.database.models import SlPostAnalysis, TradeHistory


def get_sl_trades_ready_for_analysis(
    *,
    lookback_days: int = 7,
    limit: int = 20,
) -> list[TradeHistory]:
    """
    Findet per Stop-Loss geschlossene Trades, die:
    - mindestens `lookback_days` alt sind (genug Zeit fuer
      das Beobachtungsfenster ist bereits vergangen)
    - noch keinen Eintrag in sl_post_analysis haben

    Begrenzt auf `limit` pro Aufruf, um die BitUnix-API
    nicht mit zu vielen Requests auf einmal zu belasten.
    """

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=lookback_days)
    )

    db = SessionLocal()

    try:
        already_analyzed = {
            row[0]
            for row in db.query(
                SlPostAnalysis.position_id
            ).all()
        }

        candidates = (
            db.query(TradeHistory)
            .filter(
                TradeHistory.close_reason == "STOP_LOSS",
                TradeHistory.closed_at <= cutoff,
            )
            .order_by(TradeHistory.closed_at.asc())
            .limit(limit * 3)
            .all()
        )

        result = [
            trade
            for trade in candidates
            if str(trade.position_id) not in already_analyzed
        ]

        return result[:limit]

    finally:
        db.close()


def save_sl_post_analysis(
    *,
    position_id: str,
    signal_id: str,
    signal_name: str,
    symbol: str,
    direction: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    original_closed_at: datetime,
    lookback_days: int,
    candle_interval_used: str,
    candle_count: int,
    tp_would_have_been_reached: bool,
    tp_reached_at: datetime | None,
    worst_price_after_sl_exit: float,
    extended_mae_percent_from_entry: float,
    mfe_percent_own_window: float | None = None,
) -> None:
    db = SessionLocal()

    try:
        existing = (
            db.query(SlPostAnalysis)
            .filter(
                SlPostAnalysis.position_id == str(position_id)
            )
            .first()
        )

        if existing is not None:
            return

        row = SlPostAnalysis(
            position_id=str(position_id),
            signal_id=str(signal_id),
            signal_name=str(signal_name),
            symbol=str(symbol),
            direction=str(direction),
            entry_price=float(entry_price),
            sl_price=float(sl_price),
            tp_price=float(tp_price),
            original_closed_at=original_closed_at,
            lookback_days=int(lookback_days),
            candle_interval_used=str(candle_interval_used),
            candle_count=int(candle_count),
            tp_would_have_been_reached=bool(
                tp_would_have_been_reached
            ),
            tp_reached_at=tp_reached_at,
            worst_price_after_sl_exit=float(
                worst_price_after_sl_exit
            ),
            extended_mae_percent_from_entry=float(
                extended_mae_percent_from_entry
            ),
            mfe_percent_own_window=(
                float(mfe_percent_own_window)
                if mfe_percent_own_window is not None
                else None
            ),
        )

        db.add(row)
        db.commit()

    finally:
        db.close()


def get_sl_analysis_summary_by_signal() -> list[dict[str, Any]]:
    """
    Aggregiert die gespeicherten SL-Post-Analysen pro
    Signal: wie viele SL-Trades haetten den TP trotzdem
    noch erreicht, und wie weit ist es im Schnitt maximal
    noch gegen die Position gelaufen.
    """

    db = SessionLocal()

    try:
        rows = db.query(SlPostAnalysis).all()
    finally:
        db.close()

    if not rows:
        return []

    grouped: dict[str, list[SlPostAnalysis]] = {}

    for row in rows:
        grouped.setdefault(row.signal_id, []).append(row)

    summary: list[dict[str, Any]] = []

    for signal_id, group in grouped.items():
        total = len(group)

        would_have_reached = [
            r for r in group if r.tp_would_have_been_reached
        ]

        relevant_mae = [
            r.extended_mae_percent_from_entry
            for r in would_have_reached
        ]

        avg_extended_mae = (
            sum(relevant_mae) / len(relevant_mae)
            if relevant_mae
            else None
        )

        max_extended_mae = (
            max(relevant_mae) if relevant_mae else None
        )

        current_sl_distances = []

        for r in group:
            if r.direction == "LONG":
                dist = (
                    (r.entry_price - r.sl_price)
                    / r.entry_price
                    * 100.0
                )
            else:
                dist = (
                    (r.sl_price - r.entry_price)
                    / r.entry_price
                    * 100.0
                )
            current_sl_distances.append(dist)

        avg_current_sl_percent = (
            sum(current_sl_distances) / len(current_sl_distances)
        )

        # TP-Optimierung: aktueller durchschnittlicher TP-
        # Abstand vom Entry, als Vergleichsbasis fuer den
        # TP-Vorschlag.
        current_tp_distances = []

        for r in group:
            if r.direction == "LONG":
                dist = (
                    (r.tp_price - r.entry_price)
                    / r.entry_price
                    * 100.0
                )
            else:
                dist = (
                    (r.entry_price - r.tp_price)
                    / r.entry_price
                    * 100.0
                )
            current_tp_distances.append(dist)

        avg_current_tp_percent = (
            sum(current_tp_distances) / len(current_tp_distances)
        )

        # TP-Optimierung: wie weit ist der Kurs VOR dem SL-
        # Treffer schon in die Gewinnzone gelaufen (eigener
        # Trade-Zeitraum, keine Zukunftsvorschau)? Ein
        # engerer TP haette solche Trades schon als Gewinn
        # geschlossen, bevor der SL ueberhaupt getriggert hat.
        own_window_mfe_values = [
            r.mfe_percent_own_window
            for r in group
            if r.mfe_percent_own_window is not None
        ]

        would_benefit_from_tighter_tp = len(
            [v for v in own_window_mfe_values if v > 0]
        )

        avg_own_window_mfe = (
            sum(own_window_mfe_values) / len(own_window_mfe_values)
            if own_window_mfe_values
            else None
        )

        suggested_tp_percent = None

        if (
            avg_own_window_mfe is not None
            and len(own_window_mfe_values) >= 3
        ):
            # 20% Sicherheitsabschlag, damit der Vorschlag
            # nicht exakt auf der historischen Kante liegt.
            candidate = avg_own_window_mfe * 0.8

            # Nur vorschlagen, wenn tatsaechlich enger als
            # der aktuelle TP - sonst waere es kein "engerer"
            # TP-Vorschlag mehr.
            if candidate > 0 and candidate < avg_current_tp_percent:
                suggested_tp_percent = round(candidate, 3)

        suggestion = None

        if avg_extended_mae is not None and total >= 3:
            suggested = max(
                avg_current_sl_percent,
                avg_extended_mae * 1.2,
            )

            suggestion = round(suggested, 3)

        summary.append(
            {
                "signal_id": signal_id,
                "signal_name": group[0].signal_name,
                "symbol": group[0].symbol,
                "analyzed_sl_trades": total,
                "would_have_reached_tp_count": len(
                    would_have_reached
                ),
                "would_have_reached_tp_percent": round(
                    len(would_have_reached) / total * 100.0,
                    2,
                ),
                "avg_current_sl_percent": round(
                    avg_current_sl_percent, 3
                ),
                "avg_extended_mae_percent": (
                    round(avg_extended_mae, 3)
                    if avg_extended_mae is not None
                    else None
                ),
                "max_extended_mae_percent": (
                    round(max_extended_mae, 3)
                    if max_extended_mae is not None
                    else None
                ),
                "suggested_sl_percent": suggestion,
                "avg_current_tp_percent": round(
                    avg_current_tp_percent, 3
                ),
                "avg_own_window_mfe_percent": (
                    round(avg_own_window_mfe, 3)
                    if avg_own_window_mfe is not None
                    else None
                ),
                "would_benefit_from_tighter_tp_count": (
                    would_benefit_from_tighter_tp
                ),
                "suggested_tp_percent": suggested_tp_percent,
            }
        )

    summary.sort(
        key=lambda item: item["would_have_reached_tp_percent"],
        reverse=True,
    )

    return summary
