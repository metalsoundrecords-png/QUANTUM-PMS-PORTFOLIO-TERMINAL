from __future__ import annotations

import asyncio
import logging
import random

from fastapi import Depends, FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import settings_store
from .config import STATIC_DIR, SYNC_INTERVAL_MINUTES
from .connectors.arbitrum import ArbitrumConnector
from .connectors.bingx import BingXConnector
from .database import LedgerEvent, SessionLocal, init_db
from .futures_isolation import FuturesLedger
from .parsers import parse_csv
from .prices import FALLBACK_PRICES, fetch_prices
from .seed import seed_database
from .vwap_engine import VWAPEngine

log = logging.getLogger(__name__)

EVENT_LABELS = {
    "BUY": "COMPRA",
    "SELL": "VENTA",
    "TRANSFER_OUT": "TRANSFERENCIA",
    "TRANSFER_IN": "TRANSFERENCIA",
    "FUTURES_PNL": "FUTURES PNL",
    "FEE": "COMISIÓN",
}


class SettingsUpdate(BaseModel):
    bingx_api_key: str | None = None
    bingx_api_secret: str | None = None
    arbitrum_rpc_url: str | None = None
    arbitrum_wallets: dict[str, str] | None = None


app = FastAPI(title="Quantum PMS", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
async def startup() -> None:
    init_db()
    with SessionLocal() as db:
        seed_database(db)
    asyncio.create_task(_bingx_sync_loop())
    log.info("BingX auto-sync programado (intervalo: %d min)", SYNC_INTERVAL_MINUTES)


async def _bingx_sync_loop() -> None:
    """Tarea de fondo: sincroniza BingX cada SYNC_INTERVAL_MINUTES minutos."""
    while True:
        await asyncio.sleep(SYNC_INTERVAL_MINUTES * 60)
        cfg = settings_store.get_config()
        if not cfg["bingx_api_key"] or not cfg["bingx_api_secret"]:
            continue
        try:
            connector = BingXConnector(cfg["bingx_api_key"], cfg["bingx_api_secret"])
            perms = connector.check_permissions()
            if perms["blocked"]:
                log.error("BingX sync automático BLOQUEADO: %s", perms["warnings"][-1])
                continue
            with SessionLocal() as db:
                stats = connector.sync(db)
            log.info("BingX sync automático: %s", stats)
        except Exception as exc:
            log.error("BingX sync error: %s", exc)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "quantum-pms"}


@app.get("/api/snapshot")
def snapshot(db: Session = Depends(get_db)) -> dict:
    events = (
        db.query(LedgerEvent)
        .order_by(LedgerEvent.timestamp.asc(), LedgerEvent.id.asc())
        .all()
    )

    spot = VWAPEngine().process(events)
    futures = FuturesLedger().process(events)
    prices = fetch_prices(list(FALLBACK_PRICES.keys()))

    arbitrum_rows = _arbitrum_rows(prices)
    inventory = spot.inventory_rows(prices)
    inventory += arbitrum_rows

    return {
        "inventory": inventory,
        "events": _event_feed(events),
        "futures_cash": futures.cash_flow_rows(),  # saldos reales de caja
        "futures_pnl_total": futures.total_realized_pnl(),
        "futures_stable_equity": _stable_equity(arbitrum_rows),  # USDT/USDC reales en wallets Arbitrum
    }


@app.post("/api/import/csv")
async def import_csv(file: UploadFile, db: Session = Depends(get_db)) -> dict:
    raw = await file.read()
    events, fmt, warnings = parse_csv(raw)
    if not events:
        return {"filename": file.filename, "fills": 0, "status": "error",
                "format": fmt, "warnings": warnings}

    # Evitar duplicados: filtrar transaction_hash ya existentes
    existing = {h for (h,) in db.query(LedgerEvent.transaction_hash).all()}
    new_events = [e for e in events if e.transaction_hash not in existing]
    db.add_all(new_events)
    db.commit()

    return {
        "filename": file.filename,
        "fills": len(new_events),
        "duplicates_skipped": len(events) - len(new_events),
        "format": fmt,
        "status": "ok",
        "warnings": warnings,
    }


