import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
DATA_DIR = ROOT_DIR / "data"
DATABASE_URL = f"sqlite:///{DATA_DIR / 'quantum.db'}"
STATIC_DIR = ROOT_DIR / "static"

# BingX API — leer SOLO desde .env, nunca hardcodear
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "")

# Intervalo de sincronización automática (minutos). Default: 5.
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "5"))

# Arbitrum Multi-Wallet — JSON con mapeo nombre → dirección (o nombre → {address, category})
# Ejemplo: {"Hedge": {"address": "0xA13C...", "category": "bot"}, "Trezor": "0xD49B..."}
ARBITRUM_RPC_URL = os.getenv("ARBITRUM_RPC_URL", "")
_raw_wallets = os.getenv("ARBITRUM_WALLETS", "{}")
try:
    ARBITRUM_WALLETS: dict[str, str] = json.loads(_raw_wallets)
except json.JSONDecodeError:
    ARBITRUM_WALLETS = {}
