from __future__ import annotations

from sqlalchemy.orm import Session

from .database import LedgerEvent


def seed_database(db: Session) -> None:
    """Base de datos vacía por defecto. Los datos reales vienen del CSV del usuario (MAR-19)."""
    if db.query(LedgerEvent).first():
        return
    # Sin datos demo — el usuario importa su historial real via CSV o API BingX
