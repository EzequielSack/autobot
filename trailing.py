"""
╔══════════════════════════════════════════════════════════╗
║         EZBOT v2.0 — Trailing Stop Monitor               ║
║         Corre en paralelo al bot principal               ║
║         Monitorea posiciones y mueve el SL cada 30s     ║
╚══════════════════════════════════════════════════════════╝

Lógica de trailing:
  • Si precio avanzó ≥ 0.5×ATR desde la entrada → SL al breakeven
  • Si precio avanzó ≥ 1.0×ATR desde la entrada → SL a +0.5×ATR de ganancia

Correr con:
    python trailing.py
"""

import os
import time
import logging
from datetime import datetime
from pybit.unified_trading import HTTP
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# ─── CONFIGURACIÓN (idéntica al bot.py) ───────────────────
load_dotenv()

API_KEY    = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
TESTNET    = os.getenv("TESTNET", "true").lower() == "true"

SYMBOLS        = ["BTCUSDT", "ETHUSDT"]
TIMEFRAME      = "5"
MONITOR_SLEEP  = 30   # segundos entre cada chequeo

# ATR — idéntico al bot.py
ATR_PERIOD  = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 3.0

# Umbrales de trailing (en múltiplos de ATR desde la entrada)
BREAKEVEN_THRESHOLD = 0.5   # mover SL al breakeven cuando el precio gana 0.5×ATR
LOCK_THRESHOLD      = 1.0   # mover SL a +0.5×ATR cuando el precio gana 1.0×ATR
LOCK_DISTANCE       = 0.5   # distancia en ATR para el SL bloqueado en ganancia

# ─── LOGGING ──────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)

# Logger de trailing con su propio archivo
trailing_log_path = "logs/trailing.log"

trailing_logger = logging.getLogger("trailing")
trailing_logger.setLevel(logging.INFO)

import sys as _sys
fh = logging.FileHandler(trailing_log_path, encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

sh = logging.StreamHandler(stream=open(_sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False))
sh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

trailing_logger.addHandler(fh)
trailing_logger.addHandler(sh)

log = trailing_logger

# ─── CONEXIÓN BYBIT ───────────────────────────────────────
session = HTTP(
    testnet=TESTNET,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

# ─── ESTADO INTERNO ───────────────────────────────────────
# Rastrea qué nivel de trailing ya se aplicó a cada posición
# para no repetir el mismo movimiento.
# Estructura: { symbol: {"nivel": "NONE"|"BREAKEVEN"|"LOCKED", "sl_actual": float} }
estado_trailing: dict[str, dict] = {}


# ══════════════════════════════════════════════════════════
#   FUNCIONES DE DATOS (idénticas al bot.py)
# ══════════════════════════════════════════════════════════

def get_klines(symbol: str, interval: str, limit: int = 50) -> pd.DataFrame:
    """Obtiene velas históricas de Bybit (mismo código que bot.py)."""
    try:
        resp = session.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        data = resp["result"]["list"]
        df   = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        df = df.astype({
            "open": float, "high": float,
            "low": float, "close": float, "volume": float
        })
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
    except Exception as e:
        log.error(f"Error obteniendo klines {symbol} {interval}: {e}")
        return pd.DataFrame()


def get_open_position(symbol: str) -> dict:
    """Retorna la posición abierta en un símbolo, si existe."""
    try:
        resp      = session.get_positions(category="linear", symbol=symbol)
        positions = resp["result"]["list"]
        for pos in positions:
            if float(pos.get("size", 0)) > 0:
                return pos
        return {}
    except Exception as e:
        log.error(f"Error obteniendo posición {symbol}: {e}")
        return {}


def get_last_price(symbol: str) -> float:
    """Precio actual del mercado."""
    try:
        resp = session.get_tickers(category="linear", symbol=symbol)
        return float(resp["result"]["list"][0]["lastPrice"])
    except Exception as e:
        log.error(f"Error obteniendo precio {symbol}: {e}")
        return 0.0


# ── Sensor 3: ATR (idéntico al bot.py) ───────────────────
def calc_atr(df: pd.DataFrame) -> pd.DataFrame:
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"]  - df["close"].shift(1))
        )
    )
    df["atr"] = df["tr"].rolling(ATR_PERIOD).mean()
    return df


