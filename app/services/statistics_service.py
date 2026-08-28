# ==========================================================
# Project Atlas
# File: app/services/statistics_service.py
# Milestone: M5.1
# ==========================================================

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import inf, sqrt
from typing import Any, Literal

from sqlalchemy import or_
from app.database.database import SessionLocal
from app.database.models import TradeHistory


StatisticsPeriod = Literal[
    "today",
    "week",
    "month",
    "last_7_days",
    "last_14_days",
    "last_30_days",
    "all",
]

VALID_PERIODS: tuple[StatisticsPeriod, ...] = (
    "today",
    "week",
    "month",
    "last_7_days",
    "last_14_days",
    "last_30_days",
    "all",
)


@dataclass(frozen=True)
class PeriodBounds:
    key: StatisticsPeriod
    start: datetime | None
    end: datetime
    label: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc_aware(
    value: datetime | None,
) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def resolve_period_bounds(
    period: StatisticsPeriod,
    *,
    now: datetime | None = None,
) -> PeriodBounds:
    if period not in VALID_PERIODS:
        raise ValueError(
            "Ungültiger Zeitraum. Erlaubt: "
            + ", ".join(VALID_PERIODS)
        )

    resolved_now = _to_utc_aware(now) or _utc_now()

    if period == "today":
        start = resolved_now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        label = "Heute"

    elif period == "week":
        start_of_day = resolved_now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        start = start_of_day - timedelta(
            days=start_of_day.weekday()
        )
        label = "Diese Woche"

    elif period == "month":
        start = resolved_now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        label = "Dieser Monat"

    elif period == "last_7_days":
        start = resolved_now - timedelta(days=7)
        label = "Letzte 7 Tage"

    elif period == "last_14_days":
        start = resolved_now - timedelta(days=14)
        label = "Letzte 14 Tage"

    elif period == "last_30_days":
        start = resolved_now - timedelta(days=30)
        label = "Letzte 30 Tage"

    else:
        start = None
        label = "Gesamt"

    return PeriodBounds(
        key=period,
        start=start,
        end=resolved_now,
        label=label,
    )


def _round(
    value: float | None,
    digits: int = 8,
) -> float | None:
    if value is None:
        return None

    return round(float(value), digits)


def _safe_average(
    values: list[float],
) -> float | None:
    if not values:
        return None

    return sum(values) / len(values)


def _wilson_confidence_interval(
    wins: int,
    total: int,
    *,
    z: float = 1.96,
) -> dict[str, float] | None:
    """
    Wilson-Score-Konfidenzintervall fuer eine Erfolgsquote.

    Robuster als die naive Normalverteilungs-Naeherung,
    besonders bei kleinen Stichproben oder Quoten nahe 0%
    bzw. 100% - Standardverfahren fuer Trefferquoten-
    Unsicherheit. z=1.96 entspricht 95% Konfidenz.

    Gibt None zurueck, wenn keine Trades vorliegen.
    """

    if total <= 0:
        return None

    p_hat = wins / total
    denominator = 1 + (z * z) / total

    center = (
        p_hat + (z * z) / (2 * total)
    ) / denominator

    margin = (
        z
        * sqrt(
            (p_hat * (1 - p_hat) / total)
            + (z * z) / (4 * total * total)
        )
        / denominator
    )

    lower = max(0.0, center - margin) * 100.0
    upper = min(1.0, center + margin) * 100.0

    return {
        "lower_percent": _round(lower),
        "upper_percent": _round(upper),
        "sample_size": total,
    }


MIN_TRADES_FOR_SCORE = 5

SCORE_WEIGHT_WIN_RATE = 0.40
SCORE_WEIGHT_PROFIT_FACTOR = 0.35
SCORE_WEIGHT_DRAWDOWN = 0.25


def _calculate_signal_quality_score(
    *,
    decisive_trade_count: int,
    win_rate_percent: float | None,
    profit_factor: float | str | None,
    max_drawdown_percent: float | None,
) -> dict[str, Any]:
    """
    Kombiniert Win-Rate, Profit Factor und Max-Drawdown zu
    einem einzelnen Score (0-100) fuer die optische
    Rangliste "Performance je Signal".

    Erfordert eine Mindestanzahl entschiedener Trades
    (MIN_TRADES_FOR_SCORE), da einzelne Trades sonst
    verzerrend wirken (z.B. 100% Winrate bei 2 Trades).
    Bei zu wenig Daten wird score=None mit
    insufficient_data=True zurueckgegeben.
    """

    if decisive_trade_count < MIN_TRADES_FOR_SCORE:
        return {
            "quality_score": None,
            "insufficient_data": True,
        }

    win_rate_component = (
        min(max(win_rate_percent or 0.0, 0.0), 100.0)
    )

    if profit_factor is None:
        profit_factor_component = 0.0
    elif profit_factor == "inf" or profit_factor == inf:
        profit_factor_component = 100.0
    else:
        # Profit Factor >= 3 gilt als exzellent (100 Punkte),
        # linear skaliert dazwischen.
        profit_factor_component = (
            min(max(float(profit_factor), 0.0), 3.0)
            / 3.0
            * 100.0
        )

    if max_drawdown_percent is None:
        drawdown_component = 100.0
    else:
        # 0% Drawdown = 100 Punkte, 50%+ Drawdown = 0 Punkte.
        drawdown_component = (
            100.0
            - min(max(max_drawdown_percent, 0.0), 50.0)
            * 2.0
        )

    score = (
        win_rate_component * SCORE_WEIGHT_WIN_RATE
        + profit_factor_component
        * SCORE_WEIGHT_PROFIT_FACTOR
        + drawdown_component * SCORE_WEIGHT_DRAWDOWN
    )

    return {
        "quality_score": _round(score, digits=1),
        "insufficient_data": False,
    }


