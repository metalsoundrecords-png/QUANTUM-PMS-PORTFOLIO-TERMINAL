from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable

from .models import EventType, LedgerEvent


@dataclass
class Lot:
    quantity: float
    unit_cost: float


@dataclass
class Position:
    asset: str
    venue: str
    quantity: float = 0.0
    cost: float = 0.0
    lots: deque[Lot] = field(default_factory=deque)

    @property
    def avg_cost(self) -> float:
        return self.cost / self.quantity if self.quantity else 0.0


def apply_events(events: Iterable[LedgerEvent]) -> dict[tuple[str, str], Position]:
    positions: dict[tuple[str, str], Position] = {}
    transfer_buffer: dict[str, deque[Lot]] = defaultdict(deque)

    def get(asset: str, venue: str) -> Position:
        key = (asset, venue)
        if key not in positions:
            positions[key] = Position(asset=asset, venue=venue)
        return positions[key]

    for event in events:
        if event.type == EventType.FUTURES_PNL.value:
            continue

        position = get(event.asset, event.venue)
        fee = event.fee or 0.0

        if event.type == EventType.TRADE_BUY.value:
            total_cost = event.quantity * event.price + fee
            unit_cost = total_cost / event.quantity if event.quantity else 0.0
            position.quantity += event.quantity
            position.cost += total_cost
            position.lots.append(Lot(event.quantity, unit_cost))

        elif event.type == EventType.TRADE_SELL.value:
            remaining = event.quantity
            while remaining > 0 and position.lots:
                lot = position.lots[0]
                used = min(remaining, lot.quantity)
                position.quantity -= used
                position.cost -= used * lot.unit_cost
                lot.quantity -= used
                remaining -= used
                if lot.quantity <= 1e-12:
                    position.lots.popleft()

        elif event.type == EventType.TRANSFER_OUT.value:
            remaining = event.quantity
            moved_cost = 0.0
            while remaining > 0 and position.lots:
                lot = position.lots[0]
                used = min(remaining, lot.quantity)
                transfer_buffer[event.tx_ref].append(Lot(used, lot.unit_cost))
                moved_cost += used * lot.unit_cost
                position.quantity -= used
                position.cost -= used * lot.unit_cost
                lot.quantity -= used
                remaining -= used
                if lot.quantity <= 1e-12:
                    position.lots.popleft()
            position.cost -= fee

        elif event.type == EventType.TRANSFER_IN.value:
            lots = transfer_buffer.get(event.tx_ref)
            if lots:
                while lots:
                    lot = lots.popleft()
                    position.quantity += lot.quantity
                    position.cost += lot.quantity * lot.unit_cost
                    position.lots.append(lot)
            else:
                unit_cost = event.price
                position.quantity += event.quantity
                position.cost += event.quantity * unit_cost
                position.lots.append(Lot(event.quantity, unit_cost))

        elif event.type == EventType.FEE_PAYMENT.value:
            position.cost += fee

    return positions


def inventory_rows(events: Iterable[LedgerEvent], prices: dict[str, float]) -> list[dict]:
    rows = []
    for idx, position in enumerate(apply_events(events).values(), start=1):
        if position.quantity <= 1e-12:
            continue
        rows.append(
            {
                "id": idx,
                "sym": position.asset,
                "name": asset_name(position.asset),
                "loc": position.venue,
                "qty": round(position.quantity, 8),
                "avg": round(position.avg_cost, 6),
                "price": prices.get(position.asset, position.avg_cost),
            }
        )
    return rows


def asset_name(symbol: str) -> str:
    names = {
        "BTC": "Bitcoin",
        "WBTC": "Wrapped BTC",
        "ETH": "Ethereum",
        "WETH": "Wrapped ETH",
        "USDC": "USD Coin",
        "USDT": "Tether",
        "ARB": "Arbitrum",
        "SOL": "Solana",
    }
    return names.get(symbol, symbol)