def get_atr_actual(symbol: str) -> float:
    """Calcula el ATR actual en base a las últimas 50 velas de 5min."""
    df = get_klines(symbol, TIMEFRAME, limit=50)
    if df.empty:
        return 0.0
    df  = calc_atr(df)
    atr = df["atr"].iloc[-1]
    return float(atr) if not pd.isna(atr) else 0.0


# ══════════════════════════════════════════════════════════
#   MOVER STOP LOSS EN BYBIT
# ══════════════════════════════════════════════════════════

def set_stop_loss(symbol: str, nuevo_sl: float, side: str) -> bool:
    """
    Actualiza el stop loss de una posición abierta en Bybit.
    Usa set_trading_stop que permite modificar SL/TP sin cerrar la posición.
    """
    try:
        resp = session.set_trading_stop(
            category="linear",
            symbol=symbol,
            stopLoss=str(round(nuevo_sl, 2)),
            slTriggerBy="MarkPrice",
            positionIdx=0,   # one-way mode
        )
        if resp.get("retCode") == 0:
            log.info(
                f"✅ SL actualizado | {symbol} ({side}) | "
                f"nuevo SL: ${nuevo_sl:,.2f}"
            )
            return True
        else:
            log.error(
                f"❌ Error actualizando SL {symbol}: "
                f"{resp.get('retMsg', 'Desconocido')}"
            )
            return False
    except Exception as e:
        log.error(f"❌ Excepción al actualizar SL {symbol}: {e}")
        return False


# ══════════════════════════════════════════════════════════
#   LÓGICA DE TRAILING STOP
# ══════════════════════════════════════════════════════════