def _calculate_max_drawdown(
    ordered_trades: list[TradeHistory],
) -> dict[str, float | None]:
    """
    Berechnet den maximalen Drawdown auf der kumulierten
    Equity-Kurve (laufende Summe von pnl_usdt in
    chronologischer Reihenfolge).

    Gibt sowohl den absoluten USDT-Betrag als auch den
    Prozentsatz relativ zum jeweiligen Hoechststand
    zurueck. Bei weniger als einem gueltigen Trade oder
    wenn nie ein positiver Peak erreicht wurde, ist der
    Prozentwert None (Division durch 0 vermeiden).
    """

    running_equity = 0.0
    peak_equity = 0.0

    max_drawdown_abs = 0.0
    max_drawdown_percent: float | None = None

    for trade in ordered_trades:
        if trade.pnl_usdt is None:
            continue

        running_equity += float(trade.pnl_usdt)

        if running_equity > peak_equity:
            peak_equity = running_equity

        drawdown_abs = peak_equity - running_equity

        if drawdown_abs > max_drawdown_abs:
            max_drawdown_abs = drawdown_abs

            if peak_equity > 0:
                max_drawdown_percent = (
                    drawdown_abs / peak_equity * 100.0
                )

    return {
        "max_drawdown_usdt": _round(max_drawdown_abs),
        "max_drawdown_percent": _round(
            max_drawdown_percent
        ),
    }


def _calculate_streaks(
    ordered_results: list[str],
) -> tuple[int, int]:
    best_win_streak = 0
    worst_loss_streak = 0

    current_win_streak = 0
    current_loss_streak = 0

    for result in ordered_results:
        if result == "win":
            current_win_streak += 1
            current_loss_streak = 0
            best_win_streak = max(
                best_win_streak,
                current_win_streak,
            )

        elif result == "loss":
            current_loss_streak += 1
            current_win_streak = 0
            worst_loss_streak = max(
                worst_loss_streak,
                current_loss_streak,
            )

        else:
            current_win_streak = 0
            current_loss_streak = 0

    return best_win_streak, worst_loss_streak


def _serialize_period(
    bounds: PeriodBounds,
) -> dict[str, Any]:
    return {
        "key": bounds.key,
        "label": bounds.label,
        "start": (
            bounds.start.isoformat()
            if bounds.start is not None
            else None
        ),
        "end": bounds.end.isoformat(),
        "timezone": "UTC",
    }


def _query_trades(
    period: StatisticsPeriod,
    *,
    now: datetime | None = None,
    lock_scope: str | None = None,
    signal_type: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
) -> tuple[list[TradeHistory], PeriodBounds]:
    """
    lock_scope: "ACTIVE" -> nur is_locked=False,
                "LOCKED" -> nur is_locked=True,
                None/"ALL" -> keine Einschraenkung
                (Rueckwaertskompatibel, bisheriges Verhalten).

    signal_type: Filtert auf Signale, deren Name den
                 angegebenen Begriff enthaelt (z.B.
                 "Indicator", "Detector", "Neeson") - nicht
                 der volle Signalname, sondern die Signal-
                 Familie/Bauart. Gross-/Kleinschreibung
                 wird ignoriert.
    """

    bounds = resolve_period_bounds(
        period,
        now=now,
    )

    db = SessionLocal()

    try:
        query = db.query(TradeHistory)

        if bounds.start is not None:
            query = query.filter(
                TradeHistory.closed_at >= bounds.start
            )

        query = query.filter(
            TradeHistory.closed_at <= bounds.end
        )

        normalized_scope = (
            str(lock_scope).strip().upper()
            if lock_scope
            else None
        )

        # Manuelle/externe BitUnix-Trades zaehlen statistisch
        # IMMER als "Gesperrt", unabhaengig vom is_locked-
        # Schalter (der nur steuert, ob der Bot den Trade
        # automatisch verwalten darf - eine andere Sache als
        # die statistische Kategorie "manuell vs. Signal").
        is_manual_trade = (
            TradeHistory.signal_id == "EXTERNAL"
        )

        if normalized_scope == "ACTIVE":
            query = query.filter(
                TradeHistory.is_locked.is_(False),
                TradeHistory.signal_id != "EXTERNAL",
            )
        elif normalized_scope == "LOCKED":
            query = query.filter(
                or_(
                    TradeHistory.is_locked.is_(True),
                    is_manual_trade,
                )
            )

        if signal_type:
            query = query.filter(
                TradeHistory.signal_name.ilike(
                    f"%{signal_type.strip()}%"
                )
            )

        if symbol:
            query = query.filter(
                TradeHistory.symbol == symbol.strip().upper()
            )

        if timeframe:
            query = query.filter(
                TradeHistory.timeframe == timeframe.strip()
            )

        if direction:
            normalized_direction = direction.strip().upper()
            if normalized_direction in ("LONG", "SHORT"):
                query = query.filter(
                    TradeHistory.direction == normalized_direction
                )

        trades = (
            query
            .order_by(
                TradeHistory.closed_at.asc(),
                TradeHistory.id.asc(),
            )
            .all()
        )

        return trades, bounds

    finally:
        db.close()


