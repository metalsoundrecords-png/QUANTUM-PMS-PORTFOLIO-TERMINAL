from __future__ import annotations

import logging
from datetime import datetime, timezone

import ccxt
from sqlalchemy.orm import Session

from ..database import EventType, LedgerEvent
from ..parsers import _extract_symbol

log = logging.getLogger(__name__)

# Transaction hash prefix distinto del CSV para evitar colisiones
# CSV:  bingx-{side}-{asset}-{i}-{ts}
# API:  bingxapi-{side}-{asset}-{trade_id}   ← trade_id del exchange es único globalmente


class BingXConnector:
    def __init__(self, api_key: str, api_secret: str) -> None:
        self._ex = ccxt.bingx({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "spot"},
        })

    # ── Verificación de permisos ───────────────────────────────────────────

    def check_permissions(self) -> dict:
        """
        Verifica conectividad y detecta permisos peligrosos.
        blocked=True si se detecta retiro habilitado — el sync queda bloqueado.
        """
        result: dict = {"connected": False, "blocked": False, "warnings": []}

        try:
            self._ex.fetch_balance()
            result["connected"] = True
        except (ccxt.AuthenticationError, ccxt.PermissionDenied) as exc:
            result["warnings"].append(f"Error de autenticación BingX: {exc}")
            return result
        except Exception as exc:
            result["warnings"].append(f"No se pudo conectar a BingX: {exc}")
            return result

        # Detectar permiso de trading: create_order lanza AuthenticationError/PermissionDenied
        # en keys solo-lectura, pero InvalidOrder/InsufficientFunds si trading SÍ está habilitado.
        try:
            self._ex.create_order("BTC/USDT", "limit", "buy", 0.000001, 0.01)
            result["warnings"].append(
                "ADVERTENCIA: La clave API tiene permisos de TRADING habilitados. "
                "Use una clave de SOLO LECTURA."
            )
        except (ccxt.AuthenticationError, ccxt.PermissionDenied, ccxt.NotSupported):
            pass  # Bien — sin permisos de trading
        except Exception:
            result["warnings"].append(
                "ADVERTENCIA: La clave API podría tener permisos de TRADING. "
                "Verifique la configuración en BingX."
            )

        # Detectar permiso de retiro — BLOQUEA el sync si está activo
        try:
            self._ex.fetch_withdrawals(limit=1)
            # Si no lanzó excepción, el permiso de retiro está habilitado
            result["blocked"] = True
            result["warnings"].append(
                "BLOQUEADO: La clave API tiene permisos de RETIRO habilitados. "
                "El sync está desactivado por seguridad. "
                "Genera una nueva clave con SOLO permisos de lectura."
            )
        except (ccxt.AuthenticationError, ccxt.PermissionDenied, ccxt.NotSupported):
            pass  # Bien — sin permisos de retiro
        except Exception:
            pass

        return result

    # ── Fetch de trades ────────────────────────────────────────────────────

    def fetch_spot_trades(self, since_ms: int | None = None) -> list[LedgerEvent]:
        """
        Descarga trades spot de BingX.
        Deriva los símbolos activos desde el balance para no recorrer cientos de mercados.
        """
        events: list[LedgerEvent] = []

        # Obtener activos con saldo para limitar el scope de búsqueda
        try:
            balance = self._ex.fetch_balance()
            active_assets = {
                asset for asset, info in balance.get("total", {}).items()
                if isinstance(info, (int, float)) and info > 0
            }
        except Exception as exc:
            log.warning("BingX: no se pudo obtener balance: %s", exc)
            active_assets = set()

        # Construir pares: ASSET/USDT y ASSET/USDC para activos con saldo actual
        # más pares históricamente comunes aunque el saldo sea 0
        quote_currencies = ["USDT", "USDC"]
        symbols_to_check: set[str] = set()
        for asset in active_assets:
            if asset not in quote_currencies:
                for quote in quote_currencies:
                    symbols_to_check.add(f"{asset}/{quote}")

        # Siempre incluir pares comunes para capturar historial aunque el saldo sea 0
        symbols_to_check.update({"BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
                                  "BTC/USDC", "ETH/USDC"})

        for symbol in sorted(symbols_to_check):
            try:
                trades = self._ex.fetch_my_trades(symbol, since=since_ms, limit=500)
                for trade in trades:
                    evt = self._trade_to_event(trade)
                    if evt:
                        events.append(evt)
            except (ccxt.BadSymbol, ccxt.NotSupported):
                continue
            except Exception as exc:
                log.debug("BingX: error al obtener trades de %s: %s", symbol, exc)

        log.info("BingX API: %d trades descargados", len(events))
        return events

    def _trade_to_event(self, trade: dict) -> LedgerEvent | None:
        try:
            side = (trade.get("side") or "").upper()
            if side not in ("BUY", "SELL"):
                return None

            asset, _ = _extract_symbol(trade.get("symbol", ""))

            ts_ms = trade.get("timestamp")
            ts = (
                datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                if ts_ms
                else datetime.now(timezone.utc)
            )

            qty = float(trade.get("amount") or 0)
            price = float(trade.get("price") or 0)

            # Fingerprint idéntico al hash del CSV para evitar duplicados entre ambas fuentes:
            # bingx-{side}-{asset}-{qty:.8g}-{price:.8g}-{ts_unix}
            tx_hash = f"bingx-{side.lower()}-{asset}-{qty:.8g}-{price:.8g}-{int(ts.timestamp())}"

            fee_info = trade.get("fee") or {}
            fee = float(fee_info.get("cost", 0.0)) if fee_info else 0.0

            return LedgerEvent(
                event_type=EventType.BUY.value if side == "BUY" else EventType.SELL.value,
                asset=asset,
                venue="BingX",
                quantity=qty,
                price=price,
                fee=fee,
                transaction_hash=tx_hash,
                timestamp=ts,
            )
        except Exception as exc:
            log.warning("BingX: error al convertir trade %s: %s", trade.get("id"), exc)
            return None

    # ── Sync principal ─────────────────────────────────────────────────────

    def sync(self, db: Session, since_ms: int | None = None) -> dict:
        """
        Sincroniza trades de BingX a la base de datos.
        Usa transaction_hash para evitar duplicados con importaciones CSV previas.
        """
        events = self.fetch_spot_trades(since_ms=since_ms)
        if not events:
            return {"fetched": 0, "new": 0, "duplicates_skipped": 0}

        existing = {h for (h,) in db.query(LedgerEvent.transaction_hash).all()}
        new_events = [e for e in events if e.transaction_hash not in existing]
        db.add_all(new_events)
        db.commit()

        log.info("BingX sync: %d nuevos, %d duplicados omitidos",
                 len(new_events), len(events) - len(new_events))
        return {
            "fetched": len(events),
            "new": len(new_events),
            "duplicates_skipped": len(events) - len(new_events),
        }