@app.get("/api/events")
def list_events(
    asset: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    """Historial completo de LedgerEvent (memoria del sistema), ordenado por fecha descendente."""
    query = db.query(LedgerEvent)
    if asset:
        query = query.filter(LedgerEvent.asset == asset.upper())
    if search:
        like = f"%{search}%"
        query = query.filter(
            (LedgerEvent.transaction_hash.ilike(like)) | (LedgerEvent.venue.ilike(like))
        )
    total = query.count()
    rows = (
        query.order_by(LedgerEvent.timestamp.desc(), LedgerEvent.id.desc())
        .offset(offset)
        .limit(min(limit, 1000))
        .all()
    )
    return {"total": total, "events": [_event_to_dict(e) for e in rows]}


@app.get("/api/settings")
def get_settings() -> dict:
    """Configuración actual con secretos ofuscados (solo se muestran los últimos 4 caracteres)."""
    cfg = settings_store.get_config()
    return {
        "bingx_api_key": settings_store.obfuscate_secret(cfg["bingx_api_key"]),
        "bingx_api_secret": settings_store.obfuscate_secret(cfg["bingx_api_secret"]),
        "bingx_configured": bool(cfg["bingx_api_key"] and cfg["bingx_api_secret"]),
        "arbitrum_rpc_url": settings_store.obfuscate_url(cfg["arbitrum_rpc_url"]),
        "arbitrum_configured": bool(cfg["arbitrum_rpc_url"]),
        "arbitrum_wallets": cfg["arbitrum_wallets"],
    }


@app.post("/api/settings")
def post_settings(payload: SettingsUpdate) -> dict:
    """Guarda overrides de configuración en data/settings.json (no toca .env)."""
    settings_store.update_config(payload.model_dump(exclude_none=True))
    return get_settings()


@app.get("/api/pnl")
def pnl_breakdown(db: Session = Depends(get_db)) -> dict:
    """Desglose de PnL por activo: costo total, valor de mercado, PnL no realizado/realizado, top/worst."""
    events = (
        db.query(LedgerEvent)
        .order_by(LedgerEvent.timestamp.asc(), LedgerEvent.id.asc())
        .all()
    )
    spot = VWAPEngine().process(events)
    prices = fetch_prices(list(FALLBACK_PRICES.keys()))
    return spot.pnl_summary(prices)


@app.get("/api/sync/arbitrum")
def sync_arbitrum() -> dict:
    """Lectura en vivo de balances Arbitrum para las wallets configuradas."""
    cfg = settings_store.get_config()
    if not cfg["arbitrum_rpc_url"]:
        return {"status": "error", "message": "Falta configurar la URL RPC de Arbitrum (ver Configuración)."}
    if not cfg["arbitrum_wallets"]:
        return {"status": "error", "message": "Falta configurar wallets de Arbitrum (ver Configuración)."}
    connector = ArbitrumConnector(cfg["arbitrum_rpc_url"], cfg["arbitrum_wallets"])
    rows = connector.fetch_balances()
    prices = fetch_prices(list(FALLBACK_PRICES.keys()))
    for row in rows:
        row["price"] = prices.get(row["sym"], 0.0)
    return {"status": "ok", "wallets": len(cfg["arbitrum_wallets"]), "rows": rows}


@app.post("/api/sync/bingx")
def sync_bingx(db: Session = Depends(get_db)) -> dict:
    """Sincronización manual con BingX API. Requiere claves configuradas (ver Configuración o .env)."""
    cfg = settings_store.get_config()
    if not cfg["bingx_api_key"] or not cfg["bingx_api_secret"]:
        return {
            "status": "error",
            "message": "Faltan credenciales. Configúralas en Configuración o en tu archivo .env.",
        }

    connector = BingXConnector(cfg["bingx_api_key"], cfg["bingx_api_secret"])

    perms = connector.check_permissions()
    if not perms["connected"]:
        return {"status": "error", "message": perms["warnings"][0] if perms["warnings"] else "Sin conexión"}

    if perms["blocked"]:
        return {"status": "blocked", "message": perms["warnings"][-1], "warnings": perms["warnings"]}

    stats = connector.sync(db)
    return {
        "status": "ok",
        "fetched": stats["fetched"],
        "new": stats["new"],
        "duplicates_skipped": stats["duplicates_skipped"],
        "warnings": perms["warnings"],
    }


@app.websocket("/ws/live")
async def live_feed(websocket: WebSocket) -> None:
    await websocket.accept()
    pool = [
        {"type": "SYNC", "parts": ["BingX API heartbeat ", {"mono": "solo lectura"}]},
        {"type": "TRANSFER", "parts": ["Snapshot de pool Arbitrum reconciliado ", {"mono": "VWAP preservado"}]},
        {"type": "FUTURES PNL", "parts": [{"mono": "+$210.00 USDT"}, " realizado ", {"mono": "aislado"}]},
        {"type": "COMPRA", "parts": ["0.08 ETH comprado @ BingX ", {"mono": "VWAP actualizado"}]},
    ]
    try:
        while True:
            await asyncio.sleep(5)
            await websocket.send_json({"kind": "event", "event": random.choice(pool)})
    except WebSocketDisconnect:
        return


def _event_feed(events: list[LedgerEvent]) -> list[dict]:
    feed = []
    for event in reversed(events[-10:]):
        feed.append({
            "type": EVENT_LABELS.get(event.event_type, "SYNC"),
            "parts": [
                f"{event.quantity:g} {event.asset} @ {event.venue} ",
                {"mono": event.transaction_hash},
            ],
            "ago": "seed",
        })
    return feed


def _event_to_dict(event: LedgerEvent) -> dict:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "label": EVENT_LABELS.get(event.event_type, event.event_type),
        "asset": event.asset,
        "venue": event.venue,
        "destination": event.destination,
        "quantity": event.quantity,
        "price": event.price,
        "fee": event.fee,
        "transaction_hash": event.transaction_hash,
        "timestamp": event.timestamp.isoformat(),
    }