def _calculate_performance_by_symbol(
    trades: list[TradeHistory],
) -> list[dict[str, Any]]:
    """
    Aggregiert Performance-Kennzahlen (Winrate, PnL,
    Profit Factor, Drawdown, Score) gruppiert nach Symbol
    statt nach Signal - fasst dabei alle Signale desselben
    Symbols zusammen.
    """

    symbol_groups: dict[str, list[TradeHistory]] = (
        defaultdict(list)
    )

    for trade in trades:
        symbol_groups[str(trade.symbol)].append(trade)

    performance_by_symbol: list[dict[str, Any]] = []

    for symbol, symbol_trades in symbol_groups.items():
        symbol_valid = [
            trade
            for trade in symbol_trades
            if trade.pnl_usdt is not None
        ]

        symbol_wins = [
            trade
            for trade in symbol_valid
            if float(trade.pnl_usdt) > 0
        ]

        symbol_losses = [
            trade
            for trade in symbol_valid
            if float(trade.pnl_usdt) < 0
        ]

        symbol_breakeven = [
            trade
            for trade in symbol_valid
            if float(trade.pnl_usdt) == 0
        ]

        symbol_decisive = (
            len(symbol_wins) + len(symbol_losses)
        )

        symbol_roi_values = [
            float(trade.pnl_percent)
            for trade in symbol_trades
            if trade.pnl_percent is not None
        ]

        symbol_gross_profit = sum(
            float(trade.pnl_usdt)
            for trade in symbol_wins
        )

        symbol_gross_loss_abs = abs(
            sum(
                float(trade.pnl_usdt)
                for trade in symbol_losses
            )
        )

        symbol_profit_factor: float | None

        if symbol_gross_loss_abs > 0:
            symbol_profit_factor = (
                symbol_gross_profit
                / symbol_gross_loss_abs
            )
        elif symbol_gross_profit > 0:
            symbol_profit_factor = inf
        else:
            symbol_profit_factor = None

        symbol_drawdown = _calculate_max_drawdown(
            symbol_trades
        )

        long_trades = [
            trade
            for trade in symbol_valid
            if str(trade.direction or "").upper() == "LONG"
        ]

        short_trades = [
            trade
            for trade in symbol_valid
            if str(trade.direction or "").upper() == "SHORT"
        ]

        performance_by_symbol.append(
            {
                "symbol": symbol,
                "trade_count": len(symbol_trades),
                "evaluated_trade_count": len(
                    symbol_valid
                ),
                "missing_result_count": (
                    len(symbol_trades)
                    - len(symbol_valid)
                ),
                "winning_trades": len(symbol_wins),
                "losing_trades": len(symbol_losses),
                "breakeven_trades": len(
                    symbol_breakeven
                ),
                "long_trade_count": len(long_trades),
                "short_trade_count": len(short_trades),
                "win_rate_percent": _round(
                    (
                        len(symbol_wins)
                        / symbol_decisive
                        * 100.0
                    )
                    if symbol_decisive
                    else None
                ),
                "win_rate_confidence_interval": (
                    _wilson_confidence_interval(
                        len(symbol_wins),
                        symbol_decisive,
                    )
                ),
                "net_pnl_usdt": _round(
                    sum(
                        float(trade.pnl_usdt)
                        for trade in symbol_valid
                    )
                ),
                "average_roi_percent": _round(
                    _safe_average(symbol_roi_values)
                ),
                "profit_factor": (
                    None
                    if symbol_profit_factor is None
                    else (
                        "inf"
                        if symbol_profit_factor == inf
                        else _round(symbol_profit_factor)
                    )
                ),
                "max_drawdown_usdt": (
                    symbol_drawdown["max_drawdown_usdt"]
                ),
                "max_drawdown_percent": (
                    symbol_drawdown["max_drawdown_percent"]
                ),
                **_calculate_signal_quality_score(
                    decisive_trade_count=symbol_decisive,
                    win_rate_percent=(
                        len(symbol_wins)
                        / symbol_decisive
                        * 100.0
                        if symbol_decisive
                        else None
                    ),
                    profit_factor=symbol_profit_factor,
                    max_drawdown_percent=(
                        symbol_drawdown[
                            "max_drawdown_percent"
                        ]
                    ),
                ),
            }
        )

    performance_by_symbol.sort(
        key=lambda item: (
            item["quality_score"] is not None,
            item["quality_score"] or 0.0,
            item["net_pnl_usdt"] or 0.0,
            item["evaluated_trade_count"],
        ),
        reverse=True,
    )

    return performance_by_symbol


MIN_OVERLAPPING_DAYS_FOR_CORRELATION = 5
CORRELATION_FLAG_THRESHOLD = 0.5


def _pearson_correlation(
    series_a: list[float],
    series_b: list[float],
) -> float | None:
    n = len(series_a)

    if n < 2 or n != len(series_b):
        return None

    mean_a = sum(series_a) / n
    mean_b = sum(series_b) / n

    cov = sum(
        (a - mean_a) * (b - mean_b)
        for a, b in zip(series_a, series_b)
    )

    var_a = sum((a - mean_a) ** 2 for a in series_a)
    var_b = sum((b - mean_b) ** 2 for b in series_b)

    if var_a <= 0 or var_b <= 0:
        return None

    return cov / sqrt(var_a * var_b)


def _calculate_signal_correlations(
    trades: list[TradeHistory],
) -> list[dict[str, Any]]:
    """
    Berechnet paarweise Pearson-Korrelation der taeglichen
    Netto-PnL zwischen Signalen. Ein hoher positiver Wert
    bedeutet: die Signale gewinnen/verlieren tendenziell
    am selben Tag - sieht nach Diversifikation aus, ist es
    aber nicht. Nur auffaellige Paare (|r| >= Schwelle)
    werden zurueckgegeben, nicht die volle Matrix.

    Tage ohne Trade eines Signals zaehlen als 0 PnL fuer
    dieses Signal an diesem Tag (nicht als fehlender Wert) -
    das ist inhaltlich richtig, da "kein Trade" ebenfalls
    eine reale Tagesaussage ist ("an diesem Tag hat das
    Signal weder gewonnen noch verloren").
    """

    valid_trades = [
        t for t in trades if t.pnl_usdt is not None
    ]

    if not valid_trades:
        return []

    all_days: set[str] = set()
    signal_names: dict[str, str] = {}
    daily_by_signal: dict[str, dict[str, float]] = (
        defaultdict(lambda: defaultdict(float))
    )

    for trade in valid_trades:
        closed_at = _to_utc_aware(trade.closed_at)

        if closed_at is None:
            continue

        day_key = closed_at.date().isoformat()
        all_days.add(day_key)

        signal_id = str(trade.signal_id)
        signal_names[signal_id] = str(trade.signal_name)

        daily_by_signal[signal_id][day_key] += float(
            trade.pnl_usdt
        )

    sorted_days = sorted(all_days)
    signal_ids = sorted(daily_by_signal.keys())

    series_by_signal: dict[str, list[float]] = {}

    for signal_id in signal_ids:
        series_by_signal[signal_id] = [
            daily_by_signal[signal_id].get(day, 0.0)
            for day in sorted_days
        ]

    correlations: list[dict[str, Any]] = []

    for i in range(len(signal_ids)):
        for j in range(i + 1, len(signal_ids)):
            id_a = signal_ids[i]
            id_b = signal_ids[j]

            trade_days_a = len(
                daily_by_signal[id_a]
            )
            trade_days_b = len(
                daily_by_signal[id_b]
            )

            if (
                trade_days_a < MIN_OVERLAPPING_DAYS_FOR_CORRELATION
                or trade_days_b < MIN_OVERLAPPING_DAYS_FOR_CORRELATION
            ):
                continue

            r = _pearson_correlation(
                series_by_signal[id_a],
                series_by_signal[id_b],
            )

            if r is None:
                continue

            if abs(r) < CORRELATION_FLAG_THRESHOLD:
                continue

            correlations.append(
                {
                    "signal_id_a": id_a,
                    "signal_name_a": signal_names[id_a],
                    "signal_id_b": id_b,
                    "signal_name_b": signal_names[id_b],
                    "correlation": _round(r, digits=3),
                    "trade_days_a": trade_days_a,
                    "trade_days_b": trade_days_b,
                }
            )

    correlations.sort(
        key=lambda item: abs(item["correlation"]),
        reverse=True,
    )

    return correlations


