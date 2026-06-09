from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict[str, float] = {}
_CACHE_TS: float = 0.0
_CACHE_TTL: float = 45.0  # segundos

_BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"

# Activos que no tienen par directo XUSDT en Binance
_SYMBOL_MAP: dict[str, str] = {
    "WBTC": "BTCUSDT",
    "WETH": "ETHUSDT",
}

FALLBACK_PRICES: dict[str, float] = {
    "BTC": 68240.50,
    "WBTC": 68190.00,
    "ETH": 3548.75,
    "WETH": 3547.20,
    "USDC": 1.0001,
    "USDT": 0.9998,
    "ARB": 0.9180,
    "SOL": 168.90,
}


def fetch_prices(symbols: list[str]) -> dict[str, float]:
    """
    Obtiene precios en tiempo real desde la API pública de Binance.
    Cache de 45 segundos para no saturar el endpoint gratuito.
    Si la API falla, retorna los precios estáticos de respaldo.
    """
    global _CACHE, _CACHE_TS

    now = time.monotonic()
    if _CACHE and (now - _CACHE_TS) < _CACHE_TTL:
        return dict(_CACHE)

    try:
        prices = _fetch_from_binance(symbols)
        _CACHE = prices
        _CACHE_TS = now
        return prices
    except Exception as exc:
        logger.warning("Binance API no disponible (%s). Usando precios estáticos.", exc)
        return dict(FALLBACK_PRICES)


def _fetch_from_binance(symbols: list[str]) -> dict[str, float]:
    binance_symbols = {sym: _to_binance_symbol(sym) for sym in symbols}
    unique_pairs = list(set(binance_symbols.values()))

    prices: dict[str, float] = {}
    with httpx.Client(timeout=5.0) as client:
        for pair in unique_pairs:
            try:
                resp = client.get(_BINANCE_URL, params={"symbol": pair})
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                prices[pair] = float(data["price"])
            except Exception as exc:
                logger.warning("No se pudo obtener %s de Binance: %s", pair, exc)

    result: dict[str, float] = {}
    for sym in symbols:
        pair = binance_symbols[sym]
        if pair in prices:
            result[sym] = prices[pair]
        elif sym in FALLBACK_PRICES:
            result[sym] = FALLBACK_PRICES[sym]
    return result


def _to_binance_symbol(asset: str) -> str:
    if asset in _SYMBOL_MAP:
        return _SYMBOL_MAP[asset]
    if asset in ("USDT", "USDC"):
        return "USDCUSDT" if asset == "USDC" else "USDTUSD"
    return f"{asset}USDT"
