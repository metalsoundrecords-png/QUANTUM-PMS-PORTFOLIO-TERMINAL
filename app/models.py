from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DATABASE_URL, DATA_DIR


class Base(DeclarativeBase):
    pass


class EventType(str, Enum):
    TRADE_BUY = "TRADE_BUY"
    TRADE_SELL = "TRADE_SELL"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    FUTURES_PNL = "FUTURES_PNL"
    FEE_PAYMENT = "FEE_PAYMENT"


class LedgerEvent(Base):
    __tablename__ = "ledger_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    asset: Mapped[str] = mapped_column(String(24), index=True)
    venue: Mapped[str] = mapped_column(String(48), index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0)
    price: Mapped[float] = mapped_column(Float, default=0)
    fee: Mapped[float] = mapped_column(Float, default=0)
    tx_ref: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
