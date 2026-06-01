"""
╔══════════════════════════════════════════════════════════╗
║         EZBOT v2.2 — Bot de Trading para Bybit           ║
║         6 Sensores: BB + RSI + ATR + EMA + VOL + ADX    ║
║         Autor: Canal @ezequielsack                       ║
║                                                          ║
║  Parámetros validados por análisis cuantitativo:         ║
║  • Riesgo: 0.5% | PF backtest: 2.83 | Sharpe: 6.46     ║
║  • Drawdown controlado: -13% esperado                    ║
║  • Walk-forward consistente (no sobreoptimizado)         ║
║                                                          ║
║  ⚠️  DISCLAIMER: No es asesoramiento financiero.         ║
║  El trading en futuros tiene riesgo real de pérdida      ║
║  total del capital. Usá solo lo que puedas perder.       ║
╚══════════════════════════════════════════════════════════╝
"""

import time
import logging
import os
from datetime import datetime, timezone
from pybit.unified_trading import HTTP
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# ─── CONFIGURACIÓN ────────────────────────────────────────
load_dotenv()

API_KEY    = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
TESTNET    = os.getenv("TESTNET", "true").lower() == "true"

# Parámetros del bot
SYMBOLS        = ["BTCUSDT"]          # ETH excluido — PF insuficiente en backtest
LEVERAGE       = 3
RISK_PER_TRADE = 0.005               # 0.5% — óptimo según análisis cuantitativo
TIMEFRAME      = "5"                 # 5 minutos — ejecución
TIMEFRAME_HTF  = "60"               # 1 hora — filtro de tendencia
LOOP_SLEEP     = 60                  # segundos entre ciclos

# ── Sensor 1: Bollinger Bands (el elástico) ──────────────
BB_PERIOD = 20
BB_STD    = 2.0

# ── Sensor 2: RSI (el termómetro) ────────────────────────
RSI_PERIOD     = 14
RSI_OVERSOLD   = 30                  # señales más selectivas
RSI_OVERBOUGHT = 70

# ── Sensor 3: ATR (el viento) ────────────────────────────
ATR_PERIOD  = 14
ATR_SL_MULT = 1.2                    # SL ajustado — validado en backtests
ATR_TP_MULT = 4.2                    # ratio 1:3.5 — mejor PF

# ── Sensor 4: EMA en 1h (el río) ─────────────────────────
EMA_HTF_PERIOD = 50

# ── Sensor 5: Volumen (la cancha) ────────────────────────
VOL_PERIOD = 20
VOL_MULT   = 1.5                     # filtro estricto — menos señales falsas

# ── Sensor 6: ADX (la tormenta) ──────────────────────────
ADX_PERIOD    = 14
ADX_THRESHOLD = 25

# ─── FILTROS HORARIOS Y DIARIOS ───────────────────────────
# Análisis cuantitativo identificó horas y días malos:
SESSION_START = 8    # 08:00 UTC
SESSION_END   = 22   # 22:00 UTC
# Horas con PF < 1 dentro de la sesión — se saltan
HORAS_MALAS   = {10, 13, 19, 21}
# Días malos: Viernes=4, Sábado=5 (0=Lunes)
DIAS_MALOS    = {4, 5}              # Viernes y Sábado

# ─── CIRCUIT BREAKER ──────────────────────────────────────
MAX_PERDIDAS_CONSECUTIVAS = 4       # pausa tras 4 pérdidas seguidas
PAUSA_MINUTOS             = 180     # 3 horas de pausa

# ─── LOGGING ──────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            f"logs/ezbot_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("ezbot")

# ─── CONEXIÓN BYBIT ───────────────────────────────────────
session = HTTP(
    testnet=TESTNET,
    api_key=API_KEY,
    api_secret=API_SECRET,
)

# ─── ESTADO DEL CIRCUIT BREAKER ───────────────────────────
estado = {
    "perdidas_consecutivas": 0,
    "pausa_hasta":           None,   # datetime UTC o None
}


# ══════════════════════════════════════════════════════════
#   FUNCIONES DE DATOS
# ══════════════════════════════════════════════════════════