def _calculate_performance_by_timeframe(
    trades: list[TradeHistory],
) -> list[dict[str, Any]]:
    """
    Aggregiert Performance-Kennzahlen (Winrate, PnL,
    Profit Factor, Drawdown, Score) gruppiert nach Timeframe
    statt nach Signal - fasst dabei alle Signale desselben
    Zeitrahmens (z.B. 15M, 30M, 1H) zusammen.
    """

    timeframe_groups: dict[str, list[TradeHistory]] = (
        defaultdict(list)
    )

    for trade in trades:
        timeframe_groups[str(trade.timeframe)].append(trade)

    performance_by_timeframe: list[dict[str, Any]] = []

    for tf, timeframe_trades in timeframe_groups.items():
        timeframe_valid = [
            trade
            for trade in timeframe_trades
            if trade.pnl_usdt is not None
        ]

        timeframe_wins = [
            trade
            for trade in timeframe_valid
            if float(trade.pnl_usdt) > 0
        ]

        timeframe_losses = [
            trade
            for trade in timeframe_valid
            if float(trade.pnl_usdt) < 0
        ]

        timeframe_breakeven = [
            trade
            for trade in timeframe_valid
            if float(trade.pnl_usdt) == 0
        ]

        timeframe_tp_hits = [
            trade
            for trade in timeframe_trades
            if str(trade.close_reason or "").strip().upper()
            == "TAKE_PROFIT"
        ]

        timeframe_sl_hits = [
            trade
            for trade in timeframe_trades
            if str(trade.close_reason or "").strip().upper()
            == "STOP_LOSS"
        ]

        timeframe_decisive = (
            len(timeframe_wins) + len(timeframe_losses)
        )

        timeframe_roi_values = [
            float(trade.pnl_percent)
            for trade in timeframe_trades
            if trade.pnl_percent is not None
        ]

        timeframe_gross_profit = sum(
            float(trade.pnl_usdt)
            for trade in timeframe_wins
        )

        timeframe_gross_loss_abs = abs(
            sum(
                float(trade.pnl_usdt)
                for trade in timeframe_losses
            )
        )

        timeframe_profit_factor: float | None

        if timeframe_gross_loss_abs > 0:
            timeframe_profit_factor = (
                timeframe_gross_profit
                / timeframe_gross_loss_abs
            )
        elif timeframe_gross_profit > 0:
            timeframe_profit_factor = inf
        else:
            timeframe_profit_factor = None

        timeframe_drawdown = _calculate_max_drawdown(
            timeframe_trades
        )

        long_trades = [
            trade
            for trade in timeframe_valid
            if str(trade.direction or "").upper() == "LONG"
        ]

        short_trades = [
            trade
            for trade in timeframe_valid
            if str(trade.direction or "").upper() == "SHORT"
        ]

        performance_by_timeframe.append(
            {
                "timeframe": tf,
                "trade_count": len(timeframe_trades),
                "evaluated_trade_count": len(
                    timeframe_valid
                ),
                "missing_result_count": (
                    len(timeframe_trades)
                    - len(timeframe_valid)
                ),
                "winning_trades": len(timeframe_wins),
                "losing_trades": len(timeframe_losses),
                "breakeven_trades": len(
                    timeframe_breakeven
                ),
                "tp_hit_count": len(timeframe_tp_hits),
                "sl_hit_count": len(timeframe_sl_hits),
                "long_trade_count": len(long_trades),
                "short_trade_count": len(short_trades),
                "win_rate_percent": _round(
                    (
                        len(timeframe_wins)
                        / timeframe_decisive
                        * 100.0
                    )
                    if timeframe_decisive
                    else None
                ),
                "win_rate_confidence_interval": (
                    _wilson_confidence_interval(
                        len(timeframe_wins),
                        timeframe_decisive,
                    )
                ),
                "net_pnl_usdt": _round(
                    sum(
                        float(trade.pnl_usdt)
                        for trade in timeframe_valid
                    )
                ),
                "average_roi_percent": _round(
                    _safe_average(timeframe_roi_values)
                ),
                "profit_factor": (
                    None
                    if timeframe_profit_factor is None
                    else (
                        "inf"
                        if timeframe_profit_factor == inf
                        else _round(timeframe_profit_factor)
                    )
                ),
                "max_drawdown_usdt": (
                    timeframe_drawdown["max_drawdown_usdt"]
                ),
                "max_drawdown_percent": (
                    timeframe_drawdown["max_drawdown_percent"]
                ),
                **_calculate_signal_quality_score(
                    decisive_trade_count=timeframe_decisive,
                    win_rate_percent=(
                        len(timeframe_wins)
                        / timeframe_decisive
                        * 100.0
                        if timeframe_decisive
                        else None
                    ),
                    profit_factor=timeframe_profit_factor,
                    max_drawdown_percent=(
                        timeframe_drawdown[
                            "max_drawdown_percent"
                        ]
                    ),
                ),
            }
        )

    performance_by_timeframe.sort(
        key=lambda item: (
            item["quality_score"] is not None,
            item["quality_score"] or 0.0,
            item["net_pnl_usdt"] or 0.0,
            item["evaluated_trade_count"],
        ),
        reverse=True,
    )

    return performance_by_timeframe


