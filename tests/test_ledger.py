from datetime import datetime, timezone

from app.ledger import apply_events
from app.models import EventType, LedgerEvent


def make_event(type_, asset, venue, quantity, price=0, tx_ref="tx"):
    return LedgerEvent(
        type=type_.value,
        asset=asset,
        venue=venue,
        quantity=quantity,
        price=price,
        tx_ref=tx_ref,
        created_at=datetime.now(timezone.utc),
    )


def test_transfers_preserve_spot_vwap_and_futures_pnl_is_isolated():
    events = [
        make_event(EventType.TRADE_BUY, "BTC", "BingX", 1.0, 50000, "buy-1"),
        make_event(EventType.TRANSFER_OUT, "BTC", "BingX", 1.0, 0, "btc-trezor"),
        make_event(EventType.TRANSFER_IN, "BTC", "Trezor", 1.0, 0, "btc-trezor"),
        make_event(EventType.TRANSFER_OUT, "BTC", "Trezor", 1.0, 0, "btc-arb"),
        make_event(EventType.TRANSFER_IN, "BTC", "Arbitrum", 1.0, 0, "btc-arb"),
        make_event(EventType.FUTURES_PNL, "USDT", "BingX Futures", 1200, 1.0, "pnl-1"),
    ]

    positions = apply_events(events)
    btc_arbitrum = positions[("BTC", "Arbitrum")]

    assert btc_arbitrum.quantity == 1.0
    assert btc_arbitrum.avg_cost == 50000
    assert ("USDT", "BingX Futures") not in positions