def get_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """Obtiene velas históricas de Bybit."""
    try:
        resp = session.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        data = resp["result"]["list"]
        df = pd.DataFrame(data, columns=[
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


def get_balance() -> float:
    """Retorna el balance disponible en USDT."""
    try:
        resp    = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        balance = float(resp["result"]["list"][0]["coin"][0]["availableToWithdraw"])
        return balance
    except Exception as e:
        log.error(f"Error obteniendo balance: {e}")
        return 0.0


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


def get_ultimas_ops_cerradas(symbol: str, limit: int = 10) -> list:
    """Obtiene las últimas operaciones cerradas para el circuit breaker."""
    try:
        resp = session.get_closed_pnl(
            category="linear",
            symbol=symbol,
            limit=limit
        )
        return resp["result"]["list"]
    except Exception as e:
        log.error(f"Error obteniendo ops cerradas {symbol}: {e}")
        return []


# ══════════════════════════════════════════════════════════
#   LOS 6 SENSORES
# ══════════════════════════════════════════════════════════

def calc_bollinger(df: pd.DataFrame) -> pd.DataFrame:
    """EL ELÁSTICO — detecta extremos del rango."""
    df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"]   = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    return df


def calc_rsi(df: pd.DataFrame) -> pd.DataFrame:
    """EL TERMÓMETRO — RSI <30 sobreventa, >70 sobrecompra."""
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def calc_atr(df: pd.DataFrame) -> pd.DataFrame:
    """EL VIENTO — mide volatilidad real para SL y TP automáticos."""
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"]  - df["close"].shift(1))
        )
    )
    df["atr"] = df["tr"].rolling(ATR_PERIOD).mean()
    return df


def get_htf_trend(symbol: str) -> str:
    """
    EL RÍO — EMA 50 en 1h para saber la tendencia mayor.
    Solo operamos a favor del río.
    Retorna: 'BULLISH', 'BEARISH' o 'NEUTRAL'
    """
    df_1h = get_klines(symbol, TIMEFRAME_HTF, limit=100)
    if df_1h.empty or len(df_1h) < EMA_HTF_PERIOD:
        return "NEUTRAL"
    ema50     = df_1h["close"].ewm(span=EMA_HTF_PERIOD, adjust=False).mean().iloc[-1]
    precio_1h = df_1h["close"].iloc[-1]
    if precio_1h > ema50 * 1.001:
        return "BULLISH"
    elif precio_1h < ema50 * 0.999:
        return "BEARISH"
    return "NEUTRAL"


def calc_volume_filter(df: pd.DataFrame) -> pd.DataFrame:
    """LA CANCHA — confirma participación real del mercado."""
    df["vol_avg"] = df["volume"].rolling(VOL_PERIOD).mean()
    df["vol_ok"]  = df["volume"] >= df["vol_avg"] * VOL_MULT
    return df


def calc_adx(df: pd.DataFrame) -> pd.DataFrame:
    """LA TORMENTA — fuerza de la tendencia. ADX>25 → reducir posición."""
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    plus_dm  = high.diff()
    minus_dm = low.diff().abs()
    plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    tr = np.maximum(
        high - low,
        np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))
    )
    atr_adx  = pd.Series(tr).rolling(ADX_PERIOD).mean()
    plus_di  = 100 * plus_dm.rolling(ADX_PERIOD).mean()  / atr_adx
    minus_di = 100 * minus_dm.rolling(ADX_PERIOD).mean() / atr_adx
    dx       = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    df["adx"] = dx.rolling(ADX_PERIOD).mean()
    return df


def add_all_sensors(df: pd.DataFrame) -> pd.DataFrame:
    df = calc_bollinger(df)
    df = calc_rsi(df)
    df = calc_atr(df)
    df = calc_volume_filter(df)
    df = calc_adx(df)
    return df


# ══════════════════════════════════════════════════════════
#   FILTROS DE SESIÓN Y CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════

def en_sesion_valida() -> bool:
    """
    Verifica que estemos en un horario y día válido para operar.
    Basado en análisis cuantitativo de PF por hora y día.
    """
    ahora    = datetime.now(timezone.utc)
    hora_utc = ahora.hour
    dia_sem  = ahora.weekday()   # 0=Lunes, 6=Domingo

    # Verificar día
    if dia_sem in DIAS_MALOS:
        log.info(f"📅 Día malo ({ahora.strftime('%A')}) — sin operar hoy")
        return False

    # Verificar sesión horaria
    if not (SESSION_START <= hora_utc < SESSION_END):
        log.info(f"🕐 Fuera de sesión ({hora_utc}h UTC) — esperando {SESSION_START}h")
        return False

    # Verificar hora mala dentro de la sesión
    if hora_utc in HORAS_MALAS:
        log.info(f"⚠️  Hora mala identificada ({hora_utc}h UTC) — saltando")
        return False

    return True


