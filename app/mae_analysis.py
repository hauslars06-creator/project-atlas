# ==========================================================
# Project Atlas
# File: app/mae_analysis.py
# Zweck: Gemeinsame MAE-/Post-Stop-Loss-Analyse-Logik,
#        genutzt von der manuellen Test-Route UND vom
#        automatischen Hintergrund-Job (sl_analysis.py)
# ==========================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.exchanges.bitunix import BitunixClient


async def _analyze_post_stop_loss(
    *,
    client: BitunixClient,
    symbol: str,
    direction: str,
    entry_price: float,
    tp_price: float,
    closed_at: datetime,
    lookback_days: int = 7,
) -> dict:
    """
    Beobachtet nach einem Stop-Loss-Exit weiter, ob der
    Preis den urspruenglichen Take-Profit innerhalb von
    `lookback_days` trotzdem noch erreicht haette, und wie
    weit es zwischenzeitlich noch gegen die Position
    gelaufen waere (Extended MAE ab Entry).

    Nutzt 1h-Kerzen (7 Tage = 168 Kerzen, passt in einen
    einzelnen BitUnix-Request mit Limit 200).
    """

    window_start_ms = int(closed_at.timestamp() * 1000)
    window_end_ms = int(
        (
            closed_at + timedelta(days=lookback_days)
        ).timestamp() * 1000
    )

    kline_response = await client.get_kline(
        symbol=symbol,
        interval="1h",
        start_time_ms=window_start_ms,
        end_time_ms=window_end_ms,
        limit=200,
    )

    raw_candles = kline_response.get("data") or []

    candles = sorted(
        (
            c for c in raw_candles
            if window_start_ms
            <= int(c.get("time", 0))
            <= window_end_ms
        ),
        key=lambda c: int(c.get("time", 0)),
    )

    if not candles:
        return {
            "checked": False,
            "reason": (
                "Keine Kerzendaten fuer den "
                "Beobachtungszeitraum verfuegbar."
            ),
        }

    tp_reached_at = None
    worst_price_after_sl = None

    for candle in candles:
        high = float(candle["high"])
        low = float(candle["low"])

        if direction == "LONG":
            if worst_price_after_sl is None or low < worst_price_after_sl:
                worst_price_after_sl = low

            if tp_reached_at is None and high >= tp_price:
                tp_reached_at = int(candle.get("time", 0))

        elif direction == "SHORT":
            if worst_price_after_sl is None or high > worst_price_after_sl:
                worst_price_after_sl = high

            if tp_reached_at is None and low <= tp_price:
                tp_reached_at = int(candle.get("time", 0))

    if direction == "LONG":
        extended_mae_percent = (
            (entry_price - worst_price_after_sl) / entry_price * 100.0
        )
    else:
        extended_mae_percent = (
            (worst_price_after_sl - entry_price) / entry_price * 100.0
        )

    return {
        "checked": True,
        "lookback_days": lookback_days,
        "candle_interval_used": "1h",
        "candle_count": len(candles),
        "tp_would_have_been_reached": tp_reached_at is not None,
        "tp_reached_at": (
            datetime.fromtimestamp(
                tp_reached_at / 1000, tz=timezone.utc
            ).isoformat()
            if tp_reached_at is not None
            else None
        ),
        "worst_price_after_sl_exit": worst_price_after_sl,
        "extended_mae_percent_from_entry": round(
            extended_mae_percent, 4
        ),
    }


