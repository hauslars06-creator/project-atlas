# ==========================================================
# Project Atlas
# File: app/database/tp_analysis_repository.py
# Zweck: Datenbankzugriffe fuer die automatische
#        TP-Post-Analyse (Extended MFE / TP-Optimierung)
# ==========================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.database.database import SessionLocal
from app.database.models import TpPostAnalysis, TradeHistory


def get_tp_trades_ready_for_analysis(
    *,
    lookback_days: int = 7,
    limit: int = 20,
) -> list[TradeHistory]:
    """
    Findet per Take-Profit geschlossene Trades, die:
    - mindestens `lookback_days` alt sind
    - noch keinen Eintrag in tp_post_analysis haben

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
                TpPostAnalysis.position_id
            ).all()
        }

        candidates = (
            db.query(TradeHistory)
            .filter(
                TradeHistory.close_reason == "TAKE_PROFIT",
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


def save_tp_post_analysis(
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
    sl_would_have_been_hit: bool,
    sl_hit_at: datetime | None,
    best_price_before_sl: float,
    extended_mfe_percent_from_entry: float,
) -> None:
    db = SessionLocal()

    try:
        existing = (
            db.query(TpPostAnalysis)
            .filter(
                TpPostAnalysis.position_id == str(position_id)
            )
            .first()
        )

        if existing is not None:
            return

        row = TpPostAnalysis(
            position_id=str(position_id),
            signal_id=str(signal_id),
            signal_name=str(signal_name),
            symbol=str(symbol),
            direction=str(direction),
            entry_price=float(entry_price),
            sl_price=float(sl_price),
            tp_price=float(tp_price),
            original_closed_at=original_closed_at,
            sl_would_have_been_hit=bool(
                sl_would_have_been_hit
            ),
            sl_hit_at=sl_hit_at,
            best_price_before_sl=float(
                best_price_before_sl
            ),
            extended_mfe_percent_from_entry=float(
                extended_mfe_percent_from_entry
            ),
        )

        db.add(row)
        db.commit()

    finally:
        db.close()


def get_tp_analysis_summary_by_signal() -> list[dict[str, Any]]:
    """
    Fasst alle vorhandenen TP-Post-Analysen je Signal
    zusammen und errechnet einen risikobewussten TP-
    Erhoehungsvorschlag.

    Risikobewusst heisst: Signale, bei denen ein
    nennenswerter Anteil der Trades den SL erreicht haette
    (bevor der neue, hoehere TP getroffen worden waere),
    bekommen KEINEN Vorschlag - hier wuerde ein hoeherer TP
    voraussichtlich die Gewinnquote spuerbar verschlechtern.
    """

    db = SessionLocal()

    try:
        rows = db.query(TpPostAnalysis).all()

    finally:
        db.close()

    grouped: dict[tuple[str, str], list[TpPostAnalysis]] = {}

    for row in rows:
        key = (row.signal_id, row.symbol)
        grouped.setdefault(key, []).append(row)

    summary: list[dict[str, Any]] = []

    for (signal_id, symbol), group in grouped.items():
        total = len(group)

        sl_risk_count = len(
            [r for r in group if r.sl_would_have_been_hit]
        )

        sl_risk_ratio = (
            sl_risk_count / total * 100.0
            if total > 0
            else 0.0
        )

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
            sum(current_tp_distances)
            / len(current_tp_distances)
        )

        # Nur die "sicheren" Trades (SL waere NICHT
        # getroffen worden) fliessen in den Durchschnitt der
        # zusaetzlich moeglichen Bewegung ein.
        safe_extended_mfe_values = [
            r.extended_mfe_percent_from_entry
            for r in group
            if not r.sl_would_have_been_hit
        ]

        avg_extended_mfe = (
            sum(safe_extended_mfe_values)
            / len(safe_extended_mfe_values)
            if safe_extended_mfe_values
            else None
        )

        suggested_tp_percent = None

        # Risiko-Schwelle: bei mehr als 25% "riskanten"
        # Trades (SL waere unterwegs getroffen worden) wird
        # bewusst KEIN hoeherer TP vorgeschlagen.
        risk_threshold_ok = sl_risk_ratio <= 25.0

        if (
            avg_extended_mfe is not None
            and len(safe_extended_mfe_values) >= 3
            and risk_threshold_ok
        ):
            # 30% Sicherheitsabschlag (konservativer als bei
            # der SL-Optimierung, da hier zusaetzlich das
            # Risiko einer schlechteren Gewinnquote beachtet
            # werden soll).
            candidate = avg_extended_mfe * 0.7

            if candidate > avg_current_tp_percent:
                suggested_tp_percent = round(
                    avg_current_tp_percent + candidate, 3
                )

        signal_name = group[0].signal_name

        summary.append(
            {
                "signal_id": signal_id,
                "signal_name": signal_name,
                "symbol": symbol,
                "analyzed_tp_trades": total,
                "sl_risk_count": sl_risk_count,
                "sl_risk_percent": round(sl_risk_ratio, 2),
                "avg_current_tp_percent": round(
                    avg_current_tp_percent, 3
                ),
                "avg_extended_mfe_percent": (
                    round(avg_extended_mfe, 3)
                    if avg_extended_mfe is not None
                    else None
                ),
                "suggested_tp_percent": suggested_tp_percent,
            }
        )

    summary.sort(
        key=lambda item: (
            item["suggested_tp_percent"] is not None,
            item["avg_extended_mfe_percent"] or 0,
        ),
        reverse=True,
    )

    return summary
