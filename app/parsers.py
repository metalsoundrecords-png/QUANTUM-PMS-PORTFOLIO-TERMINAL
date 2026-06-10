from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

from .database import EventType, LedgerEvent

log = logging.getLogger(__name__)


# ── Formatos soportados ────────────────────────────────────────────────────

# BingX Spot — Historial de Órdenes
# Time,Symbol,Side,Price,Amount,Total,Fee,FeeCoin,Status
_BINGX_SPOT_HEADERS = {"time", "symbol", "side", "price", "amount", "total"}

# BingX Spot — Historial de Transacciones (formato alternativo)
# Date,Type,Coin,Amount,Price,Total(USDT),Fee,Status
_BINGX_TX_HEADERS = {"date", "type", "coin", "amount", "price"}

# Binance — Trade History
# Date(UTC),Pair,Side,Price,Executed,Amount,Fee
_BINANCE_HEADERS = {"date(utc)", "pair", "side", "price", "executed"}

# Genérico mínimo — cualquier CSV con estas columnas
_GENERIC_HEADERS = {"asset", "side", "price", "quantity"}


def _normalize_headers(row: dict) -> dict:
    return {k.strip().lower(): v.strip() for k, v in row.items()}


def _detect_format(headers: set[str]) -> str:
    h = {h.strip().lower() for h in headers}
    if _BINGX_SPOT_HEADERS.issubset(h):
        return "bingx_spot"
    if _BINGX_TX_HEADERS.issubset(h):
        return "bingx_tx"
    if _BINANCE_HEADERS.issubset(h):
        return "binance"
    if _GENERIC_HEADERS.issubset(h):
        return "generic"
    return "unknown"


def _parse_ts(value: str) -> datetime:
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _extract_symbol(pair: str) -> tuple[str, str]:
    """'BTC/USDT' → ('BTC', 'USDT'),  'BTCUSDT' → ('BTC', 'USDT')."""
    pair = pair.upper().replace("-", "/")
    if "/" in pair:
        parts = pair.split("/")
        return parts[0], parts[1]
    for quote in ("USDT", "USDC", "BTC", "ETH", "BNB"):
        if pair.endswith(quote):
            return pair[: -len(quote)], quote
    return pair, "USDT"


def _safe_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value.replace(",", "").strip())
    except (ValueError, AttributeError):
        return default


# ── Parsers por formato ────────────────────────────────────────────────────

def _parse_bingx_spot(rows: list[dict]) -> list[LedgerEvent]:
    events: list[LedgerEvent] = []
    for i, raw in enumerate(rows):
        r = _normalize_headers(raw)
        side = r.get("side", "").upper()
        if side not in ("BUY", "SELL"):
            continue
        symbol_raw = r.get("symbol", "")
        asset, quote = _extract_symbol(symbol_raw)
        price = _safe_float(r.get("price", "0"))
        qty = _safe_float(r.get("amount", "0"))
        fee_raw = r.get("fee", "0")
        fee = _safe_float(fee_raw)
        ts = _parse_ts(r.get("time", ""))
        tx_hash = f"bingx-{side.lower()}-{asset}-{qty:.8g}-{price:.8g}-{int(ts.timestamp())}"
        events.append(LedgerEvent(
            event_type=EventType.BUY.value if side == "BUY" else EventType.SELL.value,
            asset=asset,
            venue="BingX",
            quantity=qty,
            price=price,
            fee=fee,
            transaction_hash=tx_hash,
            timestamp=ts,
        ))
    return events