def procesar_trailing(symbol: str):
    """
    Evalúa si corresponde mover el SL de la posición abierta en `symbol`.

    Reglas:
      1. Si precio avanzó ≥ 0.5×ATR → SL al breakeven (precio de entrada)
      2. Si precio avanzó ≥ 1.0×ATR → SL a (entrada + 0.5×ATR) para LONG
                                         o (entrada - 0.5×ATR) para SHORT
    El nivel de trailing solo avanza (nunca retrocede).
    """
    pos = get_open_position(symbol)
    if not pos:
        # Si no hay posición abierta, limpiamos el estado guardado
        if symbol in estado_trailing:
            estado_trailing.pop(symbol)
        return

    side        = pos.get("side", "")        # "Buy" o "Sell"
    entry_price = float(pos.get("avgPrice", 0))
    sl_actual   = float(pos.get("stopLoss", 0))
    size        = float(pos.get("size", 0))

    if entry_price == 0 or size == 0:
        return

    precio = get_last_price(symbol)
    if precio == 0:
        return

    atr = get_atr_actual(symbol)
    if atr == 0:
        log.warning(f"⚠️  ATR=0 para {symbol} — trailing omitido")
        return

    # Estado actual de este símbolo
    if symbol not in estado_trailing:
        estado_trailing[symbol] = {
            "nivel":     "NONE",     # NONE → BREAKEVEN → LOCKED
            "sl_actual": sl_actual,
        }

    estado = estado_trailing[symbol]

    # ── Distancia que avanzó el precio desde la entrada ──
    if side == "Buy":
        avance = precio - entry_price
    else:  # Sell / SHORT
        avance = entry_price - precio

    log.info(
        f"🔍 {symbol} ({side}) | "
        f"Entrada: ${entry_price:,.2f} | "
        f"Precio: ${precio:,.2f} | "
        f"Avance: ${avance:,.2f} | "
        f"ATR: ${atr:,.2f} | "
        f"SL actual: ${sl_actual:,.2f} | "
        f"Nivel trailing: {estado['nivel']}"
    )

    # ── Nivel 2: precio avanzó ≥ 1.0×ATR → SL a 0.5×ATR de ganancia ──
    if avance >= atr * LOCK_THRESHOLD and estado["nivel"] != "LOCKED":
        if side == "Buy":
            nuevo_sl = round(entry_price + atr * LOCK_DISTANCE, 2)
        else:
            nuevo_sl = round(entry_price - atr * LOCK_DISTANCE, 2)

        # Solo mover si mejora el SL actual
        debe_mover = (
            (side == "Buy"  and nuevo_sl > sl_actual) or
            (side == "Sell" and nuevo_sl < sl_actual)
        )
        if debe_mover:
            log.info(
                f"🔒 TRAILING LOCK | {symbol} | "
                f"Avance: ${avance:,.2f} ≥ 1.0×ATR (${atr:,.2f}) | "
                f"SL {sl_actual:,.2f} → {nuevo_sl:,.2f} (+0.5×ATR ganancia)"
            )
            if set_stop_loss(symbol, nuevo_sl, side):
                estado["nivel"]     = "LOCKED"
                estado["sl_actual"] = nuevo_sl

    # ── Nivel 1: precio avanzó ≥ 0.5×ATR → SL al breakeven ──
    elif avance >= atr * BREAKEVEN_THRESHOLD and estado["nivel"] == "NONE":
        nuevo_sl = round(entry_price, 2)

        debe_mover = (
            (side == "Buy"  and nuevo_sl > sl_actual) or
            (side == "Sell" and nuevo_sl < sl_actual)
        )
        if debe_mover:
            log.info(
                f"🟡 BREAKEVEN | {symbol} | "
                f"Avance: ${avance:,.2f} ≥ 0.5×ATR (${atr*0.5:,.2f}) | "
                f"SL {sl_actual:,.2f} → {nuevo_sl:,.2f} (entrada)"
            )
            if set_stop_loss(symbol, nuevo_sl, side):
                estado["nivel"]     = "BREAKEVEN"
                estado["sl_actual"] = nuevo_sl
        else:
            log.info(
                f"⏭️  Breakeven calculado (${nuevo_sl:,.2f}) no mejora SL actual "
                f"(${sl_actual:,.2f}) — sin cambios"
            )

    else:
        log.info(
            f"⏳ {symbol} | Avance ${avance:,.2f} | "
            f"Umbral breakeven: ${atr * BREAKEVEN_THRESHOLD:,.2f} | "
            f"Sin acción de trailing"
        )


# ══════════════════════════════════════════════════════════
#   LOOP PRINCIPAL
# ══════════════════════════════════════════════════════════

def run():
    log.info("═" * 56)
    log.info("  EZBOT v2.0 — Trailing Stop Monitor")
    log.info(f"  Modo     : {'TESTNET 🧪' if TESTNET else 'REAL 🔴'}")
    log.info(f"  Símbolos : {SYMBOLS}")
    log.info(f"  Refresh  : cada {MONITOR_SLEEP}s")
    log.info(f"  Log      : {trailing_log_path}")
    log.info(f"  Regla 1  : SL al breakeven si avance ≥ {BREAKEVEN_THRESHOLD}×ATR")
    log.info(f"  Regla 2  : SL a +{LOCK_DISTANCE}×ATR si avance ≥ {LOCK_THRESHOLD}×ATR")
    log.info("═" * 56)

    ciclo = 0
    while True:
        ciclo += 1
        log.info(f"\n{'─'*40}")
        log.info(f"Ciclo #{ciclo} | {datetime.now().strftime('%H:%M:%S')}")
        log.info(f"{'─'*40}")

        for symbol in SYMBOLS:
            try:
                procesar_trailing(symbol)
            except Exception as e:
                log.error(f"Error procesando trailing para {symbol}: {e}")

        log.info(f"\n⏱️  Próximo chequeo en {MONITOR_SLEEP}s...")
        time.sleep(MONITOR_SLEEP)


if __name__ == "__main__":
    run()