def _arbitrum_rows(prices: dict[str, float]) -> list[dict]:
    """
    Obtiene balances en vivo de las wallets Arbitrum configuradas.
    Retorna lista vacía si falta RPC o wallets (no rompe el snapshot).
    """
    cfg = settings_store.get_config()
    if not cfg["arbitrum_rpc_url"] or not cfg["arbitrum_wallets"]:
        return []
    try:
        connector = ArbitrumConnector(cfg["arbitrum_rpc_url"], cfg["arbitrum_wallets"])
        rows = connector.fetch_balances()
        for row in rows:
            price = prices.get(row["sym"], 0.0)
            qty = row["qty"]
            # Sin historial de trades → no hay costo base. avg=price para que
            # el PnL no realizado sea 0 en vez de mostrar una ganancia falsa del 100%.
            row["price"] = price
            row["avg"] = price
            row["cost_total"] = round(price * qty, 2)
            row["market_value"] = round(price * qty, 2)
            row["pnl_usd"] = 0.0
            row["pnl_pct"] = 0.0
            row["realized_pnl"] = 0.0
        return rows
    except Exception as exc:
        log.warning("Arbitrum snapshot error: %s", exc)
        return []


def _stable_equity(arbitrum_rows: list[dict]) -> float:
    """Suma real de USDT/USDC encontrados en las wallets Arbitrum del usuario."""
    return round(
        sum(row["market_value"] for row in arbitrum_rows if row["sym"] in ("USDT", "USDC")),
        2,
    )