def _parse_bingx_tx(rows: list[dict]) -> list[LedgerEvent]:
    events: list[LedgerEvent] = []
    for i, raw in enumerate(rows):
        r = _normalize_headers(raw)
        tx_type = r.get("type", "").upper()
        if tx_type not in ("BUY", "SELL", "BUY_MARKET", "SELL_MARKET"):
            continue
        asset = r.get("coin", "").upper()
        price = _safe_float(r.get("price", "0"))
        qty = _safe_float(r.get("amount", "0"))
        ts = _parse_ts(r.get("date", ""))
        side = "BUY" if "BUY" in tx_type else "SELL"
        tx_hash = f"bingx-{side.lower()}-{asset}-{qty:.8g}-{price:.8g}-{int(ts.timestamp())}"
        events.append(LedgerEvent(
            event_type=EventType.BUY.value if side == "BUY" else EventType.SELL.value,
            asset=asset,
            venue="BingX",
            quantity=qty,
            price=price,
            transaction_hash=tx_hash,
            timestamp=ts,
        ))
    return events


def _parse_binance(rows: list[dict]) -> list[LedgerEvent]:
    events: list[LedgerEvent] = []
    for i, raw in enumerate(rows):
        r = _normalize_headers(raw)
        side = r.get("side", "").upper()
        if side not in ("BUY", "SELL"):
            continue
        pair = r.get("pair", "")
        asset, _ = _extract_symbol(pair)
        price = _safe_float(r.get("price", "0"))
        executed = r.get("executed", "0")
        qty = _safe_float(executed.split()[0] if executed else "0")
        fee_raw = r.get("fee", "0")
        fee = _safe_float(fee_raw.split()[0] if fee_raw else "0")
        ts = _parse_ts(r.get("date(utc)", ""))
        tx_hash = f"binance-{side.lower()}-{asset}-{i}-{int(ts.timestamp())}"
        events.append(LedgerEvent(
            event_type=EventType.BUY.value if side == "BUY" else EventType.SELL.value,
            asset=asset,
            venue="Binance",
            quantity=qty,
            price=price,
            fee=fee,
            transaction_hash=tx_hash,
            timestamp=ts,
        ))
    return events


def _parse_generic(rows: list[dict]) -> list[LedgerEvent]:
    events: list[LedgerEvent] = []
    for i, raw in enumerate(rows):
        r = _normalize_headers(raw)
        side = r.get("side", "").upper()
        asset = r.get("asset", "").upper()
        price = _safe_float(r.get("price", "0"))
        qty = _safe_float(r.get("quantity", "0"))
        ts = _parse_ts(r.get("date", r.get("time", "")))
        if side not in ("BUY", "SELL") or not asset:
            continue
        tx_hash = f"generic-{side.lower()}-{asset}-{i}"
        events.append(LedgerEvent(
            event_type=EventType.BUY.value if side == "BUY" else EventType.SELL.value,
            asset=asset,
            venue="Import",
            quantity=qty,
            price=price,
            transaction_hash=tx_hash,
            timestamp=ts,
        ))
    return events


# ── Punto de entrada público ───────────────────────────────────────────────

def parse_csv(content: bytes) -> tuple[list[LedgerEvent], str, list[str]]:
    """
    Parsea un CSV de exchange y retorna (eventos, formato_detectado, errores).
    Deduplica por transaction_hash.
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], "empty", ["CSV vacío o sin filas de datos"]

    fmt = _detect_format(set(rows[0].keys()))
    log.info("CSV format detected: %s (%d rows)", fmt, len(rows))

    parsers = {
        "bingx_spot": _parse_bingx_spot,
        "bingx_tx":   _parse_bingx_tx,
        "binance":    _parse_binance,
        "generic":    _parse_generic,
    }
    parser_fn = parsers.get(fmt)
    if not parser_fn:
        return [], fmt, [
            f"Formato no reconocido. Encabezados detectados: {list(rows[0].keys())[:8]}. "
            "Formatos soportados: BingX Spot, BingX Transacciones, Binance."
        ]

    events = parser_fn(rows)
    # Deduplicar por transaction_hash
    seen: set[str] = set()
    unique: list[LedgerEvent] = []
    for e in events:
        if e.transaction_hash not in seen:
            seen.add(e.transaction_hash)
            unique.append(e)

    skipped = len(events) - len(unique)
    warnings = []
    if skipped:
        warnings.append(f"{skipped} filas duplicadas omitidas")

    return unique, fmt, warnings
