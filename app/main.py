from __future__ import annotations

import asyncio
import random
from pathlib import Path

from fastapi import Depends, FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import STATIC_DIR
from .ledger import inventory_rows
from .models import LedgerEvent, SessionLocal, init_db
from .seed import PRICES, seed_database


app = FastAPI(title="Quantum PMS", version="0.1.0")
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
    events = db.query(LedgerEvent).order_by(LedgerEvent.created_at.asc(), LedgerEvent.id.asc()).all()
    return {
        "inventory": inventory_rows(events, PRICES),
        "events": event_feed(events),
        "futures": futures_flow(),
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
        {"type": "SYNC", "parts": ["BingX API heartbeat ", {"mono": "read-only"}]},
        {"type": "TRANSFER", "parts": ["Arbitrum pool snapshot reconciled ", {"mono": "VWAP preserved"}]},
        {"type": "FUTURES PNL", "parts": [{"mono": "+$210.00 USDT"}, " realized ", {"mono": "isolated"}]},
        {"type": "TRADE BUY", "parts": ["0.08 ETH bought @ BingX ", {"mono": "VWAP updated"}]},
    ]
    try:
        while True:
            await asyncio.sleep(5)
            await websocket.send_json({"kind": "event", "event": random.choice(pool)})
    except WebSocketDisconnect:
        return


def event_feed(events: list[LedgerEvent]) -> list[dict]:
    labels = {
        "TRADE_BUY": "TRADE BUY",
        "TRADE_SELL": "TRADE SELL",
        "TRANSFER_OUT": "TRANSFER",
        "TRANSFER_IN": "TRANSFER",
        "FUTURES_PNL": "FUTURES PNL",
        "FEE_PAYMENT": "SYNC",
    }
    feed = []
    for event in reversed(events[-10:]):
        feed.append(
            {
                "type": labels.get(event.type, "SYNC"),
                "parts": [f"{event.quantity:g} {event.asset} @ {event.venue} ", {"mono": event.tx_ref}],
                "ago": "seed",
            }
        )
    return feed


def futures_flow() -> list[dict]:
    flows = [320, -180, 450, 210, -120, 680, 140, -260, 520, 390, -90, 610, 240, 450]
    base = 24500
    rows = []
    for idx, flow in enumerate(flows, start=1):
        base += flow
        rows.append({"d": f"D-{15 - idx:02d}", "flow": flow, "equity": base})
    return rows
