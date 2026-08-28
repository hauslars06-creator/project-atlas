from app.database.database import SessionLocal
from app.database.models import Signal


def get_signal_config(signal_id: str):
    db = SessionLocal()

    try:
        signal = (
            db.query(Signal)
            .filter(Signal.signal_id == signal_id)
            .first()
        )

        if signal is None:
            return None

        return {
            "display_name": signal.display_name,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "timeframe": signal.timeframe,
            "margin_usdt": signal.margin_usdt,
            "leverage": signal.leverage,
            "take_profit_percent": signal.take_profit_percent,
            "take_profit_2_percent": signal.take_profit_2_percent,
            "break_even_mode": signal.break_even_mode,
            "stop_loss_percent": signal.stop_loss_percent,
            "margin_mode": signal.margin_mode,
            "order_type": signal.order_type,
            "enabled": signal.enabled,
        }

    finally:
        db.close()
def toggle_signal_enabled(signal_id: str):
    db = SessionLocal()

    try:
        signal = (
            db.query(Signal)
            .filter(Signal.signal_id == signal_id)
            .first()
        )

        if signal is None:
            return None

        signal.enabled = not signal.enabled
        db.commit()
        db.refresh(signal)

        return signal.enabled

    finally:
        db.close()

def create_signal(
    signal_id: str,
    display_name: str,
    symbol: str,
    direction: str,
    timeframe: str,
    margin_usdt: float,
    leverage: int,
    take_profit_percent: float,
    stop_loss_percent: float,
    take_profit_2_percent: float | None = None,
    break_even_mode: str = "OFF",
    margin_mode: str = "CROSS",
    order_type: str = "MARKET",
    enabled: bool = True,
):
    db = SessionLocal()

    try:
        existing = (
            db.query(Signal)
            .filter(Signal.signal_id == signal_id)
            .first()
        )

        if existing is not None:
            return None

        signal = Signal(
            signal_id=signal_id,
            display_name=display_name,
            symbol=symbol,
            direction=direction,
            timeframe=timeframe,
            margin_usdt=margin_usdt,
            leverage=leverage,
            take_profit_percent=take_profit_percent,
            take_profit_2_percent=take_profit_2_percent,
            break_even_mode=break_even_mode,
            stop_loss_percent=stop_loss_percent,
            margin_mode=margin_mode,
            order_type=order_type,
            enabled=enabled,
        )

        db.add(signal)
        db.commit()
        db.refresh(signal)

        return signal

    finally:
        db.close()


def update_signal(
    signal_id: str,
    display_name: str,
    symbol: str,
    direction: str,
    timeframe: str,
    margin_usdt: float,
    leverage: int,
    take_profit_percent: float,
    stop_loss_percent: float,
    margin_mode: str,
    order_type: str,
    enabled: bool,
    take_profit_2_percent: float | None = None,
    break_even_mode: str = "OFF",
):
    db = SessionLocal()

    try:
        signal = (
            db.query(Signal)
            .filter(Signal.signal_id == signal_id)
            .first()
        )

        if signal is None:
            return None

        signal.display_name = display_name
        signal.symbol = symbol
        signal.direction = direction
        signal.timeframe = timeframe
        signal.margin_usdt = margin_usdt
        signal.leverage = leverage
        signal.take_profit_percent = take_profit_percent
        signal.take_profit_2_percent = take_profit_2_percent
        signal.break_even_mode = break_even_mode
        signal.stop_loss_percent = stop_loss_percent
        signal.margin_mode = margin_mode
        signal.order_type = order_type
        signal.enabled = enabled

        db.commit()
        db.refresh(signal)

        return signal

    finally:
        db.close()
def delete_signal(signal_id: str):
    db = SessionLocal()

    try:
        signal = (
            db.query(Signal)
            .filter(Signal.signal_id == signal_id)
            .first()
        )

        if signal is None:
            return False

        db.delete(signal)
        db.commit()

        return True

    finally:
        db.close()

def bulk_update_signals(
    signal_ids: list[str],
    margin_usdt: float | None = None,
    leverage: int | None = None,
    take_profit_percent: float | None = None,
    stop_loss_percent: float | None = None,
    take_profit_2_mode: str = "UNCHANGED",
    take_profit_2_percent: float | None = None,
    break_even_mode: str | None = None,
) -> int:
    """
    Aktualisiert ausschließlich die ausgewählten Signale.

    None beziehungsweise UNCHANGED bedeutet:
    Feld unverändert lassen.
    """

    if not signal_ids:
        return 0

    normalized_tp2_mode = str(
        take_profit_2_mode or "UNCHANGED"
    ).strip().upper()

    if normalized_tp2_mode not in {
        "UNCHANGED",
        "ENABLE",
        "DISABLE",
    }:
        raise ValueError(
            "Ungültiger TP2-Modus."
        )

    normalized_break_even = (
        str(break_even_mode).strip().upper()
        if break_even_mode not in (None, "")
        else None
    )

    if normalized_break_even not in {
        None,
        "OFF",
        "TP1",
        "TP2",
    }:
        raise ValueError(
            "Ungültiger Break-even-Modus."
        )

    if (
        normalized_tp2_mode == "ENABLE"
        and take_profit_2_percent is None
    ):
        raise ValueError(
            "Zum Aktivieren von TP2 fehlt der TP2-Prozentwert."
        )

    db = SessionLocal()

    try:
        signals = (
            db.query(Signal)
            .filter(
                Signal.signal_id.in_(signal_ids)
            )
            .all()
        )

        for signal in signals:

            if margin_usdt is not None:
                signal.margin_usdt = margin_usdt

            if leverage is not None:
                signal.leverage = leverage

            if take_profit_percent is not None:
                signal.take_profit_percent = (
                    take_profit_percent
                )

            if stop_loss_percent is not None:
                signal.stop_loss_percent = (
                    stop_loss_percent
                )

            if normalized_tp2_mode == "DISABLE":
                signal.take_profit_2_percent = None
                signal.break_even_mode = "OFF"

            elif normalized_tp2_mode == "ENABLE":
                signal.take_profit_2_percent = (
                    take_profit_2_percent
                )

                signal.break_even_mode = (
                    normalized_break_even
                    if normalized_break_even is not None
                    else "OFF"
                )

            elif normalized_break_even is not None:
                if (
                    normalized_break_even in {"TP1", "TP2"}
                    and signal.take_profit_2_percent is None
                ):
                    raise ValueError(
                        "Break-even nach TP1 oder TP2 "
                        "benötigt ein aktives TP2."
                    )

                signal.break_even_mode = (
                    normalized_break_even
                )

        db.commit()

        return len(signals)

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
