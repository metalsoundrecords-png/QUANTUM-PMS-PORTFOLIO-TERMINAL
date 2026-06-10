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
    realized_pnl: float = 0.0
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
            price = prices.get(position.asset, position.avg_cost)
            avg = position.avg_cost
            qty = position.quantity
            cost_total = avg * qty
            market_value = price * qty
            pnl_usd = market_value - cost_total
            pnl_pct = ((price - avg) / avg * 100) if avg > 1e-12 else 0.0
            rows.append({
                "id": idx,
                "sym": position.asset,
                "name": _asset_name(position.asset),
                "loc": position.venue,
                "qty": round(qty, 8),
                "avg": round(avg, 6),
                "price": price,
                "cost_total": round(cost_total, 2),
                "market_value": round(market_value, 2),
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "realized_pnl": round(position.realized_pnl, 2),
            })
        return rows

    def pnl_summary(self, prices: dict[str, float]) -> dict:
        """
        Desglose de PnL agregado por activo (cruzando venues).

        Para cada activo retorna: cantidad total, costo total invertido,
        valor de mercado, PnL no realizado ($/%) y PnL realizado acumulado.

        top/worst identifican el mejor y peor activo por pnl_pct, considerando
        solo posiciones con costo base > 0 (evita ruido de posiciones cerradas).
        """
        by_asset: dict[str, dict] = {}
        for position in self._positions.values():
            has_position = position.quantity > 1e-12
            has_realized = abs(position.realized_pnl) > 1e-12
            if not has_position and not has_realized:
                continue
            price = prices.get(position.asset, position.avg_cost)
            agg = by_asset.setdefault(position.asset, {
                "sym": position.asset,
                "name": _asset_name(position.asset),
                "qty": 0.0,
                "cost_total": 0.0,
                "market_value": 0.0,
                "realized_pnl": 0.0,
            })
            agg["qty"] += position.quantity
            agg["cost_total"] += position.avg_cost * position.quantity
            agg["market_value"] += price * position.quantity
            agg["realized_pnl"] += position.realized_pnl

        rows = []
        for asset, agg in by_asset.items():
            cost_total = agg["cost_total"]
            market_value = agg["market_value"]
            pnl_usd = market_value - cost_total
            pnl_pct = (pnl_usd / cost_total * 100) if cost_total > 1e-12 else 0.0
            rows.append({
                "sym": asset,
                "name": agg["name"],
                "qty": round(agg["qty"], 8),
                "avg": round(cost_total / agg["qty"], 6) if agg["qty"] > 1e-12 else 0.0,
                "cost_total": round(cost_total, 2),
                "market_value": round(market_value, 2),
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "realized_pnl": round(agg["realized_pnl"], 2),
            })

        ranked = [r for r in rows if r["cost_total"] > 1e-12]
        ranked.sort(key=lambda r: r["pnl_pct"], reverse=True)
        top = ranked[0] if ranked else None
        worst = ranked[-1] if len(ranked) > 1 else None

        return {"assets": rows, "top": top, "worst": worst}

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

        PnL Realizado = (precio_venta - unit_cost del lote) × cantidad descargada,
        acumulado por posición. El fee de la venta se descuenta una sola vez.
        """
        position = self._get(event.asset, event.venue)
        remaining = event.quantity
        sale_price = event.price
        while remaining > 0 and position.lots:
            lot = position.lots[0]
            used = min(remaining, lot.quantity)
            position.realized_pnl += used * (sale_price - lot.unit_cost)
            position.quantity -= used
            position.cost -= used * lot.unit_cost
            lot.quantity -= used
            remaining -= used
            if lot.quantity <= 1e-12:
                position.lots.popleft()
        position.realized_pnl -= (event.fee or 0.0)

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
