from __future__ import annotations

import asyncio
import random

from fastapi import Depends, FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import STATIC_DIR
from .database import LedgerEvent, SessionLocal, init_db
from .futures_isolation import FuturesLedger
from .prices import FALLBACK_PRICES, fetch_prices
from .seed import seed_database
from .vwap_engine import VWAPEngine


app = FastAPI(title="Quantum PMS", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def startup() -> None:
    init_db()
    with SessionLocal() as db:
        seed_database(db)


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

    return {
        "inventory": spot.inventory_rows(prices),
        "events": _event_feed(events),
        "futures": _futures_chart_rows(),          # formato {d, flow, equity} para el gráfico
        "futures_cash": futures.cash_flow_rows(),  # saldos reales de caja
        "futures_pnl_total": futures.total_realized_pnl("USDT"),
    }


@app.post("/api/import/csv")
async def import_csv(file: UploadFile) -> dict:
    raw = await file.read()
    fills = max(1, raw.count(b"\n") - 1)
    return {"filename": file.filename, "fills": fills, "status": "accepted"}


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
    labels = {
        "BUY": "COMPRA",
        "SELL": "VENTA",
        "TRANSFER_OUT": "TRANSFERENCIA",
        "TRANSFER_IN": "TRANSFERENCIA",
        "FUTURES_PNL": "FUTURES PNL",
        "FEE": "COMISIÓN",
    }
    feed = []
    for event in reversed(events[-10:]):
        feed.append({
            "type": labels.get(event.event_type, "SYNC"),
            "parts": [
                f"{event.quantity:g} {event.asset} @ {event.venue} ",
                {"mono": event.transaction_hash},
            ],
            "ago": "seed",
        })
    return feed


def _futures_chart_rows() -> list[dict]:
    """Datos simulados para el gráfico (formato {d, flow, equity}). Se reemplazará con histórico real en Epic 3."""
    flows = [320, -180, 450, 210, -120, 680, 140, -260, 520, 390, -90, 610, 240, 450]
    base = 24500
    rows = []
    for idx, flow in enumerate(flows, start=1):
        base += flow
        rows.append({"d": f"D-{15 - idx:02d}", "flow": flow, "equity": base})
    return rows