def _calculate_performance_by_weekday(
    trades: list[TradeHistory],
) -> list[dict[str, Any]]:
    """
    Aggregiert Performance-Kennzahlen (Winrate, PnL,
    Profit Factor, Drawdown, Score) gruppiert nach Wochentag (Handelsschluss-Zeitpunkt,
    UTC) - zeigt, ob bestimmte Wochentage systematisch
    besser oder schlechter laufen.
    """

    weekday_names = [
        "Montag", "Dienstag", "Mittwoch", "Donnerstag",
        "Freitag", "Samstag", "Sonntag"
    ]

    weekday_groups: dict[str, list[TradeHistory]] = (
        defaultdict(list)
    )

    for trade in trades:
        closed_at = _to_utc_aware(trade.closed_at)
        if closed_at is None:
            continue
        weekday_label = weekday_names[closed_at.weekday()]
        weekday_groups[weekday_label].append(trade)

    performance_by_weekday: list[dict[str, Any]] = []

    for weekday_label, weekday_trades in weekday_groups.items():
        weekday_valid = [
            trade
            for trade in weekday_trades
            if trade.pnl_usdt is not None
        ]

        weekday_wins = [
            trade
            for trade in weekday_valid
            if float(trade.pnl_usdt) > 0
        ]

        weekday_losses = [
            trade
            for trade in weekday_valid
            if float(trade.pnl_usdt) < 0
        ]

        weekday_breakeven = [
            trade
            for trade in weekday_valid
            if float(trade.pnl_usdt) == 0
        ]

        weekday_tp_hits = [
            trade
            for trade in weekday_trades
            if str(trade.close_reason or "").strip().upper()
            == "TAKE_PROFIT"
        ]

        weekday_sl_hits = [
            trade
            for trade in weekday_trades
            if str(trade.close_reason or "").strip().upper()
            == "STOP_LOSS"
        ]

        weekday_decisive = (
            len(weekday_wins) + len(weekday_losses)
        )

        weekday_roi_values = [
            float(trade.pnl_percent)
            for trade in weekday_trades
            if trade.pnl_percent is not None
        ]

        weekday_gross_profit = sum(
            float(trade.pnl_usdt)
            for trade in weekday_wins
        )

        weekday_gross_loss_abs = abs(
            sum(
                float(trade.pnl_usdt)
                for trade in weekday_losses
            )
        )

        weekday_profit_factor: float | None

        if weekday_gross_loss_abs > 0:
            weekday_profit_factor = (
                weekday_gross_profit
                / weekday_gross_loss_abs
            )
        elif weekday_gross_profit > 0:
            weekday_profit_factor = inf
        else:
            weekday_profit_factor = None

        weekday_drawdown = _calculate_max_drawdown(
            weekday_trades
        )

        long_trades = [
            trade
            for trade in weekday_valid
            if str(trade.direction or "").upper() == "LONG"
        ]

        short_trades = [
            trade
            for trade in weekday_valid
            if str(trade.direction or "").upper() == "SHORT"
        ]

        performance_by_weekday.append(
            {
                "weekday": weekday_label,
                "trade_count": len(weekday_trades),
                "evaluated_trade_count": len(
                    weekday_valid
                ),
                "missing_result_count": (
                    len(weekday_trades)
                    - len(weekday_valid)
                ),
                "winning_trades": len(weekday_wins),
                "losing_trades": len(weekday_losses),
                "breakeven_trades": len(
                    weekday_breakeven
                ),
                "tp_hit_count": len(weekday_tp_hits),
                "sl_hit_count": len(weekday_sl_hits),
                "long_trade_count": len(long_trades),
                "short_trade_count": len(short_trades),
                "win_rate_percent": _round(
                    (
                        len(weekday_wins)
                        / weekday_decisive
                        * 100.0
                    )
                    if weekday_decisive
                    else None
                ),
                "win_rate_confidence_interval": (
                    _wilson_confidence_interval(
                        len(weekday_wins),
                        weekday_decisive,
                    )
                ),
                "net_pnl_usdt": _round(
                    sum(
                        float(trade.pnl_usdt)
                        for trade in weekday_valid
                    )
                ),
                "average_roi_percent": _round(
                    _safe_average(weekday_roi_values)
                ),
                "profit_factor": (
                    None
                    if weekday_profit_factor is None
                    else (
                        "inf"
                        if weekday_profit_factor == inf
                        else _round(weekday_profit_factor)
                    )
                ),
                "max_drawdown_usdt": (
                    weekday_drawdown["max_drawdown_usdt"]
                ),
                "max_drawdown_percent": (
                    weekday_drawdown["max_drawdown_percent"]
                ),
                **_calculate_signal_quality_score(
                    decisive_trade_count=weekday_decisive,
                    win_rate_percent=(
                        len(weekday_wins)
                        / weekday_decisive
                        * 100.0
                        if weekday_decisive
                        else None
                    ),
                    profit_factor=weekday_profit_factor,
                    max_drawdown_percent=(
                        weekday_drawdown[
                            "max_drawdown_percent"
                        ]
                    ),
                ),
            }
        )

    weekday_order = {
        name: i for i, name in enumerate(weekday_names)
    }
    performance_by_weekday.sort(
        key=lambda item: weekday_order.get(item["weekday"], 99)
    )

    return performance_by_weekday


