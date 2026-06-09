from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from .database import EventType, LedgerEvent

logger = logging.getLogger(__name__)


@dataclass
class CashBalance:
    asset: str
    venue: str
    balance: float = 0.0
    realized_pnl: float = 0.0
    event_count: int = 0


class FuturesLedger:
    """
    Ledger aislado para eventos FUTURES_PNL.

    Invariante de seguridad: este módulo NUNCA accede ni muta posiciones spot.
    Solo opera sobre saldos de caja (USDT/USDC). Cualquier intento de liquidar
    un activo no-stablecoin se rechaza con un log de advertencia.
    """

    SETTLEMENT_ASSETS: frozenset[str] = frozenset({"USDT", "USDC"})

    def __init__(self) -> None:
        self._balances: dict[tuple[str, str], CashBalance] = {}

    def process(self, events: Iterable[LedgerEvent]) -> "FuturesLedger":
        for event in events:
            if event.event_type == EventType.FUTURES_PNL.value:
                self._handle_futures_pnl(event)
        return self

    def balances(self) -> dict[tuple[str, str], CashBalance]:
        return dict(self._balances)

    def total_realized_pnl(self, asset: str | None = None) -> float:
        return sum(
            b.realized_pnl
            for b in self._balances.values()
            if asset is None or b.asset == asset
        )

    def cash_flow_rows(self) -> list[dict]:
        rows = []
        for balance in self._balances.values():
            rows.append({
                "asset": balance.asset,
                "venue": balance.venue,
                "balance": round(balance.balance, 4),
                "realized_pnl": round(balance.realized_pnl, 4),
                "event_count": balance.event_count,
            })
        return rows

    def _handle_futures_pnl(self, event: LedgerEvent) -> None:
        if event.asset not in self.SETTLEMENT_ASSETS:
            # Bloqueo de seguridad: futuros solo liquidan en stablecoins
            logger.warning(
                "FUTURES_PNL rechazado — asset '%s' no es un activo de liquidación. "
                "Solo se permiten %s. hash=%s",
                event.asset,
                self.SETTLEMENT_ASSETS,
                event.transaction_hash,
            )
            return
        key = (event.asset, event.venue)
        if key not in self._balances:
            self._balances[key] = CashBalance(asset=event.asset, venue=event.venue)
        balance = self._balances[key]
        pnl = event.quantity * (event.price or 1.0)
        balance.balance += pnl
        balance.realized_pnl += pnl
        balance.event_count += 1
