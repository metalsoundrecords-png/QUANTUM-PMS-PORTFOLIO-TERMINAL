from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"

# Pausa entre wallets (s) para no saturar la API pública de Hyperliquid
_WALLET_DELAY_S = 0.25


class HyperliquidConnector:
    """
    Lee el valor de cuenta en Hyperliquid via su API pública
    (https://api.hyperliquid.xyz/info). No requiere API keys: solo la
    dirección 0x... de cada wallet.

    Suma dos fuentes:
      - clearinghouseState.marginSummary.accountValue → margen + PnL de Perpetuos
      - spotClearinghouseState.balances[USDC].total   → saldo USDC en cuenta Spot
    """

    def __init__(self, wallets: dict[str, dict]) -> None:
        # wallets: {"Hedge": {"address": "0xA13C...", "category": "bot"}, ...}
        self._wallets = wallets

    def fetch_account_values(self) -> dict[str, float]:
        """Retorna {wallet_name: account_value_usd} (Perpetuos + Spot USDC) por wallet."""
        values: dict[str, float] = {}
        for name, wallet in self._wallets.items():
            address = wallet.get("address", "")
            if not address:
                continue
            perp = self._fetch_perp_value(name, address)
            spot = self._fetch_spot_usdc(name, address)
            values[name] = round(perp + spot, 2)
            time.sleep(_WALLET_DELAY_S)
        return values

    def _fetch_perp_value(self, name: str, address: str) -> float:
        data = self._post(name, "clearinghouseState", address)
        if data is None:
            return 0.0
        try:
            return float(data.get("marginSummary", {}).get("accountValue", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _fetch_spot_usdc(self, name: str, address: str) -> float:
        data = self._post(name, "spotClearinghouseState", address)
        if data is None:
            return 0.0
        for balance in data.get("balances", []):
            if balance.get("coin") == "USDC":
                try:
                    return float(balance.get("total", 0.0))
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _post(self, name: str, req_type: str, address: str) -> dict | None:
        try:
            resp = httpx.post(
                HYPERLIQUID_API,
                json={"type": req_type, "user": address},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning("Hyperliquid API error (%s) para wallet '%s': %s", req_type, name, exc)
            return None