def _calculate_performance_by_hour(
    trades: list[TradeHistory],
) -> list[dict[str, Any]]:
    """
    Aggregiert Performance-Kennzahlen (Winrate, PnL,
    Profit Factor, Drawdown, Score) gruppiert nach Handelsschluss-Stunde (UTC, 0-23) -
    zeigt, ob bestimmte Tageszeiten systematisch besser
    oder schlechter laufen.
    """

    hour_groups: dict[int, list[TradeHistory]] = (
        defaultdict(list)
    )

    for trade in trades:
        closed_at = _to_utc_aware(trade.closed_at)
        if closed_at is None:
            continue
        hour_groups[closed_at.hour].append(trade)

    performance_by_hour: list[dict[str, Any]] = []

    for hour_value, hour_trades in hour_groups.items():
        hour_valid = [
            trade
            for trade in hour_trades
            if trade.pnl_usdt is not None
        ]

        hour_wins = [
            trade
            for trade in hour_valid
            if float(trade.pnl_usdt) > 0
        ]

        hour_losses = [
            trade
            for trade in hour_valid
            if float(trade.pnl_usdt) < 0
        ]

        hour_breakeven = [
            trade
            for trade in hour_valid
            if float(trade.pnl_usdt) == 0
        ]

        hour_tp_hits = [
            trade
            for trade in hour_trades
            if str(trade.close_reason or "").strip().upper()
            == "TAKE_PROFIT"
        ]

        hour_sl_hits = [
            trade
            for trade in hour_trades
            if str(trade.close_reason or "").strip().upper()
            == "STOP_LOSS"
        ]

        hour_decisive = (
            len(hour_wins) + len(hour_losses)
        )

        hour_roi_values = [
            float(trade.pnl_percent)
            for trade in hour_trades
            if trade.pnl_percent is not None
        ]

        hour_gross_profit = sum(
            float(trade.pnl_usdt)
            for trade in hour_wins
        )

        hour_gross_loss_abs = abs(
            sum(
                float(trade.pnl_usdt)
                for trade in hour_losses
            )
        )

        hour_profit_factor: float | None

        if hour_gross_loss_abs > 0:
            hour_profit_factor = (
                hour_gross_profit
                / hour_gross_loss_abs
            )
        elif hour_gross_profit > 0:
            hour_profit_factor = inf
        else:
            hour_profit_factor = None

        hour_drawdown = _calculate_max_drawdown(
            hour_trades
        )

        long_trades = [
            trade
            for trade in hour_valid
            if str(trade.direction or "").upper() == "LONG"
        ]

        short_trades = [
            trade
            for trade in hour_valid
            if str(trade.direction or "").upper() == "SHORT"
        ]

        performance_by_hour.append(
            {
                "hour_utc": hour_value,
                "trade_count": len(hour_trades),
                "evaluated_trade_count": len(
                    hour_valid
                ),
                "missing_result_count": (
                    len(hour_trades)
                    - len(hour_valid)
                ),
                "winning_trades": len(hour_wins),
                "losing_trades": len(hour_losses),
                "breakeven_trades": len(
                    hour_breakeven
                ),
                "tp_hit_count": len(hour_tp_hits),
                "sl_hit_count": len(hour_sl_hits),
                "long_trade_count": len(long_trades),
                "short_trade_count": len(short_trades),
                "win_rate_percent": _round(
                    (
                        len(hour_wins)
                        / hour_decisive
                        * 100.0
                    )
                    if hour_decisive
                    else None
                ),
                "win_rate_confidence_interval": (
                    _wilson_confidence_interval(
                        len(hour_wins),
                        hour_decisive,
                    )
                ),
                "net_pnl_usdt": _round(
                    sum(
                        float(trade.pnl_usdt)
                        for trade in hour_valid
                    )
                ),
                "average_roi_percent": _round(
                    _safe_average(hour_roi_values)
                ),
                "profit_factor": (
                    None
                    if hour_profit_factor is None
                    else (
                        "inf"
                        if hour_profit_factor == inf
                        else _round(hour_profit_factor)
                    )
                ),
                "max_drawdown_usdt": (
                    hour_drawdown["max_drawdown_usdt"]
                ),
                "max_drawdown_percent": (
                    hour_drawdown["max_drawdown_percent"]
                ),
                **_calculate_signal_quality_score(
                    decisive_trade_count=hour_decisive,
                    win_rate_percent=(
                        len(hour_wins)
                        / hour_decisive
                        * 100.0
                        if hour_decisive
                        else None
                    ),
                    profit_factor=hour_profit_factor,
                    max_drawdown_percent=(
                        hour_drawdown[
                            "max_drawdown_percent"
                        ]
                    ),
                ),
            }
        )

    performance_by_hour.sort(
        key=lambda item: item["hour_utc"]
    )

    return performance_by_hour


def _calculate_performance_by_direction(
    trades: list[TradeHistory],
) -> list[dict[str, Any]]:
    """
    Aggregiert Performance-Kennzahlen (Winrate, PnL,
    Profit Factor, Drawdown, Score) gruppiert nach Richtung (LONG/SHORT) - direkter
    Vergleich, ob Long- oder Short-Trades besser laufen.
    """

    direction_groups: dict[str, list[TradeHistory]] = (
        defaultdict(list)
    )

    for trade in trades:
        direction_groups[str(trade.direction or '').upper()].append(trade)

    performance_by_direction: list[dict[str, Any]] = []

    for direction_key, direction_trades in direction_groups.items():
        direction_valid = [
            trade
            for trade in direction_trades
            if trade.pnl_usdt is not None
        ]

        direction_wins = [
            trade
            for trade in direction_valid
            if float(trade.pnl_usdt) > 0
        ]

        direction_losses = [
            trade
            for trade in direction_valid
            if float(trade.pnl_usdt) < 0
        ]

        direction_breakeven = [
            trade
            for trade in direction_valid
            if float(trade.pnl_usdt) == 0
        ]

        direction_decisive = (
            len(direction_wins) + len(direction_losses)
        )

        direction_roi_values = [
            float(trade.pnl_percent)
            for trade in direction_trades
            if trade.pnl_percent is not None
        ]

        direction_gross_profit = sum(
            float(trade.pnl_usdt)
            for trade in direction_wins
        )

        direction_gross_loss_abs = abs(
            sum(
                float(trade.pnl_usdt)
                for trade in direction_losses
            )
        )

        direction_profit_factor: float | None

        if direction_gross_loss_abs > 0:
            direction_profit_factor = (
                direction_gross_profit
                / direction_gross_loss_abs
            )
        elif direction_gross_profit > 0:
            direction_profit_factor = inf
        else:
            direction_profit_factor = None

        direction_drawdown = _calculate_max_drawdown(
            direction_trades
        )

        performance_by_direction.append(
            {
                "direction": direction_key,
                "trade_count": len(direction_trades),
                "evaluated_trade_count": len(
                    direction_valid
                ),
                "missing_result_count": (
                    len(direction_trades)
                    - len(direction_valid)
                ),
                "winning_trades": len(direction_wins),
                "losing_trades": len(direction_losses),
                "breakeven_trades": len(
                    direction_breakeven
                ),
                "win_rate_percent": _round(
                    (
                        len(direction_wins)
                        / direction_decisive
                        * 100.0
                    )
                    if direction_decisive
                    else None
                ),
                "win_rate_confidence_interval": (
                    _wilson_confidence_interval(
                        len(direction_wins),
                        direction_decisive,
                    )
                ),
                "net_pnl_usdt": _round(
                    sum(
                        float(trade.pnl_usdt)
                        for trade in direction_valid
                    )
                ),
                "average_roi_percent": _round(
                    _safe_average(direction_roi_values)
                ),
                "profit_factor": (
                    None
                    if direction_profit_factor is None
                    else (
                        "inf"
                        if direction_profit_factor == inf
                        else _round(direction_profit_factor)
                    )
                ),
                "max_drawdown_usdt": (
                    direction_drawdown["max_drawdown_usdt"]
                ),
                "max_drawdown_percent": (
                    direction_drawdown["max_drawdown_percent"]
                ),
                **_calculate_signal_quality_score(
                    decisive_trade_count=direction_decisive,
                    win_rate_percent=(
                        len(direction_wins)
                        / direction_decisive
                        * 100.0
                        if direction_decisive
                        else None
                    ),
                    profit_factor=direction_profit_factor,
                    max_drawdown_percent=(
                        direction_drawdown[
                            "max_drawdown_percent"
                        ]
                    ),
                ),
            }
        )

    performance_by_direction.sort(
        key=lambda item: (
            item["quality_score"] is not None,
            item["quality_score"] or 0.0,
            item["net_pnl_usdt"] or 0.0,
            item["evaluated_trade_count"],
        ),
        reverse=True,
    )

    return performance_by_direction


