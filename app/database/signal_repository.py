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
            "margin_usdt": signal.margin_usdt,
            "leverage": signal.leverage,
            "take_profit_percent": signal.take_profit_percent,
            "stop_loss_percent": signal.stop_loss_percent,
            "margin_mode": signal.margin_mode,
            "order_type": signal.order_type,
            "enabled": signal.enabled,
        }

    finally:
        db.close()
