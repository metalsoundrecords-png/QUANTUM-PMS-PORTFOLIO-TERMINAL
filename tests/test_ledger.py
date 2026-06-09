from datetime import datetime, timezone

from app.database import EventType, LedgerEvent
from app.futures_isolation import FuturesLedger
from app.vwap_engine import VWAPEngine


def _evt(type_: EventType, asset: str, venue: str, quantity: float, price: float = 0.0, tx_hash: str = "tx") -> LedgerEvent:
    return LedgerEvent(
        event_type=type_.value,
        asset=asset,
        venue=venue,
        quantity=quantity,
        price=price,
        transaction_hash=tx_hash,
        timestamp=datetime.now(timezone.utc),
    )


def test_transfers_preserve_spot_vwap():
    """Las transferencias entre venues no deben alterar el costo base (VWAP)."""
    events = [
        _evt(EventType.BUY, "BTC", "BingX", 1.0, 50000, "buy-1"),
        _evt(EventType.TRANSFER_OUT, "BTC", "BingX", 1.0, 0, "btc-trezor"),
        _evt(EventType.TRANSFER_IN, "BTC", "Trezor", 1.0, 0, "btc-trezor"),
        _evt(EventType.TRANSFER_OUT, "BTC", "Trezor", 1.0, 0, "btc-arb"),
        _evt(EventType.TRANSFER_IN, "BTC", "Arbitrum", 1.0, 0, "btc-arb"),
    ]
    engine = VWAPEngine().process(events)
    positions = engine.positions()
    btc = positions[("BTC", "Arbitrum")]
    assert btc.quantity == 1.0
    assert btc.avg_cost == 50000.0


def test_futures_pnl_is_isolated_from_spot():
    """FUTURES_PNL no debe crear posiciones en el motor spot."""
    events = [
        _evt(EventType.BUY, "BTC", "BingX", 1.0, 50000, "buy-1"),
        _evt(EventType.FUTURES_PNL, "USDT", "BingX Futures", 1200, 1.0, "pnl-1"),
    ]
    engine = VWAPEngine().process(events)
    positions = engine.positions()
    assert ("USDT", "BingX Futures") not in positions
    assert ("BTC", "BingX") in positions


def test_futures_pnl_accumulates_cash_balance():
    """FuturesLedger debe acumular el PnL realizado correctamente."""
    events = [
        _evt(EventType.FUTURES_PNL, "USDT", "BingX Futures", 450, 1.0, "pnl-1"),
        _evt(EventType.FUTURES_PNL, "USDT", "BingX Futures", -120, 1.0, "pnl-2"),
    ]
    ledger = FuturesLedger().process(events)
    assert ledger.total_realized_pnl("USDT") == 330.0


def test_futures_pnl_rejects_non_stablecoin():
    """FuturesLedger debe ignorar PnL en activos no-stablecoin."""
    events = [
        _evt(EventType.FUTURES_PNL, "BTC", "BingX Futures", 0.05, 68000, "pnl-btc"),
    ]
    ledger = FuturesLedger().process(events)
    assert ledger.total_realized_pnl() == 0.0


def test_vwap_recalculated_on_multiple_buys():
    """El VWAP debe recalcularse correctamente tras múltiples compras."""
    events = [
        _evt(EventType.BUY, "ETH", "BingX", 2.0, 3000.0, "buy-1"),
        _evt(EventType.BUY, "ETH", "BingX", 3.0, 4000.0, "buy-2"),
    ]
    engine = VWAPEngine().process(events)
    pos = engine.positions()[("ETH", "BingX")]
    # VWAP = (2*3000 + 3*4000) / 5 = 18000 / 5 = 3600
    assert pos.quantity == 5.0
    assert abs(pos.avg_cost - 3600.0) < 1e-6


def test_fifo_sell_preserves_remaining_vwap():
    """La venta FIFO no debe alterar el costo base de los lotes restantes."""
    events = [
        _evt(EventType.BUY, "SOL", "BingX", 10.0, 100.0, "buy-1"),
        _evt(EventType.BUY, "SOL", "BingX", 10.0, 200.0, "buy-2"),
        _evt(EventType.SELL, "SOL", "BingX", 10.0, 150.0, "sell-1"),
    ]
    engine = VWAPEngine().process(events)
    pos = engine.positions()[("SOL", "BingX")]
    # Tras vender los primeros 10 (a $100), quedan 10 del segundo lote a $200
    assert pos.quantity == 10.0
    assert abs(pos.avg_cost - 200.0) < 1e-6