def _analyze_trades(
    trades: list[TradeHistory],
    bounds: PeriodBounds,
) -> dict[str, Any]:
    valid_pnl_trades = [
        trade
        for trade in trades
        if trade.pnl_usdt is not None
    ]

    missing_pnl_trades = [
        trade
        for trade in trades
        if trade.pnl_usdt is None
    ]

    winning_trades = [
        trade
        for trade in valid_pnl_trades
        if float(trade.pnl_usdt) > 0
    ]

    losing_trades = [
        trade
        for trade in valid_pnl_trades
        if float(trade.pnl_usdt) < 0
    ]

    breakeven_trades = [
        trade
        for trade in valid_pnl_trades
        if float(trade.pnl_usdt) == 0
    ]

    gross_profit = sum(
        float(trade.pnl_usdt)
        for trade in winning_trades
    )

    gross_loss_abs = abs(
        sum(
            float(trade.pnl_usdt)
            for trade in losing_trades
        )
    )

    net_pnl = sum(
        float(trade.pnl_usdt)
        for trade in valid_pnl_trades
    )

    roi_values = [
        float(trade.pnl_percent)
        for trade in trades
        if trade.pnl_percent is not None
    ]

    evaluated_trade_count = len(valid_pnl_trades)
    decisive_trade_count = (
        len(winning_trades)
        + len(losing_trades)
    )

    win_rate = (
        len(winning_trades)
        / decisive_trade_count
        * 100.0
        if decisive_trade_count
        else None
    )

    profit_factor: float | None

    if gross_loss_abs > 0:
        profit_factor = gross_profit / gross_loss_abs
    elif gross_profit > 0:
        profit_factor = inf
    else:
        profit_factor = None

    ordered_results: list[str] = []

    for trade in valid_pnl_trades:
        pnl = float(trade.pnl_usdt)

        if pnl > 0:
            ordered_results.append("win")
        elif pnl < 0:
            ordered_results.append("loss")
        else:
            ordered_results.append("breakeven")

    best_win_streak, worst_loss_streak = (
        _calculate_streaks(ordered_results)
    )

    overall_drawdown = _calculate_max_drawdown(trades)

    largest_win = max(
        (
            float(trade.pnl_usdt)
            for trade in winning_trades
        ),
        default=None,
    )

    largest_loss = min(
        (
            float(trade.pnl_usdt)
            for trade in losing_trades
        ),
        default=None,
    )

    signal_groups: dict[
        tuple[str, str],
        list[TradeHistory],
    ] = defaultdict(list)

    for trade in trades:
        signal_groups[
            (
                str(trade.signal_id),
                str(trade.signal_name),
            )
        ].append(trade)

    performance_by_signal: list[dict[str, Any]] = []

    for (
        signal_id,
        signal_name,
    ), signal_trades in signal_groups.items():
        signal_valid = [
            trade
            for trade in signal_trades
            if trade.pnl_usdt is not None
        ]

        signal_wins = [
            trade
            for trade in signal_valid
            if float(trade.pnl_usdt) > 0
        ]

        signal_losses = [
            trade
            for trade in signal_valid
            if float(trade.pnl_usdt) < 0
        ]

        signal_breakeven = [
            trade
            for trade in signal_valid
            if float(trade.pnl_usdt) == 0
        ]

        signal_decisive = (
            len(signal_wins)
            + len(signal_losses)
        )

        signal_roi_values = [
            float(trade.pnl_percent)
            for trade in signal_trades
            if trade.pnl_percent is not None
        ]

        signal_gross_profit = sum(
            float(trade.pnl_usdt)
            for trade in signal_wins
        )

        signal_gross_loss_abs = abs(
            sum(
                float(trade.pnl_usdt)
                for trade in signal_losses
            )
        )

        signal_profit_factor: float | None

        if signal_gross_loss_abs > 0:
            signal_profit_factor = (
                signal_gross_profit
                / signal_gross_loss_abs
            )
        elif signal_gross_profit > 0:
            signal_profit_factor = inf
        else:
            signal_profit_factor = None

        signal_drawdown = _calculate_max_drawdown(
            signal_trades
        )

        performance_by_signal.append(
            {
                "signal_id": signal_id,
                "signal_name": signal_name,
                "trade_count": len(signal_trades),
                "evaluated_trade_count": len(
                    signal_valid
                ),
                "missing_result_count": (
                    len(signal_trades)
                    - len(signal_valid)
                ),
                "winning_trades": len(signal_wins),
                "losing_trades": len(signal_losses),
                "breakeven_trades": len(
                    signal_breakeven
                ),
                "win_rate_percent": _round(
                    (
                        len(signal_wins)
                        / signal_decisive
                        * 100.0
                    )
                    if signal_decisive
                    else None
                ),
                "win_rate_confidence_interval": (
                    _wilson_confidence_interval(
                        len(signal_wins),
                        signal_decisive,
                    )
                ),
                "net_pnl_usdt": _round(
                    sum(
                        float(trade.pnl_usdt)
                        for trade in signal_valid
                    )
                ),
                "average_roi_percent": _round(
                    _safe_average(signal_roi_values)
                ),
                "profit_factor": (
                    None
                    if signal_profit_factor is None
                    else (
                        "inf"
                        if signal_profit_factor == inf
                        else _round(signal_profit_factor)
                    )
                ),
                "symbol": (
                    str(signal_trades[0].symbol)
                    if signal_trades
                    else None
                ),
                "timeframe": (
                    str(signal_trades[0].timeframe)
                    if signal_trades
                    else None
                ),
                "direction": (
                    str(signal_trades[0].direction)
                    if signal_trades
                    else None
                ),
                "max_drawdown_usdt": (
                    signal_drawdown["max_drawdown_usdt"]
                ),
                "max_drawdown_percent": (
                    signal_drawdown["max_drawdown_percent"]
                ),
                **_calculate_signal_quality_score(
                    decisive_trade_count=(
                        signal_decisive
                    ),
                    win_rate_percent=(
                        len(signal_wins)
                        / signal_decisive
                        * 100.0
                        if signal_decisive
                        else None
                    ),
                    profit_factor=signal_profit_factor,
                    max_drawdown_percent=(
                        signal_drawdown[
                            "max_drawdown_percent"
                        ]
                    ),
                ),
            }
        )

    performance_by_signal.sort(
        key=lambda item: (
            item["quality_score"] is not None,
            item["quality_score"] or 0.0,
            item["net_pnl_usdt"] or 0.0,
            item["evaluated_trade_count"],
        ),
        reverse=True,
    )

    daily_groups: dict[str, list[TradeHistory]] = (
        defaultdict(list)
    )

    for trade in trades:
        closed_at = _to_utc_aware(
            trade.closed_at
        )

        if closed_at is None:
            continue

        daily_groups[
            closed_at.date().isoformat()
        ].append(trade)

    daily_pnl: list[dict[str, Any]] = []

    running_pnl = 0.0

    for day in sorted(daily_groups):
        day_trades = daily_groups[day]

        day_valid = [
            trade
            for trade in day_trades
            if trade.pnl_usdt is not None
        ]

        day_pnl = sum(
            float(trade.pnl_usdt)
            for trade in day_valid
        )

        running_pnl += day_pnl

        daily_pnl.append(
            {
                "date": day,
                "trade_count": len(day_trades),
                "evaluated_trade_count": len(
                    day_valid
                ),
                "pnl_usdt": _round(day_pnl),
                "cumulative_pnl_usdt": _round(
                    running_pnl
                ),
            }
        )

    return {
        "period": _serialize_period(bounds),
        "summary": {
            "trade_count": len(trades),
            "evaluated_trade_count": (
                evaluated_trade_count
            ),
            "decisive_trade_count": (
                decisive_trade_count
            ),
            "missing_result_count": len(
                missing_pnl_trades
            ),
            "winning_trades": len(
                winning_trades
            ),
            "losing_trades": len(
                losing_trades
            ),
            "breakeven_trades": len(
                breakeven_trades
            ),
            "win_rate_percent": _round(
                win_rate
            ),
            "gross_profit_usdt": _round(
                gross_profit
            ),
            "gross_loss_usdt": _round(
                -gross_loss_abs
            ),
            "net_pnl_usdt": _round(
                net_pnl
            ),
            "average_roi_percent": _round(
                _safe_average(roi_values)
            ),
            "profit_factor": (
                "infinity"
                if profit_factor == inf
                else _round(profit_factor)
            ),
            "max_drawdown_usdt": (
                overall_drawdown["max_drawdown_usdt"]
            ),
            "max_drawdown_percent": (
                overall_drawdown["max_drawdown_percent"]
            ),
            "average_win_usdt": _round(
                _safe_average(
                    [
                        float(trade.pnl_usdt)
                        for trade in winning_trades
                    ]
                )
            ),
            "average_loss_usdt": _round(
                _safe_average(
                    [
                        float(trade.pnl_usdt)
                        for trade in losing_trades
                    ]
                )
            ),
            "largest_win_usdt": _round(
                largest_win
            ),
            "largest_loss_usdt": _round(
                largest_loss
            ),
            "best_win_streak": (
                best_win_streak
            ),
            "worst_loss_streak": (
                worst_loss_streak
            ),
        },
        "performance_by_signal": (
            performance_by_signal
        ),
        "performance_by_symbol": (
            _calculate_performance_by_symbol(trades)
        ),
        "signal_correlations": (
            _calculate_signal_correlations(trades)
        ),
        "performance_by_timeframe": (
            _calculate_performance_by_timeframe(trades)
        ),
        "performance_by_direction": (
            _calculate_performance_by_direction(trades)
        ),
        "performance_by_weekday": (
            _calculate_performance_by_weekday(trades)
        ),
        "performance_by_hour": (
            _calculate_performance_by_hour(trades)
        ),
        "daily_pnl": daily_pnl,
    }


def get_statistics(
    period: StatisticsPeriod = "last_30_days",
    *,
    now: datetime | None = None,
    lock_scope: str | None = None,
    signal_type: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    trades, bounds = _query_trades(
        period,
        now=now,
        lock_scope=lock_scope,
        signal_type=signal_type,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
    )

    return _analyze_trades(
        trades,
        bounds,
    )


def get_period_comparison(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    periods: list[StatisticsPeriod] = [
        "today",
        "week",
        "month",
        "last_30_days",
        "all",
    ]

    comparison: dict[str, Any] = {}

    for period in periods:
        statistics = get_statistics(
            period,
            now=now,
        )

        comparison[period] = {
            "period": statistics["period"],
            "summary": statistics["summary"],
        }

    return comparison
