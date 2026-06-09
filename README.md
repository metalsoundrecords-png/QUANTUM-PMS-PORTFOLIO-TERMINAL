# Quantum PMS

Terminal financiero y sistema de gestión de portafolio cripto con backend FastAPI, ledger SQLite por eventos y frontend web basado en el diseño original de Claude Designs.

## Ejecutar local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abre `http://127.0.0.1:8000`.

## Qué incluye

- Backend FastAPI con `/api/snapshot`, `/ws/live`, `/health` y carga CSV.
- Base SQLite local en `data/quantum.db`.
- Motor contable inicial con VWAP preservado entre transferencias.
- Aislamiento de PnL de futuros frente al costo base Spot.
- Frontend desempaquetado en `static/`, listo para evolucionar.
- Configuración para GitHub y Railway (`Procfile`, `railway.json`).

## Pruebas

```bash
pytest
```