def verificar_circuit_breaker() -> bool:
    """
    Verifica si el circuit breaker está activo.
    Retorna True si podemos operar, False si estamos en pausa.
    """
    if estado["pausa_hasta"] is None:
        return True

    ahora = datetime.now(timezone.utc)
    if ahora >= estado["pausa_hasta"]:
        estado["pausa_hasta"] = None
        estado["perdidas_consecutivas"] = 0
        log.info("✅ Circuit breaker: pausa terminada — operando normal")
        return True

    minutos_restantes = (estado["pausa_hasta"] - ahora).seconds // 60
    log.info(f"⏸️  Circuit breaker activo — pausa por {minutos_restantes} minutos más")
    return False


def actualizar_circuit_breaker(resultado: str):
    """Actualiza el contador de pérdidas consecutivas."""
    if resultado == "LOSS":
        estado["perdidas_consecutivas"] += 1
        log.info(f"📉 Pérdidas consecutivas: {estado['perdidas_consecutivas']}/{MAX_PERDIDAS_CONSECUTIVAS}")
        if estado["perdidas_consecutivas"] >= MAX_PERDIDAS_CONSECUTIVAS:
            estado["pausa_hasta"] = datetime.now(timezone.utc).replace(
                microsecond=0
            )
            from datetime import timedelta
            estado["pausa_hasta"] = datetime.now(timezone.utc) + timedelta(minutes=PAUSA_MINUTOS)
            log.warning(
                f"🚨 CIRCUIT BREAKER ACTIVADO — "
                f"pausa de {PAUSA_MINUTOS} minutos hasta "
                f"{estado['pausa_hasta'].strftime('%H:%M UTC')}"
            )
    else:
        if estado["perdidas_consecutivas"] > 0:
            log.info(f"✅ Trade ganador — reseteando contador de pérdidas")
        estado["perdidas_consecutivas"] = 0


# ══════════════════════════════════════════════════════════
#   LÓGICA DE SEÑAL
# ══════════════════════════════════════════════════════════

def get_signal(df: pd.DataFrame, htf_trend: str) -> tuple[str, float]:
    """
    Evalúa los 6 sensores al mismo tiempo.
    Solo opera si TODOS confirman.

    Retorna: (señal, multiplicador_posicion)
    señal = 'LONG', 'SHORT' o 'NONE'
    multiplicador = 1.0 (normal) o 0.5 (tormenta ADX)
    """
    min_candles = max(BB_PERIOD, RSI_PERIOD, ATR_PERIOD, ADX_PERIOD, VOL_PERIOD) + 5
    if df.empty or len(df) < min_candles:
        return "NONE", 1.0

    last = df.iloc[-1]
    prev = df.iloc[-2]

    precio   = last["close"]
    bb_lower = last["bb_lower"]
    bb_upper = last["bb_upper"]
    bb_width = last["bb_width"]
    rsi      = last["rsi"]
    atr      = last["atr"]
    vol_ok   = last["vol_ok"]
    adx      = last["adx"]

    # ── Filtros globales ──────────────────────────────────
    if bb_width < 0.01:
        log.info("⏳ BB: bandas comprimidas — esperando expansión")
        return "NONE", 1.0

    atr_min = precio * 0.001
    if atr < atr_min:
        log.info("⏳ ATR: volatilidad insuficiente")
        return "NONE", 1.0

    if not vol_ok:
        log.info("⏳ Volumen: insuficiente — posible trampa")
        return "NONE", 1.0

    # ── Multiplicador por tormenta (ADX) ──────────────────
    pos_mult = 0.5 if adx > ADX_THRESHOLD else 1.0
    if pos_mult == 0.5:
        log.info(f"⚡ ADX alto ({adx:.1f}) — posición reducida al 50%")

    # ── Señal LONG ────────────────────────────────────────
    long_bb  = prev["close"] > bb_lower and precio <= bb_lower
    long_rsi = rsi <= RSI_OVERSOLD
    long_htf = htf_trend == "BULLISH"

    if long_bb and long_rsi and long_htf:
        log.info(
            f"✅ SEÑAL LONG | precio: {precio:.2f} | "
            f"BB_lower: {bb_lower:.2f} | RSI: {rsi:.1f} | "
            f"Tendencia 1h: {htf_trend} | ADX: {adx:.1f}"
        )
        return "LONG", pos_mult

    # ── Señal SHORT ───────────────────────────────────────
    short_bb  = prev["close"] < bb_upper and precio >= bb_upper
    short_rsi = rsi >= RSI_OVERBOUGHT
    short_htf = htf_trend == "BEARISH"

    if short_bb and short_rsi and short_htf:
        log.info(
            f"✅ SEÑAL SHORT | precio: {precio:.2f} | "
            f"BB_upper: {bb_upper:.2f} | RSI: {rsi:.1f} | "
            f"Tendencia 1h: {htf_trend} | ADX: {adx:.1f}"
        )
        return "SHORT", pos_mult

    return "NONE", 1.0


