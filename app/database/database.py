from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base


DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = "sqlite:///data/project_atlas.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def _ensure_open_trade_live_columns() -> None:
    """
    Ergänzt neue M2-Spalten idempotent in bestehenden SQLite-Datenbanken.

    SQLAlchemy create_all() erstellt fehlende Tabellen, erweitert jedoch
    keine bereits bestehenden Tabellen.
    """

    required_columns = {
        "current_price": "REAL",
        "liquidation_price": "REAL",
        "last_exchange_sync": "DATETIME",

        # PROJECT ATLAS M5.3B EXTERNAL TRADES START
        "trade_source": (
            "VARCHAR(20) NOT NULL DEFAULT 'ATLAS'"
        ),
        # PROJECT ATLAS M5.3B EXTERNAL TRADES END

    }

    with engine.begin() as connection:
        rows = connection.exec_driver_sql(
            "PRAGMA table_info(open_trades)"
        ).fetchall()

        existing_columns = {
            str(row[1])
            for row in rows
        }

        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue

            connection.exec_driver_sql(
                f"ALTER TABLE open_trades "
                f"ADD COLUMN {column_name} {column_type}"
            )


def _ensure_sl_post_analysis_columns() -> None:
    """
    Ergaenzt die neue Spalte fuer die TP-Optimierung
    idempotent in bereits bestehenden SQLite-Datenbanken.
    """

    with engine.begin() as connection:
        rows = connection.exec_driver_sql(
            "PRAGMA table_info(sl_post_analysis)"
        ).fetchall()

        existing_columns = {
            str(row[1])
            for row in rows
        }

        if "mfe_percent_own_window" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE sl_post_analysis "
                "ADD COLUMN mfe_percent_own_window REAL"
            )


def _ensure_position_limit_setting_columns() -> None:
    """
    Ergaenzt die neuen Aktien-Futures-Limit-Spalten
    idempotent in bereits bestehenden SQLite-Datenbanken.
    """

    with engine.begin() as connection:
        rows = connection.exec_driver_sql(
            "PRAGMA table_info(position_limit_setting)"
        ).fetchall()

        existing_columns = {
            str(row[1])
            for row in rows
        }

        if "max_long_trades_stocks" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE position_limit_setting "
                "ADD COLUMN max_long_trades_stocks INTEGER"
            )

        if "max_short_trades_stocks" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE position_limit_setting "
                "ADD COLUMN max_short_trades_stocks INTEGER"
            )


def _ensure_open_trade_exchange_column() -> None:
    """
    Ergaenzt die exchange-Spalte idempotent in bereits
    bestehenden SQLite-Datenbanken. Bestehende Zeilen
    bekommen automatisch BITUNIX als Default.
    """

    with engine.begin() as connection:
        rows = connection.exec_driver_sql(
            "PRAGMA table_info(open_trades)"
        ).fetchall()

        existing_columns = {
            str(row[1])
            for row in rows
        }

        if "exchange" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE open_trades "
                "ADD COLUMN exchange VARCHAR(20) "
                "NOT NULL DEFAULT 'BITUNIX'"
            )


def _ensure_trade_history_exchange_column() -> None:
    """
    Ergaenzt die exchange-Spalte idempotent in bereits
    bestehenden trade_history-Tabellen.
    """

    with engine.begin() as connection:
        rows = connection.exec_driver_sql(
            "PRAGMA table_info(trade_history)"
        ).fetchall()

        existing_columns = {
            str(row[1])
            for row in rows
        }

        if "exchange" not in existing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE trade_history "
                "ADD COLUMN exchange VARCHAR(20) "
                "NOT NULL DEFAULT 'BITUNIX'"
            )


def init_database():
    Base.metadata.create_all(bind=engine)
    _ensure_open_trade_live_columns()
    _ensure_sl_post_analysis_columns()
    _ensure_position_limit_setting_columns()
    _ensure_open_trade_exchange_column()
    _ensure_trade_history_exchange_column()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
