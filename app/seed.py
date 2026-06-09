from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .database import EventType, LedgerEvent


PRICES = {
    "BTC": 68240.50,
    "WBTC": 68190.00,
    "ETH": 3548.75,
    "WETH": 3547.20,
    "USDC": 1.0001,
    "USDT": 0.9998,
    "ARB": 0.9180,
    "SOL": 168.90,
}


def seed_database(db: Session) -> None:
    if db.query(LedgerEvent).first():
        return

    now = datetime.now(timezone.utc)
    events = [
        _event(EventType.BUY, "BTC", "BingX", 1.38, 57000, "buy-btc-1", now - timedelta(days=8)),
        _event(EventType.TRANSFER_OUT, "BTC", "BingX", 0.912, 0, "btc-trezor", now - timedelta(days=7)),
        _event(EventType.TRANSFER_IN, "BTC", "Trezor", 0.912, 0, "btc-trezor", now - timedelta(days=7, minutes=-2)),
        _event(EventType.TRANSFER_OUT, "BTC", "BingX", 0.468, 0, "btc-arb", now - timedelta(days=6)),
        _event(EventType.TRANSFER_IN, "WBTC", "Arbitrum", 0.468, 57000, "btc-arb", now - timedelta(days=6, minutes=-3)),
        _event(EventType.BUY, "ETH", "BingX", 24.05, 2980.40, "buy-eth-1", now - timedelta(days=5)),
        _event(EventType.TRANSFER_OUT, "ETH", "BingX", 9.8, 0, "eth-arb", now - timedelta(days=4)),
        _event(EventType.TRANSFER_IN, "WETH", "Arbitrum", 9.8, 2980.40, "eth-arb", now - timedelta(days=4, minutes=-3)),
        _event(EventType.BUY, "USDC", "Arbitrum", 18450, 1.0, "lp-usdc", now - timedelta(days=3)),
        _event(EventType.BUY, "ARB", "Arbitrum", 12400, 1.1240, "buy-arb-1", now - timedelta(days=3)),
        _event(EventType.BUY, "SOL", "BingX", 86.4, 142.30, "buy-sol-1", now - timedelta(days=2)),
        _event(EventType.BUY, "USDT", "BingX", 9600, 1.0, "collateral", now - timedelta(days=2)),
        _event(EventType.FUTURES_PNL, "USDT", "BingX Futures", 450, 1.0, "fut-1", now - timedelta(hours=6)),
        _event(EventType.FUTURES_PNL, "USDT", "BingX Futures", -120, 1.0, "fut-2", now - timedelta(hours=2)),
    ]
    db.add_all(events)
    db.commit()


def _event(
    type_: EventType,
    asset: str,
    venue: str,
    quantity: float,
    price: float,
    transaction_hash: str,
    timestamp: datetime,
    destination: str = "",
) -> LedgerEvent:
    return LedgerEvent(
        event_type=type_.value,
        asset=asset,
        venue=venue,
        destination=destination,
        quantity=quantity,
        price=price,
        transaction_hash=transaction_hash,
        timestamp=timestamp,
    )