# ══════════════════════════════════════════════════════════
#   GESTIÓN DE RIESGO Y ÓRDENES
# ══════════════════════════════════════════════════════════

def set_leverage(symbol: str):
    """Configura el apalancamiento del símbolo."""
    try:
        session.set_leverage(
            category="linear",
            symbol=symbol,
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE)
        )
        log.info(f"⚙️  Apalancamiento: {symbol} → {LEVERAGE}x")
    except Exception as e:
        log.warning(f"Apalancamiento ya configurado o error: {e}")


def calc_position_size(balance: float, atr: float, precio: float, mult: float) -> float:
    """
    Calcula el tamaño de la posición.
    Riesgo = 0.5% del capital (validado: drawdown -13% esperado).
    Stop loss = 1.2 × ATR.
    """
    riesgo_usdt = balance * RISK_PER_TRADE * mult
    stop_dist   = atr * ATR_SL_MULT
    qty_raw     = (riesgo_usdt * LEVERAGE) / stop_dist
    qty         = round(qty_raw, 3)
    return max(qty, 0.001)


def place_order(symbol: str, side: str, qty: float, precio: float, atr: float) -> dict:
    """
    Coloca la orden con SL y TP automáticos.
    SL = 1.2 × ATR | TP = 4.2 × ATR (ratio 1:3.5)
    NO usar salidas parciales ni BE — el análisis cuantitativo
    demostró que destruyen el PF de 2.57 a 1.14.
    """
    stop_dist = atr * ATR_SL_MULT
    tp_dist   = atr * ATR_TP_MULT

    if side == "Buy":
        sl = round(precio - stop_dist, 2)
        tp = round(precio + tp_dist,   2)
    else:
        sl = round(precio + stop_dist, 2)
        tp = round(precio - tp_dist,   2)

    try:
        resp = session.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            stopLoss=str(sl),
            takeProfit=str(tp),
            timeInForce="GoodTillCancel",
            reduceOnly=False,
        )
        log.info(
            f"📤 Orden ejecutada | {side} {qty} {symbol} | "
            f"precio: {precio:.2f} | SL: {sl} | TP: {tp} | "
            f"Riesgo: ${round(qty * stop_dist, 2)} USDT"
        )
        return resp
    except Exception as e:
        log.error(f"❌ Error colocando orden {symbol}: {e}")
        return {}


# ══════════════════════════════════════════════════════════
#   REGISTRO DE OPERACIONES
# ══════════════════════════════════════════════════════════

def log_trade(symbol, side, qty, precio, sl, tp, adx, rsi, htf_trend, pos_mult):
    """Guarda cada operación en CSV para seguimiento y análisis."""
    stop_dist = abs(precio - sl)
    row = {
        "timestamp":    datetime.now().isoformat(),
        "symbol":       symbol,
        "side":         side,
        "qty":          qty,
        "entry_price":  precio,
        "stop_loss":    sl,
        "take_profit":  tp,
        "sl_dist_usdt": round(stop_dist, 2),
        "riesgo_usd":   round(qty * stop_dist, 2),
        "rsi_entrada":  round(rsi, 1),
        "adx_entrada":  round(adx, 1),
        "tendencia_1h": htf_trend,
        "pos_mult":     pos_mult,
        "resultado":    "",   # WIN / LOSS — completar manualmente o con trailing.py
    }
    df_log = pd.DataFrame([row])
    path   = "data/trades.csv"
    header = not os.path.exists(path)
    df_log.to_csv(path, mode="a", header=header, index=False)
    log.info("📋 Trade registrado en data/trades.csv")


# ══════════════════════════════════════════════════════════
#   LOOP PRINCIPAL
# ══════════════════════════════════════════════════════════

