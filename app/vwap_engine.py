from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable

from .database import EventType, LedgerEvent

logger = logging.getLogger(__name__)


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


class VWAPEngine:
    """
    Motor de cálculo VWAP/FIFO para posiciones spot.

    Procesa eventos cronológicamente y mantiene el estado contable de cada
    posición (asset, venue). El método FIFO garantiza que al vender se
    descargan los lotes más antiguos primero, preservando el costo base
    de los lotes restantes.

    Invariante: los eventos FUTURES_PNL son bloqueados explícitamente.
    Este motor no conoce ni le afecta el estado del módulo futures_isolation.
    """

    def __init__(self) -> None:
        self._positions: dict[tuple[str, str], Position] = {}
        self._transfer_buffer: dict[str, deque[Lot]] = defaultdict(deque)

    def process(self, events: Iterable[LedgerEvent]) -> "VWAPEngine":
        for event in events:
            self._dispatch(event)
        return self

    def positions(self) -> dict[tuple[str, str], Position]:
        return dict(self._positions)

    def inventory_rows(self, prices: dict[str, float]) -> list[dict]:
        rows = []
        for idx, position in enumerate(self._positions.values(), start=1):
            if position.quantity <= 1e-12:
                continue
            rows.append({
                "id": idx,
                "sym": position.asset,
                "name": _asset_name(position.asset),
                "loc": position.venue,
                "qty": round(position.quantity, 8),
                "avg": round(position.avg_cost, 6),
                "price": prices.get(position.asset, position.avg_cost),
            })
        return rows

    # ------------------------------------------------------------------ #
    #  Despacho                                                            #
    # ------------------------------------------------------------------ #

    def _dispatch(self, event: LedgerEvent) -> None:
        if event.event_type == EventType.FUTURES_PNL.value:
            return  # firewall spot/futures — nunca procesar aquí
        handlers = {
            EventType.BUY.value: self._handle_buy,
            EventType.SELL.value: self._handle_sell,
            EventType.TRANSFER_OUT.value: self._handle_transfer_out,
            EventType.TRANSFER_IN.value: self._handle_transfer_in,
            EventType.FEE.value: self._handle_fee,
        }
        handler = handlers.get(event.event_type)
        if handler:
            handler(event)

    # ------------------------------------------------------------------ #
    #  Handlers por tipo de evento                                         #
    # ------------------------------------------------------------------ #

    def _handle_buy(self, event: LedgerEvent) -> None:
        """
        VWAP = (Costo Total Antiguo + Costo Nuevo) / (Cant. Antigua + Cant. Nueva)
        El fee de compra se incluye en el costo del lote.
        """
        position = self._get(event.asset, event.venue)
        total_cost = event.quantity * event.price + (event.fee or 0.0)
        unit_cost = total_cost / event.quantity if event.quantity else 0.0
        position.quantity += event.quantity
        position.cost += total_cost
        position.lots.append(Lot(event.quantity, unit_cost))

    def _handle_sell(self, event: LedgerEvent) -> None:
        """
        FIFO: descarga lotes más antiguos primero.
        El VWAP de los lotes restantes no se altera porque el costo se reduce
        proporcionalmente al unit_cost del lote descargado.
        """
        position = self._get(event.asset, event.venue)
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

    def _handle_transfer_out(self, event: LedgerEvent) -> None:
        """
        Mueve lotes FIFO al buffer temporal vinculado al transaction_hash.
        El fee de gas se registra como pérdida operativa (reduce el costo base
        de la posición origen, no el de los lotes en tránsito).
        """
        position = self._get(event.asset, event.venue)
        key = event.transaction_hash
        remaining = event.quantity
        while remaining > 0 and position.lots:
            lot = position.lots[0]
            used = min(remaining, lot.quantity)
            self._transfer_buffer[key].append(Lot(used, lot.unit_cost))
            position.quantity -= used
            position.cost -= used * lot.unit_cost
            lot.quantity -= used
            remaining -= used
            if lot.quantity <= 1e-12:
                position.lots.popleft()
        position.cost -= (event.fee or 0.0)

    def _handle_transfer_in(self, event: LedgerEvent) -> None:
        """
        Recupera lotes del buffer por transaction_hash para preservar el costo base.
        Si no existe un TRANSFER_OUT previo con el mismo hash, se registra una
        advertencia de auditoría — NO se asume precio de mercado en silencio.
        """
        position = self._get(event.asset, event.venue)
        key = event.transaction_hash
        lots = self._transfer_buffer.get(key)
        if lots:
            while lots:
                lot = lots.popleft()
                position.quantity += lot.quantity
                position.cost += lot.quantity * lot.unit_cost
                position.lots.append(lot)
        else:
            # Advertencia de auditoría: ingreso sin TRANSFER_OUT registrado
            logger.warning(
                "TRANSFER_IN sin buffer previo — asset=%s venue=%s hash=%s. "
                "Se usará event.price=%s como costo base. Verificar integridad del ledger.",
                event.asset,
                event.venue,
                event.transaction_hash,
                event.price,
            )
            unit_cost = event.price
            position.quantity += event.quantity
            position.cost += event.quantity * unit_cost
            position.lots.append(Lot(event.quantity, unit_cost))

    def _handle_fee(self, event: LedgerEvent) -> None:
        """El pago de fee aislado incrementa el costo base del activo."""
        position = self._get(event.asset, event.venue)
        position.cost += (event.fee or 0.0)

    def _get(self, asset: str, venue: str) -> Position:
        key = (asset, venue)
        if key not in self._positions:
            self._positions[key] = Position(asset=asset, venue=venue)
        return self._positions[key]


# ------------------------------------------------------------------ #
#  Funciones de compatibilidad para main.py                           #
# ------------------------------------------------------------------ #

def inventory_rows(events: Iterable[LedgerEvent], prices: dict[str, float]) -> list[dict]:
    """Wrapper funcional sobre VWAPEngine — mantiene la firma que consume main.py."""
    return VWAPEngine().process(events).inventory_rows(prices)


def _asset_name(symbol: str) -> str:
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
