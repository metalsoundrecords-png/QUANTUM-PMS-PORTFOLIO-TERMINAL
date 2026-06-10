from __future__ import annotations

import json

from .config import (
    ARBITRUM_RPC_URL,
    ARBITRUM_WALLETS,
    BINGX_API_KEY,
    BINGX_API_SECRET,
    DATA_DIR,
)

SETTINGS_FILE = DATA_DIR / "settings.json"


def _read_overrides() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def get_config() -> dict:
    """Configuración efectiva: overrides de settings.json sobre defaults de .env."""
    overrides = _read_overrides()
    return {
        "bingx_api_key": overrides.get("bingx_api_key") or BINGX_API_KEY,
        "bingx_api_secret": overrides.get("bingx_api_secret") or BINGX_API_SECRET,
        "arbitrum_rpc_url": overrides.get("arbitrum_rpc_url") or ARBITRUM_RPC_URL,
        "arbitrum_wallets": overrides.get("arbitrum_wallets") or ARBITRUM_WALLETS,
    }


def update_config(updates: dict) -> dict:
    """Persiste cambios parciales en settings.json. Solo sobrescribe campos no vacíos."""
    overrides = _read_overrides()
    for key in ("bingx_api_key", "bingx_api_secret", "arbitrum_rpc_url"):
        value = updates.get(key)
        if value:
            overrides[key] = value
    wallets = updates.get("arbitrum_wallets")
    if wallets:
        overrides["arbitrum_wallets"] = wallets
    SETTINGS_FILE.write_text(json.dumps(overrides, indent=2))
    return get_config()


def obfuscate_secret(value: str) -> str:
    """Enmascara un secreto dejando visibles solo los últimos 4 caracteres."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "****..." + value[-4:]


def obfuscate_url(url: str) -> str:
    """Enmascara la porción final (clave API) de una URL, conservando el host."""
    if not url:
        return ""
    base, sep, tail = url.rpartition("/")
    if not sep:
        return obfuscate_secret(url)
    if len(tail) <= 4:
        return base + "/****"
    return base + "/****..." + tail[-4:]