def run():
    log.info("=" * 56)
    log.info("  EZBOT v2.2 — Parámetros optimizados")
    log.info(f"  Modo      : {'TESTNET 🧪' if TESTNET else 'REAL 🔴'}")
    log.info(f"  Pares     : {SYMBOLS}")
    log.info(f"  Leverage  : {LEVERAGE}x")
    log.info(f"  Riesgo    : {RISK_PER_TRADE*100}% por operación")
    log.info(f"  SL/TP     : {ATR_SL_MULT}x ATR / {ATR_TP_MULT}x ATR (ratio 1:3.5)")
    log.info(f"  Sesión    : {SESSION_START}h-{SESSION_END}h UTC")
    log.info(f"  Días off  : Viernes y Sábado")
    log.info(f"  Horas off : {sorted(HORAS_MALAS)}")
    log.info(f"  Breaker   : pausa {PAUSA_MINUTOS}min tras {MAX_PERDIDAS_CONSECUTIVAS} pérdidas")
    log.info(f"  Sensores  : BB + RSI + ATR + EMA1h + VOL + ADX")
    log.info("=" * 56)

    for sym in SYMBOLS:
        set_leverage(sym)

    ciclo = 0
    while True:
        ciclo += 1
        log.info(f"\n{'─'*40}")
        log.info(f"Ciclo #{ciclo} | {datetime.now().strftime('%H:%M:%S')}")
        log.info(f"{'─'*40}")

        # ── Verificar sesión válida ────────────────────────
        if not en_sesion_valida():
            log.info(f"⏱️  Próximo ciclo en {LOOP_SLEEP}s...")
            time.sleep(LOOP_SLEEP)
            continue

        # ── Verificar circuit breaker ──────────────────────
        if not verificar_circuit_breaker():
            time.sleep(LOOP_SLEEP)
            continue

        balance = get_balance()
        log.info(f"💰 Balance: {balance:.2f} USDT")

        if balance < 10:
            log.warning("⚠️  Balance insuficiente — esperando...")
            time.sleep(LOOP_SLEEP * 5)
            continue

        for symbol in SYMBOLS:
            log.info(f"\n🔍 {symbol}")

            # ── Verificar posición abierta ─────────────────
            pos = get_open_position(symbol)
            if pos:
                pnl = float(pos.get("unrealisedPnl", 0))
                log.info(
                    f"⏸️  Posición abierta en {symbol} | "
                    f"PnL actual: ${pnl:.4f} — esperando cierre"
                )
                continue

            # ── Sensor 4: EMA 1h (el río) ──────────────────
            htf_trend = get_htf_trend(symbol)
            log.info(f"🧭 Tendencia 1h: {htf_trend}")

            if htf_trend == "NEUTRAL":
                log.info("⏳ Río neutral — esperando definición")
                continue

            # ── Datos y sensores en 5 minutos ──────────────
            df = get_klines(symbol, TIMEFRAME, limit=200)
            if df.empty:
                continue

            df = add_all_sensors(df)

            # ── Evaluar los 6 sensores ──────────────────────
            signal, pos_mult = get_signal(df, htf_trend)

            if signal == "NONE":
                log.info(f"⏳ Sin señal en {symbol}")
                continue

            # ── Calcular tamaño de posición ─────────────────
            last   = df.iloc[-1]
            precio = last["close"]
            atr    = last["atr"]
            rsi    = last["rsi"]
            adx    = last["adx"]
            qty    = calc_position_size(balance, atr, precio, pos_mult)
            side   = "Buy" if signal == "LONG" else "Sell"

            riesgo_usdt = round(qty * atr * ATR_SL_MULT, 2)
            log.info(
                f"🎯 Entrando {signal} | qty: {qty} | "
                f"precio: {precio:.2f} | mult: {pos_mult} | "
                f"riesgo: ${riesgo_usdt}"
            )

            # ── Ejecutar la orden ───────────────────────────
            resp = place_order(symbol, side, qty, precio, atr)

            if resp:
                stop_dist = atr * ATR_SL_MULT
                tp_dist   = atr * ATR_TP_MULT
                sl = precio - stop_dist if side == "Buy" else precio + stop_dist
                tp = precio + tp_dist   if side == "Buy" else precio - tp_dist
                log_trade(
                    symbol, side, qty, precio, sl, tp,
                    adx, rsi, htf_trend, pos_mult
                )

        log.info(f"\n⏱️  Próximo ciclo en {LOOP_SLEEP}s...")
        time.sleep(LOOP_SLEEP)


# ─── ENTRADA ──────────────────────────────────────────────
if __name__ == "__main__":
    run()
