from app.config.signals_legacy import SIGNALS
from app.database.database import SessionLocal, init_database
from app.database.models import Signal


def import_signals():
    init_database()
    db = SessionLocal()

    try:
        for signal_id, config in SIGNALS.items():
            existing = (
                db.query(Signal)
                .filter(Signal.signal_id == signal_id)
                .first()
            )

            if existing:
                print(f"Bereits vorhanden: {signal_id}")
                continue

            signal = Signal(
                signal_id=signal_id,
                display_name=config["display_name"],
                symbol=config["symbol"],
                direction=config["direction"],
                margin_usdt=config["margin_usdt"],
                leverage=config["leverage"],
                take_profit_percent=config["take_profit_percent"],
                stop_loss_percent=config["stop_loss_percent"],
                margin_mode=config.get("margin_mode", "CROSS"),
                order_type=config.get("order_type", "MARKET"),
                enabled=config.get("enabled", True),
            )

            db.add(signal)
            print(f"Importiert: {signal_id}")

        db.commit()

    finally:
        db.close()


if __name__ == "__main__":
    import_signals()