async def analyze_own_window_mae_mfe(
    *,
    client: BitunixClient,
    symbol: str,
    direction: str,
    entry_price: float,
    opened_at: datetime,
    closed_at: datetime,
) -> dict | None:
    """
    Berechnet MAE (max. Gegenbewegung) UND MFE (max.
    Guenstige Bewegung) innerhalb des TATSAECHLICHEN
    Trade-Zeitraums (Entry bis Exit) - keine Vorschau in
    die Zukunft, nur der bereits gelaufene Kursverlauf
    waehrend der eigentlichen Trade-Laufzeit.

    Wird u.a. fuer die TP-Optimierung genutzt: ein hoher
    mfe_percent bei einem SL-Trade zeigt, dass der Kurs vor
    dem SL-Treffer schon deutlich in die Gewinnzone gelaufen
    war - ein engerer TP haette den Trade dort schon als
    Gewinn geschlossen.
    """

    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=timezone.utc)

    start_ms = int(opened_at.timestamp() * 1000)
    end_ms = int(closed_at.timestamp() * 1000)

    duration_minutes = (
        (closed_at - opened_at).total_seconds() / 60.0
    )

    if duration_minutes <= 200:
        interval = "1m"
    elif duration_minutes <= 200 * 5:
        interval = "5m"
    elif duration_minutes <= 200 * 15:
        interval = "15m"
    elif duration_minutes <= 200 * 60:
        interval = "1h"
    else:
        interval = "4h"

    kline_response = await client.get_kline(
        symbol=symbol,
        interval=interval,
        start_time_ms=start_ms,
        end_time_ms=end_ms,
        limit=200,
    )

    raw_candles = kline_response.get("data") or []

    candles = [
        c for c in raw_candles
        if start_ms <= int(c.get("time", 0)) <= end_ms
    ]

    if not candles:
        return None

    direction = str(direction or "").strip().upper()

    if direction not in ("LONG", "SHORT"):
        return None

    lows = [float(c["low"]) for c in candles]
    highs = [float(c["high"]) for c in candles]

    if direction == "LONG":
        worst_price = min(lows)
        best_price = max(highs)
        mae_percent = (
            (entry_price - worst_price) / entry_price * 100.0
        )
        mfe_percent = (
            (best_price - entry_price) / entry_price * 100.0
        )
    else:
        worst_price = max(highs)
        best_price = min(lows)
        mae_percent = (
            (worst_price - entry_price) / entry_price * 100.0
        )
        mfe_percent = (
            (entry_price - best_price) / entry_price * 100.0
        )

    return {
        "interval": interval,
        "candle_count": len(candles),
        "mae_percent": round(mae_percent, 4),
        "mfe_percent": round(mfe_percent, 4),
        "worst_price": worst_price,
        "best_price": best_price,
    }


async def analyze_post_take_profit(
    *,
    client: BitunixClient,
    symbol: str,
    direction: str,
    entry_price: float,
    sl_price: float,
    closed_at: datetime,
    lookback_days: int = 7,
) -> dict:
    """
    Beobachtet nach einem Take-Profit-Exit weiter, wie weit
    der Preis innerhalb von `lookback_days` noch guenstig
    weitergelaufen waere ("Extended MFE" ab Entry) - und
    prueft dabei chronologisch, ob der Preis zwischenzeitlich
    den urspruenglichen Stop-Loss erreicht haette.

    Risikobewusst: sobald der SL (hypothetisch) getroffen
    worden waere, wird die weitere Kursbewegung NICHT mehr
    fuer den TP-Vorschlag gezaehlt - der Trade waere ab
    diesem Punkt ja bereits als Verlust geschlossen worden.
    Nur die Guenstig-Bewegung VOR einem moeglichen SL-Treffer
    zaehlt als "sicherer" Spielraum fuer einen hoeheren TP.

    Nutzt 1h-Kerzen (7 Tage = 168 Kerzen, passt in einen
    einzelnen BitUnix-Request mit Limit 200).
    """

    window_start_ms = int(closed_at.timestamp() * 1000)
    window_end_ms = int(
        (
            closed_at + timedelta(days=lookback_days)
        ).timestamp() * 1000
    )

    kline_response = await client.get_kline(
        symbol=symbol,
        interval="1h",
        start_time_ms=window_start_ms,
        end_time_ms=window_end_ms,
        limit=200,
    )

    raw_candles = kline_response.get("data") or []

    candles = sorted(
        (
            c for c in raw_candles
            if window_start_ms
            <= int(c.get("time", 0))
            <= window_end_ms
        ),
        key=lambda c: int(c.get("time", 0)),
    )

    if not candles:
        return {
            "checked": False,
            "reason": (
                "Keine Kerzendaten fuer den "
                "Beobachtungszeitraum verfuegbar."
            ),
        }

    best_price_before_sl = entry_price
    sl_hit_at = None

    for candle in candles:
        high = float(candle["high"])
        low = float(candle["low"])

        if direction == "LONG":
            if low <= sl_price:
                sl_hit_at = int(candle.get("time", 0))
                break

            if high > best_price_before_sl:
                best_price_before_sl = high

        elif direction == "SHORT":
            if high >= sl_price:
                sl_hit_at = int(candle.get("time", 0))
                break

            if low < best_price_before_sl:
                best_price_before_sl = low

    if direction == "LONG":
        extended_mfe_percent = (
            (best_price_before_sl - entry_price)
            / entry_price * 100.0
        )
    else:
        extended_mfe_percent = (
            (entry_price - best_price_before_sl)
            / entry_price * 100.0
        )

    return {
        "checked": True,
        "lookback_days": lookback_days,
        "candle_interval_used": "1h",
        "candle_count": len(candles),
        "sl_would_have_been_hit": sl_hit_at is not None,
        "sl_hit_at": (
            datetime.fromtimestamp(
                sl_hit_at / 1000, tz=timezone.utc
            ).isoformat()
            if sl_hit_at is not None
            else None
        ),
        "best_price_before_sl": best_price_before_sl,
        "extended_mfe_percent_from_entry": round(
            extended_mfe_percent, 4
        ),
    }
