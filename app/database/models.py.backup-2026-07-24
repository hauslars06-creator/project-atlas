from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    signal_id: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        index=True,
        nullable=False,
    )

    display_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    symbol: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    direction: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    margin_usdt: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    leverage: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    take_profit_percent: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    stop_loss_percent: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    margin_mode: Mapped[str] = mapped_column(
        String(20),
        default="CROSS",
        nullable=False,
    )

    order_type: Mapped[str] = mapped_column(
        String(20),
        default="MARKET",
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
