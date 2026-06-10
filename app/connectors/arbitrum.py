from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

# ── Tokens en Arbitrum One ─────────────────────────────────────────────────
# (dirección, decimales)
TOKENS: dict[str, tuple[str, int]] = {
    "WBTC": ("0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", 8),
    "WETH": ("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", 18),
    "USDC": ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
    "USDT": ("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6),
    "ARB":  ("0x912CE59144191C1204E64559FE8253a0e49E6548", 18),
}

# balanceOf(address) selector
_BALANCE_OF_SELECTOR = "0x70a08231"

# Pausa entre wallets (ms) para no saturar el rate-limit de Alchemy
_WALLET_DELAY_S = 0.25


def _encode_call(token_addr: str, wallet_addr: str) -> str:
    """Construye el campo 'data' para eth_call balanceOf(wallet)."""
    # ABI encoding: selector (4 bytes) + address padded a 32 bytes
    padded_wallet = wallet_addr[2:].lower().zfill(64)
    return _BALANCE_OF_SELECTOR + padded_wallet


def _decode_uint256(hex_result: str) -> int:
    """Decodifica la respuesta hex de eth_call a entero."""
    if not hex_result or hex_result == "0x":
        return 0
    return int(hex_result, 16)


class ArbitrumConnector:
    """
    Lee balances ERC-20 de múltiples wallets en Arbitrum One via JSON-RPC.
    Agrupa las llamadas por wallet (batch de 4 tokens) con pausa entre wallets
    para respetar los límites de Alchemy.
    """

    def __init__(self, rpc_url: str, wallets: dict[str, dict]) -> None:
        # wallets: {"Hedge": {"address": "0xA13C...", "category": "bot"}, ...}
        self._rpc = rpc_url
        self._wallets = wallets

    def fetch_balances(self) -> list[dict]:
        """
        Retorna filas de inventario con el formato de VWAPEngine.inventory_rows():
        {id, sym, name, loc, qty, avg, price, category}
        avg=0 porque no hay datos de costo base desde la wallet.
        loc = "Arb: {nombre_wallet}"
        category = "spot" | "bot", según la configuración de la wallet.
        Incluye todos los tokens configurados, incluso con qty = 0
        (para que la wallet siempre aparezca en su tabla).
        """
        rows: list[dict] = []
        row_id = 1000  # IDs altos para no colisionar con los del VWAPEngine

        for wallet_name, wallet in self._wallets.items():
            wallet_rows = self._fetch_wallet(wallet_name, wallet["address"])
            for row in wallet_rows:
                row["id"] = row_id
                row["category"] = wallet.get("category", "bot")
                row["bot_type"] = wallet.get("bot_type", "Hedge")
                rows.append(row)
                row_id += 1
            time.sleep(_WALLET_DELAY_S)

        log.info("Arbitrum: %d balances no-cero en %d wallets",
                 len(rows), len(self._wallets))
        return rows

    def _fetch_wallet(self, name: str, address: str) -> list[dict]:
        """Batch de 4 eth_call para todos los tokens de una wallet."""
        batch = [
            {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [
                    {"to": token_addr, "data": _encode_call(token_addr, address)},
                    "latest",
                ],
                "id": i,
            }
            for i, (symbol, (token_addr, _)) in enumerate(TOKENS.items())
        ]

        try:
            resp = httpx.post(self._rpc, json=batch, timeout=10.0)
            resp.raise_for_status()
            results = resp.json()
        except Exception as exc:
            log.warning("Arbitrum RPC error para wallet '%s': %s", name, exc)
            return []

        # Mapear id → símbolo para parsear la respuesta del batch
        id_to_symbol = {i: sym for i, sym in enumerate(TOKENS)}

        rows: list[dict] = []
        for item in results:
            if "error" in item:
                log.debug("Arbitrum RPC error en item %s: %s", item.get("id"), item["error"])
                continue

            symbol = id_to_symbol.get(item.get("id", -1))
            if not symbol:
                continue

            _, decimals = TOKENS[symbol]
            raw = _decode_uint256(item.get("result", "0x0"))
            qty = raw / (10 ** decimals)

            rows.append({
                "sym":   symbol,
                "name":  _token_name(symbol),
                "loc":   f"Arb: {name}",
                "qty":   round(qty, 8),
                "avg":   0.0,  # sin datos de costo base desde wallet
                "price": 0.0,  # el snapshot de main.py rellena esto con precios reales
            })

        return rows


def _token_name(symbol: str) -> str:
    return {
        "WBTC": "Wrapped Bitcoin",
        "WETH": "Wrapped Ether",
        "USDC": "USD Coin",
        "USDT": "Tether USD",
        "ARB":  "Arbitrum",
    }.get(symbol, symbol)
