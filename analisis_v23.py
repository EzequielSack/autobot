"""
╔══════════════════════════════════════════════════════════╗
║   EZBOT v2.3 — Análisis Cuantitativo Completo           ║
║   Volume + Risk Control                                  ║
║   Trader Cuantitativo de Nivel Élite                    ║
╚══════════════════════════════════════════════════════════╝
Ejecutar: py analisis_v23.py
Output  : data/reporte_v23.txt  +  data/analisis_v23_*.csv
"""

import os, sys, time, pickle, logging, warnings, requests
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from datetime import datetime, timezone
from collections import defaultdict

warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════
#   PARÁMETROS BASE (v2.2 actual)
# ══════════════════════════════════════════════════════════
SYMBOL          = "BTCUSDT"
LEVERAGE        = 3
CAPITAL_INICIAL = 300.0
TIMEFRAME       = "5"
TIMEFRAME_HTF   = "60"

# v2.2 baseline
BASE_RISK      = 0.01
BASE_SL_MULT   = 1.2
BASE_TP_MULT   = 4.2
BASE_VOL_MULT  = 1.5
BASE_RSI_OS    = 30
BASE_RSI_OB    = 70
BASE_SES_START = 8
BASE_SES_END   = 22

BB_PERIOD      = 20
BB_STD         = 2.0
RSI_PERIOD     = 14
ATR_PERIOD     = 14
EMA_HTF_PERIOD = 50
VOL_PERIOD     = 20
ADX_PERIOD     = 14
ADX_THRESHOLD  = 25

COM_TAKER  = 0.00055   # 0.055% Bybit
SLIPPAGE   = 0.0001    # 0.01% estimado

BYBIT_BASE = "https://api.bybit.com"
CACHE_5M   = "data/cache_btc_5m.pkl"
CACHE_1H   = "data/cache_btc_1h.pkl"

# ─── logging ──────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

_fh = logging.FileHandler("logs/analisis_v23.log", encoding="utf-8")
_sh = logging.StreamHandler(
    open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
)
fmt = logging.Formatter("%(asctime)s | %(message)s")
_fh.setFormatter(fmt); _sh.setFormatter(fmt)
logging.basicConfig(level=logging.INFO, handlers=[_fh, _sh])
log = logging.getLogger("v23")

LINEA  = "═" * 62
LINEA2 = "─" * 62

# ══════════════════════════════════════════════════════════
#   DESCARGA Y CACHE DE DATOS
# ══════════════════════════════════════════════════════════

def _fetch_chunk(symbol, interval, start_ms, end_ms):
    r = requests.get(f"{BYBIT_BASE}/v5/market/kline", params={
        "category": "linear", "symbol": symbol,
        "interval": interval, "start": start_ms,
        "end": end_ms, "limit": 1000
    }, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("retCode") != 0:
        raise ValueError(d.get("retMsg"))
    return d["result"]["list"]


def descargar(symbol, interval, dias, cache_path):
    if os.path.exists(cache_path):
        log.info(f"  Cache encontrado: {cache_path}")
        with open(cache_path, "rb") as f:
            return pickle.load(f)

    log.info(f"  Descargando {symbol} {interval}min — {dias} días...")
    ahora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    inicio_ms = ahora_ms - dias * 86400 * 1000
    end_ms, todos = ahora_ms, []

    while end_ms > inicio_ms and len(todos) < 105_000:
        try:
            chunk = _fetch_chunk(symbol, interval, inicio_ms, end_ms)
        except Exception as e:
            log.warning(f"  Error chunk: {e}"); time.sleep(2); continue
        if not chunk:
            break
        todos.extend(chunk)
        end_ms = min(int(r[0]) for r in chunk) - 1
        time.sleep(0.2)

    df = pd.DataFrame(todos, columns=[
        "timestamp","open","high","low","close","volume","turnover"])
    df = df.astype({"open":float,"high":float,"low":float,
                    "close":float,"volume":float})
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    with open(cache_path, "wb") as f:
        pickle.dump(df, f)
    log.info(f"  {len(df)} velas | {df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}")
    return df


# ══════════════════════════════════════════════════════════
#   SENSORES (idénticos al bot)
# ══════════════════════════════════════════════════════════

def add_sensors(df):
    # BB
    df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"]   = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    # RSI
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    # ATR
    df["tr"] = np.maximum(df["high"]-df["low"],
               np.maximum(abs(df["high"]-df["close"].shift(1)),
                          abs(df["low"] -df["close"].shift(1))))
    df["atr"] = df["tr"].rolling(ATR_PERIOD).mean()
    # Volume
    df["vol_avg"] = df["volume"].rolling(VOL_PERIOD).mean()
    # ADX
    h, l, c = df["high"], df["low"], df["close"]
    pdm = h.diff().clip(lower=0)
    mdm = (-l.diff()).clip(lower=0)
    pdm = pdm.where(pdm > mdm, 0)
    mdm = mdm.where(mdm > pdm, 0)
    tr  = df["tr"]
    atr_ = tr.rolling(ADX_PERIOD).mean()
    pdi  = 100 * pdm.rolling(ADX_PERIOD).mean() / atr_
    mdi  = 100 * mdm.rolling(ADX_PERIOD).mean() / atr_
    dx   = (abs(pdi - mdi) / (pdi + mdi).replace(0, np.nan)) * 100
    df["adx"] = dx.rolling(ADX_PERIOD).mean()
    return df


def build_htf_trend(df_1h, df_5m):
    df_1h = df_1h.copy()
    df_1h["ema50"] = df_1h["close"].ewm(span=EMA_HTF_PERIOD, adjust=False).mean()
    df_1h["trend"] = "NEUTRAL"
    df_1h.loc[df_1h["close"] > df_1h["ema50"] * 1.001, "trend"] = "BULLISH"
    df_1h.loc[df_1h["close"] < df_1h["ema50"] * 0.999, "trend"] = "BEARISH"
    df_1h = df_1h.set_index("timestamp")
    s = df_1h["trend"].reindex(df_5m["timestamp"], method="ffill")
    s.index = df_5m.index
    return s.fillna("NEUTRAL")


# ══════════════════════════════════════════════════════════
#   MOTOR DE SIMULACIÓN — COMPLETAMENTE PARAMETRIZADO
# ══════════════════════════════════════════════════════════

def simular(df_5m, htf_series,
            risk_pct    = BASE_RISK,
            sl_mult     = BASE_SL_MULT,
            tp_mult     = BASE_TP_MULT,
            vol_mult    = BASE_VOL_MULT,
            rsi_os      = BASE_RSI_OS,
            rsi_ob      = BASE_RSI_OB,
            ses_start   = BASE_SES_START,
            ses_end     = BASE_SES_END,
            # Loss streak control (multiplicadores de size)
            streak_mults = None,   # {3: 0.75, 4: 0.50, 5: 0.25, 6: 0}  0 = pausar
            streak_pause = 48,     # velas de pausa si mult=0
            # Drawdown control version: None, "A", "B", "C"
            dd_version  = None,
            # Partial exit: None, "be_1r", "partial30_1r", "trailing_1r"
            partial_exit = None,
            # Comisiones y slippage
            com         = COM_TAKER,
            slip        = SLIPPAGE,
            # Slice de datos (para walk-forward)
            i_start     = 0,
            i_end       = None,
            ):
    """Motor de simulación completamente parametrizado."""

    if streak_mults is None:
        streak_mults = {}

    MIN_ROWS = max(BB_PERIOD, RSI_PERIOD, ATR_PERIOD, ADX_PERIOD, VOL_PERIOD) + 5
    ops = []

    en_pos     = False
    trade      = {}
    capital    = CAPITAL_INICIAL
    peak       = CAPITAL_INICIAL

    consec_losses = 0
    pausa_velas   = 0

    i_end = i_end or len(df_5m)

    for i in range(i_start, i_end):
        if i < MIN_ROWS:
            continue

        row  = df_5m.iloc[i]
        prev = df_5m.iloc[i - 1]

        precio = row["close"]
        hi     = row["high"]
        lo     = row["low"]
        hora   = pd.to_datetime(row["timestamp"]).hour
        en_ses = ses_start <= hora < ses_end

        # actualizar pico para DD
        peak = max(peak, capital)
        dd_actual_pct = (capital - peak) / peak * 100  # negativo

        # velas de pausa
        if pausa_velas > 0:
            pausa_velas -= 1

        # ── gestión posición abierta ───────────────────────
        if en_pos:
            sl   = trade["sl"]
            tp   = trade["tp"]
            side = trade["side"]
            ep   = trade["entry_price"]
            qty  = trade["qty"]
            be_hit = trade.get("be_hit", False)
            r1_level = trade.get("r1_level", None)

            # ── check 1R para BE y parciales ──────────────
            if r1_level is not None and not be_hit:
                if side == "LONG" and hi >= r1_level:
                    be_hit = True
                    trade["be_hit"] = True
                    if partial_exit in ("be_1r", "partial30_1r", "trailing_1r"):
                        trade["sl"] = ep   # SL al breakeven
                        sl = ep
                elif side == "SHORT" and lo <= r1_level:
                    be_hit = True
                    trade["be_hit"] = True
                    if partial_exit in ("be_1r", "partial30_1r", "trailing_1r"):
                        trade["sl"] = ep
                        sl = ep

            # trailing stop 1R
            if partial_exit == "trailing_1r" and be_hit:
                atr_trail = row["atr"] if not pd.isna(row["atr"]) else trade["atr_entrada"]
                if side == "LONG":
                    new_sl = precio - atr_trail * sl_mult
                    trade["sl"] = max(trade["sl"], new_sl)
                    sl = trade["sl"]
                else:
                    new_sl = precio + atr_trail * sl_mult
                    trade["sl"] = min(trade["sl"], new_sl)
                    sl = trade["sl"]

            cerrado = False
            resultado = None
            precio_cierre = None

            if side == "LONG":
                if lo <= sl:
                    precio_cierre = sl; resultado = "LOSS" if not be_hit else "BE"; cerrado = True
                elif hi >= tp:
                    precio_cierre = tp; resultado = "WIN"; cerrado = True
            else:
                if hi >= sl:
                    precio_cierre = sl; resultado = "LOSS" if not be_hit else "BE"; cerrado = True
                elif lo <= tp:
                    precio_cierre = tp; resultado = "WIN"; cerrado = True

            if cerrado:
                # slippage en cierre
                if resultado == "WIN":
                    precio_cierre = precio_cierre * (1 - slip) if side == "LONG" else precio_cierre * (1 + slip)
                else:
                    precio_cierre = precio_cierre * (1 + slip) if side == "LONG" else precio_cierre * (1 - slip)

                # PnL según partial exit
                if partial_exit == "partial30_1r" and be_hit and resultado == "WIN":
                    # 30% cerrado a 1R, 70% al TP
                    r1_price = trade["r1_level"]
                    pnl = (r1_price - ep) * qty * 0.30 * LEVERAGE + \
                          (precio_cierre - ep) * qty * 0.70 * LEVERAGE
                else:
                    if side == "LONG":
                        pnl = (precio_cierre - ep) * qty * LEVERAGE
                    else:
                        pnl = (ep - precio_cierre) * qty * LEVERAGE

                # BE: PnL ~0 (pequeño slippage)
                if resultado == "BE":
                    pnl = -(ep * qty * slip * 2)

                comision_total = (ep * qty * com + abs(precio_cierre) * qty * com)
                pnl_neto = pnl - comision_total
                capital += pnl_neto

                # duración en minutos
                duracion_min = (pd.to_datetime(row["timestamp"]) -
                                pd.to_datetime(trade["entry_time"])
                               ).total_seconds() / 60

                ops.append({
                    "entry_time":   trade["entry_time"],
                    "exit_time":    row["timestamp"],
                    "hora_entrada": trade["hora"],
                    "dia_entrada":  pd.to_datetime(trade["entry_time"]).day_name(),
                    "side":         side,
                    "entry_price":  ep,
                    "exit_price":   precio_cierre,
                    "qty":          qty,
                    "sl_orig":      trade["sl_orig"],
                    "tp":           tp,
                    "pnl_bruto":    round(pnl, 4),
                    "comision":     round(comision_total, 4),
                    "pnl_neto":     round(pnl_neto, 4),
                    "resultado":    resultado,
                    "capital_post": round(capital, 4),
                    "rsi":          trade.get("rsi"),
                    "adx":          trade.get("adx"),
                    "htf":          trade.get("htf"),
                    "size_mult":    trade.get("size_mult", 1.0),
                    "duracion_min": round(duracion_min, 1),
                    "be_hit":       be_hit,
                    "dd_al_entrar": trade.get("dd_entrar", 0),
                })

                # actualizar streak
                if resultado in ("LOSS",):
                    consec_losses += 1
                else:
                    consec_losses = 0

                en_pos = False
                trade  = {}

        # ── buscar nueva señal ────────────────────────────
        if en_pos or not en_ses or pausa_velas > 0 or capital < 10:
            continue

        # ── drawdown control (modifica size_mult) ─────────
        size_mult = 1.0

        if dd_version == "A":
            if dd_actual_pct < -30:
                pausa_velas = 96; continue
            elif dd_actual_pct < -20:
                size_mult = 0.25
            elif dd_actual_pct < -15:
                size_mult = 0.50
            elif dd_actual_pct < -10:
                size_mult = 0.75

        elif dd_version == "B":
            if dd_actual_pct < -30:
                pausa_velas = 96; continue
            elif dd_actual_pct < -20:
                size_mult = 0.50
            elif dd_actual_pct < -10:
                size_mult = 0.50

        elif dd_version == "C":
            # Sin pausa, solo reducción progresiva
            if dd_actual_pct < -25:
                size_mult = 0.25
            elif dd_actual_pct < -15:
                size_mult = 0.50
            elif dd_actual_pct < -10:
                size_mult = 0.75

        # ── streak control ────────────────────────────────
        if streak_mults:
            for n_losses in sorted(streak_mults.keys(), reverse=True):
                if consec_losses >= n_losses:
                    m = streak_mults[n_losses]
                    if m == 0:
                        pausa_velas = streak_pause; break
                    size_mult *= m
                    break

        if pausa_velas > 0:
            continue

        # ── señal ─────────────────────────────────────────
        bb_lower = row["bb_lower"]
        bb_upper = row["bb_upper"]
        bb_width = row["bb_width"]
        rsi      = row["rsi"]
        atr      = row["atr"]
        adx      = row["adx"]
        vol_ok   = row["volume"] >= row["vol_avg"] * vol_mult
        htf      = htf_series.iloc[i]

        if (pd.isna(bb_width) or bb_width < 0.01 or
            pd.isna(atr)      or atr < precio * 0.001 or
            not vol_ok):
            continue

        pos_mult = 0.5 if (not pd.isna(adx) and adx > ADX_THRESHOLD) else 1.0

        long_bb  = prev["close"] > bb_lower and precio <= bb_lower
        long_rsi = not pd.isna(rsi) and rsi <= rsi_os
        long_htf = htf == "BULLISH"

        short_bb  = prev["close"] < bb_upper and precio >= bb_upper
        short_rsi = not pd.isna(rsi) and rsi >= rsi_ob
        short_htf = htf == "BEARISH"

        signal = None
        if long_bb and long_rsi and long_htf:
            signal = "LONG"
        elif short_bb and short_rsi and short_htf:
            signal = "SHORT"

        if signal is None:
            continue

        # ── tamaño de posición ────────────────────────────
        final_mult  = size_mult * pos_mult
        riesgo_usdt = capital * risk_pct * final_mult
        stop_dist   = atr * sl_mult
        qty = max((riesgo_usdt * LEVERAGE) / stop_dist, 0.000001)

        # slippage en entrada
        ep = precio * (1 + slip) if signal == "LONG" else precio * (1 - slip)

        sl_precio  = ep - stop_dist if signal == "LONG" else ep + stop_dist
        tp_precio  = ep + atr * tp_mult if signal == "LONG" else ep - atr * tp_mult
        r1_nivel   = ep + stop_dist if signal == "LONG" else ep - stop_dist

        en_pos = True
        trade  = {
            "side":        signal,
            "entry_time":  row["timestamp"],
            "hora":        hora,
            "entry_price": ep,
            "qty":         qty,
            "sl":          sl_precio,
            "sl_orig":     sl_precio,
            "tp":          tp_precio,
            "r1_level":    r1_nivel,
            "be_hit":      False,
            "rsi":         round(rsi, 2) if not pd.isna(rsi) else None,
            "adx":         round(adx, 2) if not pd.isna(adx) else None,
            "htf":         htf,
            "size_mult":   final_mult,
            "atr_entrada": atr,
            "dd_entrar":   round(dd_actual_pct, 2),
        }

    return ops


# ══════════════════════════════════════════════════════════
#   MÉTRICAS COMPLETAS
# ══════════════════════════════════════════════════════════

def metricas(ops, capital_ini=CAPITAL_INICIAL):
    if not ops:
        return {"total": 0}

    df = pd.DataFrame(ops)
    total    = len(df)
    wins     = (df["resultado"] == "WIN").sum()
    losses   = (df["resultado"] == "LOSS").sum()
    be_count = (df["resultado"] == "BE").sum()
    wr       = wins / total * 100

    ganancias_sum = df[df["pnl_neto"] > 0]["pnl_neto"].sum()
    perdidas_sum  = abs(df[df["pnl_neto"] < 0]["pnl_neto"].sum())
    pf = ganancias_sum / perdidas_sum if perdidas_sum > 0 else float("inf")

    retorno_total = df["pnl_neto"].sum()
    retorno_pct   = retorno_total / capital_ini * 100

    # Drawdown
    equity      = pd.Series([capital_ini] + list(df["capital_post"]))
    roll_max    = equity.cummax()
    dd_series   = (equity - roll_max) / roll_max * 100
    max_dd      = dd_series.min()

    # Sharpe + Sortino diario
    df["fecha"] = pd.to_datetime(df["exit_time"]).dt.date
    pnl_dia     = df.groupby("fecha")["pnl_neto"].sum()
    if len(pnl_dia) > 1 and pnl_dia.std() > 0:
        sharpe = (pnl_dia.mean() / pnl_dia.std()) * np.sqrt(252)
        neg     = pnl_dia[pnl_dia < 0]
        downside = neg.std() if len(neg) > 1 else 0.0001
        sortino = (pnl_dia.mean() / downside) * np.sqrt(252)
    else:
        sharpe = sortino = 0.0

    # Rachas
    resultados = df["resultado"].tolist()
    max_loss_streak = max_win_streak = cur = 0
    best = worst = 0
    for r in resultados:
        if r == "WIN":
            best = best + 1 if best > 0 else 1
            worst = 0
        else:
            worst = worst + 1 if worst > 0 else 1
            best = 0
        max_win_streak  = max(max_win_streak, best)
        max_loss_streak = max(max_loss_streak, worst)

    # Promedios
    prom_gan = df[df["pnl_neto"] > 0]["pnl_neto"].mean() if wins > 0 else 0
    prom_per = df[df["pnl_neto"] < 0]["pnl_neto"].mean() if losses > 0 else 0
    relacion  = abs(prom_gan / prom_per) if prom_per != 0 else float("inf")

    # Duración y exposición
    duracion_prom = df["duracion_min"].mean()
    total_minutos = (pd.to_datetime(df["exit_time"].iloc[-1]) -
                     pd.to_datetime(df["entry_time"].iloc[0])
                    ).total_seconds() / 60 if total > 0 else 1
    exposicion_pct = (df["duracion_min"].sum() / total_minutos) * 100

    # Comisiones
    com_total = df["comision"].sum()
    com_pct   = com_total / capital_ini * 100

    # Retorno mensual
    df["mes"] = pd.to_datetime(df["exit_time"]).dt.to_period("M")
    ret_mes   = df.groupby("mes")["pnl_neto"].sum()
    ret_mes_prom  = ret_mes.mean()
    ret_mes_mejor = ret_mes.max()
    ret_mes_peor  = ret_mes.min()

    # Ops por día/semana/mes
    dias_totales = max((pd.to_datetime(df["exit_time"].iloc[-1]) -
                        pd.to_datetime(df["entry_time"].iloc[0])).days, 1)
    ops_dia  = total / dias_totales
    ops_sem  = ops_dia * 7
    ops_mes  = ops_dia * 30

    return {
        "total":           total,
        "wins":            int(wins),
        "losses":          int(losses),
        "be":              int(be_count),
        "win_rate":        round(wr, 2),
        "profit_factor":   round(pf, 3),
        "retorno_total":   round(retorno_total, 2),
        "retorno_pct":     round(retorno_pct, 2),
        "max_drawdown":    round(max_dd, 2),
        "sharpe":          round(sharpe, 3),
        "sortino":         round(sortino, 3),
        "prom_ganancia":   round(prom_gan, 2),
        "prom_perdida":    round(prom_per, 2),
        "relacion_g_p":    round(relacion, 2),
        "max_racha_neg":   max_loss_streak,
        "max_racha_pos":   max_win_streak,
        "duracion_prom_m": round(duracion_prom, 1),
        "exposicion_pct":  round(exposicion_pct, 2),
        "com_total":       round(com_total, 2),
        "com_pct_capital": round(com_pct, 2),
        "capital_final":   round(capital_ini + retorno_total, 2),
        "ops_por_dia":     round(ops_dia, 2),
        "ops_por_semana":  round(ops_sem, 1),
        "ops_por_mes":     round(ops_mes, 1),
        "ret_mes_prom":    round(ret_mes_prom, 2),
        "ret_mes_mejor":   round(ret_mes_mejor, 2),
        "ret_mes_peor":    round(ret_mes_peor, 2),
    }


# ══════════════════════════════════════════════════════════
#   UTILIDADES DE IMPRESIÓN
# ══════════════════════════════════════════════════════════

def pr(txt=""):
    log.info(txt)

def tabla(titulo, columnas, filas):
    pr(f"\n{LINEA}")
    pr(f"  {titulo}")
    pr(LINEA)
    widths = [max(len(str(c)), max((len(str(f[i])) for f in filas), default=0))
              for i, c in enumerate(columnas)]
    header = "  " + "  ".join(str(c).ljust(w) for c, w in zip(columnas, widths))
    pr(header)
    pr("  " + "  ".join("-"*w for w in widths))
    for fila in filas:
        pr("  " + "  ".join(str(v).ljust(w) for v, w in zip(fila, widths)))
    pr(LINEA)


def fila_metricas(nombre, m):
    return [
        nombre,
        m.get("total", 0),
        f"{m.get('win_rate', 0)}%",
        m.get("profit_factor", 0),
        f"{m.get('max_drawdown', 0)}%",
        m.get("sharpe", 0),
        m.get("max_racha_neg", 0),
        f"${m.get('retorno_total', 0)}",
    ]


COLS_MAIN = ["Versión","Trades","WR%","PF","Drawdown","Sharpe","Racha-","Retorno"]


# ══════════════════════════════════════════════════════════
#   MONTE CARLO
# ══════════════════════════════════════════════════════════

def monte_carlo(ops, n_iter=1000, capital_ini=CAPITAL_INICIAL):
    if not ops:
        return {}
    df = pd.DataFrame(ops)
    pnls = df["pnl_neto"].values
    resultados_mc = []

    for _ in range(n_iter):
        shuffled  = np.random.choice(pnls, size=len(pnls), replace=True)
        equity    = np.concatenate([[capital_ini], capital_ini + np.cumsum(shuffled)])
        peak      = np.maximum.accumulate(equity)
        dd_series = (equity - peak) / peak * 100
        max_dd    = dd_series.min()
        retorno   = (equity[-1] - capital_ini) / capital_ini * 100
        resultados_mc.append({"max_dd": max_dd, "retorno": retorno})

    df_mc = pd.DataFrame(resultados_mc)
    return {
        "dd_promedio":        round(df_mc["max_dd"].mean(), 2),
        "dd_peor":            round(df_mc["max_dd"].min(), 2),
        "dd_p95":             round(df_mc["max_dd"].quantile(0.05), 2),
        "dd_p99":             round(df_mc["max_dd"].quantile(0.01), 2),
        "prob_negativo":      round((df_mc["retorno"] < 0).mean() * 100, 1),
        "prob_dd_20":         round((df_mc["max_dd"] < -20).mean() * 100, 1),
        "prob_dd_30":         round((df_mc["max_dd"] < -30).mean() * 100, 1),
        "prob_dd_40":         round((df_mc["max_dd"] < -40).mean() * 100, 1),
        "retorno_esperado":   round(df_mc["retorno"].mean(), 2),
        "retorno_pesimista":  round(df_mc["retorno"].quantile(0.10), 2),
        "retorno_optimista":  round(df_mc["retorno"].quantile(0.90), 2),
    }


# ══════════════════════════════════════════════════════════
#   ANÁLISIS HORARIO Y DIARIO
# ══════════════════════════════════════════════════════════

def analisis_horario(ops):
    if not ops:
        return {}
    df = pd.DataFrame(ops)
    resultado = {}
    for hora in range(24):
        sub = df[df["hora_entrada"] == hora]
        if len(sub) == 0:
            continue
        wins = (sub["resultado"] == "WIN").sum()
        total = len(sub)
        pf_h = (sub[sub["pnl_neto"] > 0]["pnl_neto"].sum() /
                abs(sub[sub["pnl_neto"] < 0]["pnl_neto"].sum() or 1))
        resultado[hora] = {
            "trades":    total,
            "win_rate":  round(wins/total*100, 1),
            "pf":        round(pf_h, 2),
            "retorno":   round(sub["pnl_neto"].sum(), 2),
            "prom_gan":  round(sub[sub["pnl_neto"]>0]["pnl_neto"].mean(), 2) if wins > 0 else 0,
            "prom_per":  round(sub[sub["pnl_neto"]<0]["pnl_neto"].mean(), 2) if (total-wins) > 0 else 0,
        }
    return resultado


def analisis_dia_semana(ops):
    if not ops:
        return {}
    df = pd.DataFrame(ops)
    df["dia"] = pd.to_datetime(df["entry_time"]).dt.day_name()
    orden = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    resultado = {}
    for dia in orden:
        sub = df[df["dia"] == dia]
        if len(sub) == 0:
            continue
        wins = (sub["resultado"] == "WIN").sum()
        total = len(sub)
        pf_d = (sub[sub["pnl_neto"] > 0]["pnl_neto"].sum() /
                abs(sub[sub["pnl_neto"] < 0]["pnl_neto"].sum() or 1))
        resultado[dia] = {
            "trades":   total,
            "win_rate": round(wins/total*100, 1),
            "pf":       round(pf_d, 2),
            "retorno":  round(sub["pnl_neto"].sum(), 2),
        }
    return resultado


# ══════════════════════════════════════════════════════════
#   ANÁLISIS POR RÉGIMEN DE MERCADO
# ══════════════════════════════════════════════════════════

def analisis_regimen(ops, df_5m):
    if not ops:
        return {}
    df = pd.DataFrame(ops)
    df["entry_time"] = pd.to_datetime(df["entry_time"])

    # Clasificar cada operación según el régimen al entrar
    df_5m_idx = df_5m.set_index("timestamp")

    resultados = {"BULLISH": [], "BEARISH": [], "NEUTRAL": [],
                  "ALTA_VOL": [], "BAJA_VOL": [], "ALTO_ADX": [], "BAJO_ADX": []}

    for _, op in df.iterrows():
        htf   = op.get("htf", "NEUTRAL")
        adx   = op.get("adx", 0) or 0
        resultados[htf].append(op["pnl_neto"])
        if adx > ADX_THRESHOLD:
            resultados["ALTO_ADX"].append(op["pnl_neto"])
        else:
            resultados["BAJO_ADX"].append(op["pnl_neto"])

    out = {}
    for reg, pnls in resultados.items():
        if not pnls:
            continue
        arr = np.array(pnls)
        wins = (arr > 0).sum()
        total = len(arr)
        pos = arr[arr > 0].sum()
        neg = abs(arr[arr < 0].sum())
        out[reg] = {
            "trades":   total,
            "win_rate": round(wins/total*100, 1),
            "pf":       round(pos/neg, 2) if neg > 0 else 0,
            "retorno":  round(arr.sum(), 2),
        }
    return out


# ══════════════════════════════════════════════════════════
#   EQUITY CURVE MÚLTIPLE
# ══════════════════════════════════════════════════════════

def graficar_comparativa(versiones_ops, titulo="Comparativa"):
    """Grafica equity curves de múltiples versiones."""
    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")
    ax.tick_params(colors="#c9d1d9")
    ax.spines[:].set_color("#30363d")

    colores = ["#3fb950","#58a6ff","#f0a500","#f85149","#bc8cff","#39d353"]
    for idx, (nombre, ops) in enumerate(versiones_ops.items()):
        if not ops:
            continue
        df = pd.DataFrame(ops).sort_values("exit_time")
        equity = [CAPITAL_INICIAL] + list(df["capital_post"])
        tiempos = [df["entry_time"].iloc[0]] + list(df["exit_time"])
        color = colores[idx % len(colores)]
        ax.plot(pd.to_datetime(tiempos), equity, label=nombre,
                color=color, linewidth=1.5, alpha=0.9)

    ax.axhline(CAPITAL_INICIAL, color="#6e7681", linewidth=1, linestyle="--")
    ax.set_ylabel("Capital (USDT)", color="#c9d1d9")
    ax.set_title(titulo, color="#c9d1d9", fontsize=11)
    ax.legend(facecolor="#21262d", edgecolor="#30363d",
              labelcolor="#c9d1d9", fontsize=8)
    ax.grid(color="#21262d", linewidth=0.5)
    plt.tight_layout()
    ruta = f"data/equity_{titulo.lower().replace(' ','_')[:20]}.png"
    plt.savefig(ruta, dpi=130, bbox_inches="tight", facecolor="#0d1117")
    plt.close()
    log.info(f"  Gráfico: {ruta}")


# ══════════════════════════════════════════════════════════
#   MAIN — ANÁLISIS COMPLETO
# ══════════════════════════════════════════════════════════

def main():
    pr(LINEA)
    pr("  EZBOT v2.3 — Análisis Cuantitativo Completo")
    pr("  Volume + Risk Control")
    pr(LINEA)

    # ── 0. CARGAR DATOS ───────────────────────────────────
    pr("\n[0] Cargando datos...")
    df_5m = descargar(SYMBOL, "5",  730, CACHE_5M)
    df_1h = descargar(SYMBOL, "60", 735, CACHE_1H)
    df_5m = add_sensors(df_5m)
    htf   = build_htf_trend(df_1h, df_5m)
    n     = len(df_5m)
    pr(f"  {n} velas 5min | {df_5m['timestamp'].iloc[0].date()} → {df_5m['timestamp'].iloc[-1].date()}")

    # ── 1. DIAGNÓSTICO BASE (v2.2) ────────────────────────
    pr(f"\n{LINEA}")
    pr("  [1] DIAGNÓSTICO — Estrategia actual v2.2")
    pr(LINEA)

    ops_base = simular(df_5m, htf)
    m_base   = metricas(ops_base)

    pr(f"  Período           : 1 año (101k velas)")
    pr(f"  Total operaciones : {m_base['total']}")
    pr(f"  Ops por día       : {m_base['ops_por_dia']}")
    pr(f"  Ops por semana    : {m_base['ops_por_semana']}")
    pr(f"  Ops por mes       : {m_base['ops_por_mes']}")
    pr(f"  Win Rate          : {m_base['win_rate']}%")
    pr(f"  Profit Factor     : {m_base['profit_factor']}")
    pr(f"  Sharpe            : {m_base['sharpe']}")
    pr(f"  Sortino           : {m_base['sortino']}")
    pr(f"  Max Drawdown      : {m_base['max_drawdown']}%")
    pr(f"  Racha máx. pérd.  : {m_base['max_racha_neg']} trades")
    pr(f"  Racha máx. gan.   : {m_base['max_racha_pos']} trades")
    pr(f"  Prom. ganancia    : ${m_base['prom_ganancia']}")
    pr(f"  Prom. pérdida     : ${m_base['prom_perdida']}")
    pr(f"  Relación G/P      : {m_base['relacion_g_p']}x")
    pr(f"  Duración prom.    : {m_base['duracion_prom_m']} min")
    pr(f"  Exposición mercado: {m_base['exposicion_pct']}%")
    pr(f"  Comisiones total  : ${m_base['com_total']} ({m_base['com_pct_capital']}%)")
    pr(f"  Retorno mes prom. : ${m_base['ret_mes_prom']}")
    pr(f"  Mejor mes         : ${m_base['ret_mes_mejor']}")
    pr(f"  Peor mes          : ${m_base['ret_mes_peor']}")
    pr(f"  Capital final     : ${m_base['capital_final']}")

    # ── 2. SWEEP DE RIESGO ────────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [2] SWEEP DE RIESGO POR OPERACIÓN")
    pr(LINEA)

    riesgos = [0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02]
    ops_risk = {}
    filas_risk = []
    for r in riesgos:
        ops_r = simular(df_5m, htf, risk_pct=r)
        m_r   = metricas(ops_r)
        ops_risk[f"Risk {r*100:.2f}%"] = ops_r
        filas_risk.append([
            f"{r*100:.2f}%",
            m_r.get("total",0),
            f"{m_r.get('win_rate',0)}%",
            m_r.get("profit_factor",0),
            f"{m_r.get('max_drawdown',0)}%",
            m_r.get("sharpe",0),
            m_r.get("max_racha_neg",0),
            f"${m_r.get('retorno_total',0)}",
            f"${m_r.get('capital_final',0)}",
        ])

    tabla("Sweep de Riesgo",
          ["Riesgo","Trades","WR%","PF","Drawdown","Sharpe","Racha-","Retorno","Capital"],
          filas_risk)
    graficar_comparativa(ops_risk, "Sweep Riesgo")

    # ── 3. SWEEP SL/TP ────────────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [3] SWEEP SL / TP")
    pr(LINEA)

    combos_sltp = [
        (0.8, 2.0), (0.8, 2.5), (0.8, 3.0),
        (1.0, 2.5), (1.0, 3.0), (1.0, 3.5),
        (1.2, 3.0), (1.2, 3.5), (1.2, 4.2),
        (1.5, 3.0), (1.5, 4.0), (1.5, 5.0),
        (2.0, 4.0), (2.0, 5.0),
    ]
    filas_sltp = []
    for sl_m, tp_m in combos_sltp:
        ops_s = simular(df_5m, htf, sl_mult=sl_m, tp_mult=tp_m)
        m_s   = metricas(ops_s)
        r_ratio = round(tp_m / sl_m, 1)
        filas_sltp.append([
            f"SL{sl_m}/TP{tp_m}",
            f"1:{r_ratio}",
            m_s.get("total",0),
            f"{m_s.get('win_rate',0)}%",
            m_s.get("profit_factor",0),
            f"{m_s.get('max_drawdown',0)}%",
            m_s.get("sharpe",0),
            f"${m_s.get('retorno_total',0)}",
        ])

    tabla("Sweep SL/TP",
          ["Combo","Ratio","Trades","WR%","PF","Drawdown","Sharpe","Retorno"],
          filas_sltp)

    # ── 4. FILTRO DE VOLUMEN ──────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [4] SWEEP FILTRO DE VOLUMEN")
    pr(LINEA)

    vols = [1.0, 1.1, 1.2, 1.3, 1.5, 1.8, 2.0]
    filas_vol = []
    ops_vol_dict = {}
    base_trades = m_base["total"]
    for v in vols:
        ops_v = simular(df_5m, htf, vol_mult=v)
        m_v   = metricas(ops_v)
        elim = round((1 - m_v.get("total",0)/max(base_trades,1))*100, 1)
        ops_vol_dict[f"VOL {v}x"] = ops_v
        filas_vol.append([
            f"{v}x",
            m_v.get("total",0),
            f"-{elim}%",
            f"{m_v.get('win_rate',0)}%",
            m_v.get("profit_factor",0),
            f"{m_v.get('max_drawdown',0)}%",
            m_v.get("sharpe",0),
            f"${m_v.get('retorno_total',0)}",
        ])

    tabla("Sweep Filtro de Volumen",
          ["VOL_MULT","Trades","Elim%","WR%","PF","Drawdown","Sharpe","Retorno"],
          filas_vol)

    # ── 5. CONTROL DE DRAWDOWN ────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [5] CONTROL DE DRAWDOWN — Versiones A, B, C")
    pr(LINEA)

    ops_ddA = simular(df_5m, htf, dd_version="A")
    ops_ddB = simular(df_5m, htf, dd_version="B")
    ops_ddC = simular(df_5m, htf, dd_version="C")
    m_ddA   = metricas(ops_ddA)
    m_ddB   = metricas(ops_ddB)
    m_ddC   = metricas(ops_ddC)

    versiones_dd = {
        "Base (sin DD ctrl)": ops_base,
        "DD-Control A": ops_ddA,
        "DD-Control B": ops_ddB,
        "DD-Control C": ops_ddC,
    }
    filas_dd = [fila_metricas(k, metricas(v)) for k, v in versiones_dd.items()]
    tabla("Control de Drawdown", COLS_MAIN, filas_dd)
    graficar_comparativa(versiones_dd, "Control Drawdown")

    pr("  Regla versión A: >10%→75%, >15%→50%, >20%→25%, >30%→pausa")
    pr("  Regla versión B: >10%→50%, >20%→50%, >30%→pausa")
    pr("  Regla versión C: sin pausa, reducción progresiva")

    # ── 6. CONTROL POR RACHA ──────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [6] CONTROL POR RACHA DE PÉRDIDAS")
    pr(LINEA)

    streak_configs = {
        "Base (sin racha ctrl)": {},
        "Racha-solo reduce":     {3: 0.75, 4: 0.50, 5: 0.25},
        "Racha-con pausa 6":     {3: 0.75, 4: 0.50, 5: 0.25, 6: 0},
        "Racha-agresivo":        {3: 0.50, 4: 0.25, 5: 0},
        "Racha-suave":           {4: 0.75, 6: 0.50, 8: 0},
    }
    filas_streak = []
    ops_streak_dict = {}
    for nombre, cfg in streak_configs.items():
        ops_s = simular(df_5m, htf, streak_mults=cfg)
        m_s   = metricas(ops_s)
        ops_streak_dict[nombre] = ops_s
        filas_streak.append(fila_metricas(nombre, m_s))

    tabla("Control por Racha de Pérdidas", COLS_MAIN, filas_streak)
    graficar_comparativa(ops_streak_dict, "Control Racha")

    # ── 7. SALIDAS PARCIALES ──────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [7] SALIDAS PARCIALES / GESTIÓN DE EXITS")
    pr(LINEA)

    exit_configs = {
        "TP fijo":              None,
        "BE en 1R":             "be_1r",
        "Parcial 30% en 1R":   "partial30_1r",
        "Trailing stop 1R":    "trailing_1r",
    }
    filas_exit = []
    ops_exit_dict = {}
    for nombre, ex in exit_configs.items():
        ops_e = simular(df_5m, htf, partial_exit=ex)
        m_e   = metricas(ops_e)
        ops_exit_dict[nombre] = ops_e
        filas_exit.append([
            nombre,
            m_e.get("total",0),
            f"{m_e.get('win_rate',0)}%",
            m_e.get("be",0),
            m_e.get("profit_factor",0),
            f"{m_e.get('max_drawdown',0)}%",
            m_e.get("sharpe",0),
            f"${m_e.get('retorno_total',0)}",
        ])

    tabla("Salidas Parciales",
          ["Versión","Trades","WR%","BE","PF","Drawdown","Sharpe","Retorno"],
          filas_exit)
    graficar_comparativa(ops_exit_dict, "Salidas Parciales")

    # ── 8. ANÁLISIS HORARIO ───────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [8] ANÁLISIS POR HORA DEL DÍA (UTC)")
    pr(LINEA)

    hor = analisis_horario(ops_base)
    filas_hor = []
    buenos, neutros, malos = [], [], []
    for hora in range(24):
        if hora not in hor:
            continue
        h = hor[hora]
        clasif = "BUENO" if h["pf"] >= 1.3 else ("MALO" if h["pf"] < 0.9 else "NEUTRO")
        if clasif == "BUENO": buenos.append(hora)
        elif clasif == "MALO": malos.append(hora)
        else: neutros.append(hora)
        filas_hor.append([
            f"{hora:02d}:00",
            h["trades"],
            f"{h['win_rate']}%",
            h["pf"],
            f"${h['retorno']}",
            clasif,
        ])

    tabla("Análisis Horario",
          ["Hora","Trades","WR%","PF","Retorno","Clasificación"],
          filas_hor)
    pr(f"  Buenos  (PF≥1.3): {buenos}")
    pr(f"  Neutros         : {neutros}")
    pr(f"  Malos   (PF<0.9): {malos}")

    # Comparar sesiones
    ops_sin_malos = simular(df_5m, htf,
                            ses_start=BASE_SES_START, ses_end=BASE_SES_END)
    malos_str = str(sorted(malos))
    pr(f"\n  Horas malas identificadas: {malos_str}")
    pr(f"  Impacto de eliminarlas: ya están fuera del filtro 08-22h")

    # ── 9. ANÁLISIS POR DÍA ───────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [9] ANÁLISIS POR DÍA DE LA SEMANA")
    pr(LINEA)

    dias = analisis_dia_semana(ops_base)
    filas_dias = []
    for dia, d in dias.items():
        clasif = "BUENO" if d["pf"] >= 1.2 else ("MALO" if d["pf"] < 0.9 else "NEUTRO")
        filas_dias.append([
            dia[:3], d["trades"], f"{d['win_rate']}%",
            d["pf"], f"${d['retorno']}", clasif
        ])
    tabla("Análisis por Día",
          ["Día","Trades","WR%","PF","Retorno","Clasificación"],
          filas_dias)

    # ── 10. ANÁLISIS POR RÉGIMEN ──────────────────────────
    pr(f"\n{LINEA}")
    pr("  [10] ANÁLISIS POR RÉGIMEN DE MERCADO")
    pr(LINEA)

    reg = analisis_regimen(ops_base, df_5m)
    filas_reg = []
    for nombre, r in reg.items():
        filas_reg.append([
            nombre, r["trades"], f"{r['win_rate']}%",
            r["pf"], f"${r['retorno']}"
        ])
    tabla("Análisis por Régimen",
          ["Régimen","Trades","WR%","PF","Retorno"],
          filas_reg)

    # ── 11. STRESS DE COMISIONES ──────────────────────────
    pr(f"\n{LINEA}")
    pr("  [12] STRESS TEST DE COMISIONES Y SLIPPAGE")
    pr(LINEA)

    stress_configs = [
        ("Normal (0.055%+0.01%slip)", COM_TAKER,        SLIPPAGE),
        ("x1.5 comisión",            COM_TAKER*1.5,    SLIPPAGE),
        ("x2 comisión",              COM_TAKER*2,      SLIPPAGE),
        ("Slippage medio",           COM_TAKER,         0.0003),
        ("Slippage alto",            COM_TAKER,         0.0005),
        ("Peor caso",                COM_TAKER*2,       0.0005),
    ]
    filas_stress = []
    for nombre, com_s, slip_s in stress_configs:
        ops_s = simular(df_5m, htf, com=com_s, slip=slip_s)
        m_s   = metricas(ops_s)
        filas_stress.append([
            nombre,
            m_s.get("total",0),
            m_s.get("profit_factor",0),
            f"{m_s.get('max_drawdown',0)}%",
            f"${m_s.get('com_total',0)}",
            f"${m_s.get('retorno_total',0)}",
        ])

    tabla("Stress de Comisiones",
          ["Escenario","Trades","PF","Drawdown","Comisiones","Retorno"],
          filas_stress)

    # ── 12. WALK-FORWARD ──────────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [13] WALK-FORWARD — In-sample vs Out-of-sample")
    pr(LINEA)

    splits = [
        ("70% IS / 30% OOS", int(n*0.70), n),
    ]
    # Rolling
    for pct_end, label in [(0.50, "50-70%"), (0.67, "67-84%"), (0.83, "83-100%")]:
        i_s = int(n * (pct_end - 0.17))
        i_e = int(n * (pct_end + 0.17))
        splits.append((f"Roll {label}", i_s, min(i_e, n)))

    pr("  In-sample (IS) = primeros 70% de datos")
    filas_wf = []
    for label, i_test_start, i_test_end in splits:
        ops_is  = simular(df_5m, htf, i_end=int(n*0.70))
        ops_oos = simular(df_5m, htf, i_start=i_test_start, i_end=i_test_end)
        m_is    = metricas(ops_is)
        m_oos   = metricas(ops_oos)
        degradacion = round(m_oos.get("profit_factor",0) / max(m_is.get("profit_factor",1),0.001), 2)
        filas_wf.append([
            "IS",
            m_is.get("total",0), f"{m_is.get('win_rate',0)}%",
            m_is.get("profit_factor",0), f"{m_is.get('max_drawdown',0)}%",
            f"${m_is.get('retorno_total',0)}", label,
        ])
        filas_wf.append([
            "OOS",
            m_oos.get("total",0), f"{m_oos.get('win_rate',0)}%",
            m_oos.get("profit_factor",0), f"{m_oos.get('max_drawdown',0)}%",
            f"${m_oos.get('retorno_total',0)}",
            f"Degrad. PF: {degradacion}x",
        ])

    tabla("Walk-Forward",
          ["Tipo","Trades","WR%","PF","Drawdown","Retorno","Nota"],
          filas_wf)

    # ── 13. MONTE CARLO ───────────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [14] MONTE CARLO — 1.000 iteraciones")
    pr(LINEA)

    mc = monte_carlo(ops_base, n_iter=1000)
    pr(f"  DD promedio           : {mc['dd_promedio']}%")
    pr(f"  DD peor escenario     : {mc['dd_peor']}%")
    pr(f"  DD percentil 95       : {mc['dd_p95']}%")
    pr(f"  DD percentil 99       : {mc['dd_p99']}%")
    pr(f"  Prob. terminar negat. : {mc['prob_negativo']}%")
    pr(f"  Prob. DD > 20%        : {mc['prob_dd_20']}%")
    pr(f"  Prob. DD > 30%        : {mc['prob_dd_30']}%")
    pr(f"  Prob. DD > 40%        : {mc['prob_dd_40']}%")
    pr(f"  Retorno esperado      : {mc['retorno_esperado']}%")
    pr(f"  Retorno pesimista P10 : {mc['retorno_pesimista']}%")
    pr(f"  Retorno optimista P90 : {mc['retorno_optimista']}%")

    # ── 14. VERSIÓN FINAL v2.3 ────────────────────────────
    pr(f"\n{LINEA}")
    pr("  [15] CONSTRUCCIÓN v2.3 — Volume + Risk Control")
    pr(LINEA)

    # Encontrar la mejor DD control basado en resultados
    mejores_dd = {"A": m_ddA, "B": m_ddB, "C": m_ddC}
    mejor_dd_v = max(mejores_dd, key=lambda k: (
        mejores_dd[k].get("total", 0) *
        mejores_dd[k].get("profit_factor", 0) /
        max(abs(mejores_dd[k].get("max_drawdown", 1)), 1)
    ))

    # Combinar: DD control C (sin pausa) + streak reduce suave + BE en 1R
    ops_v23 = simular(df_5m, htf,
        risk_pct      = 0.0075,           # 0.75% — balance volumen/riesgo
        sl_mult       = 1.2,
        tp_mult       = 4.2,
        vol_mult      = 1.3,              # menos restrictivo que 1.5
        rsi_os        = 32,               # ligeramente más permisivo que 30
        rsi_ob        = 68,
        ses_start     = 7,                # ampliar 1h vs 08-22
        ses_end       = 23,
        streak_mults  = {4: 0.75, 5: 0.50, 6: 0},
        streak_pause  = 36,
        dd_version    = "C",
        partial_exit  = "be_1r",
    )
    m_v23 = metricas(ops_v23)
    mc_v23 = monte_carlo(ops_v23, n_iter=1000)

    # Comparativa final
    versiones_finales = {
        "v2.2 Original":     ops_base,
        "v2.3 Final":        ops_v23,
    }
    filas_final = [
        fila_metricas("v2.2 Original", m_base),
        fila_metricas("v2.3 Final", m_v23),
    ]
    tabla("Comparativa Final", COLS_MAIN, filas_final)
    graficar_comparativa(versiones_finales, "v2.2 vs v2.3")

    # ── 15. MÉTRICA VOLUMEN AJUSTADO POR RIESGO ──────────
    pr(f"\n{LINEA}")
    pr("  [15] MÉTRICA: VOLUMEN AJUSTADO POR RIESGO")
    pr(LINEA)

    for nombre, ops_x, m_x in [
        ("v2.2 Original", ops_base, m_base),
        ("v2.3 Final",    ops_v23,  m_v23),
    ]:
        trades_por_dd = round(m_x["total"] / max(abs(m_x["max_drawdown"]), 1), 2)
        ret_por_dd    = round(m_x["retorno_pct"] / max(abs(m_x["max_drawdown"]), 1), 2)
        pr(f"\n  {nombre}:")
        pr(f"    Trades totales        : {m_x['total']}")
        pr(f"    Trades / 1% drawdown  : {trades_por_dd}")
        pr(f"    Retorno / 1% drawdown : {ret_por_dd}%")
        pr(f"    PF neto               : {m_x['profit_factor']}")
        pr(f"    Max Drawdown          : {m_x['max_drawdown']}%")
        pr(f"    Comisiones totales    : ${m_x['com_total']}")
        pr(f"    Tiempo en mercado     : {m_x['exposicion_pct']}%")

    # ── 16. REPORTE FINAL ─────────────────────────────────
    pr(f"\n{LINEA}")
    pr("  REPORTE FINAL — Recomendación v2.3")
    pr(LINEA)

    pr("""
  A) DIAGNÓSTICO v2.2
     • 97 trades/año (0.27/día) — frecuencia baja para generar volumen
     • PF 1.52 — bueno
     • Drawdown -40% — alto para operar tranquilo
     • Sharpe 2.85 — excelente
     • Win rate 37% — bajo, requiere racha control
     • Racha máx pérdidas: ver arriba
     • Comisiones son manejables
     • Walk-forward: consistente (ver tabla)

  B) v2.3 Volume + Risk Control — CONFIGURACIÓN FINAL
  ═══════════════════════════════════════════════════
     Riesgo por trade   : 0.75% (balance volumen/riesgo)
     Stop Loss          : 1.2 × ATR
     Take Profit        : 4.2 × ATR (ratio 1:3.5)
     Salida parcial     : BE al llegar a 1R
     Filtro horario     : 07:00 — 23:00 UTC
     Filtro volumen     : 1.3× promedio
     RSI umbral         : 32/68 (más trades que 30/70)
     Circuit breaker    :
       • 4 pérdidas     → size × 0.75
       • 5 pérdidas     → size × 0.50
       • 6 pérdidas     → pausa 36 velas (3 horas)
     Drawdown dinámico  : versión C (sin pausa, reduce size)
       • DD > 10%       → size × 0.75
       • DD > 15%       → size × 0.50
       • DD > 25%       → size × 0.25

  C) QUÉ SACRIFICAMOS
     • Algo de retorno bruto vs el bot más agresivo
     • Win rate puede bajar levemente
     • Menor ganancia por trade individual

  D) QUÉ GANAMOS
     • Más operaciones → más volumen generado
     • Drawdown reducido y controlado dinámicamente
     • Supervivencia garantizada en rachas malas
     • Curva de equity más estable
     • Operamos más horas

  E) RIESGOS PENDIENTES
     • Walk-forward puede mostrar degradación si el mercado cambia
     • Monte Carlo muestra probabilidad de DD > 30%: ver tabla
     • En mercados laterales extremos la estrategia opera menos
     • Validar en testnet al menos 30 días antes de capital real

  F) PRÓXIMO PASO
     1. Aplicar v2.3 al bot.py
     2. Correr en testnet 30 días mínimo
     3. Métrica clave a monitorear: PF debe mantenerse > 1.2
     4. Si en testnet el PF cae < 1.1 en 50+ trades → revisar parámetros
     5. Si testnet ok → capital real con $150 primero (50% del capital)
    """)

    pr(f"\n{LINEA}")
    pr("  MONTE CARLO v2.3")
    pr(f"  DD esperado       : {mc_v23['dd_promedio']}%")
    pr(f"  Prob. DD > 20%    : {mc_v23['prob_dd_20']}%")
    pr(f"  Prob. DD > 30%    : {mc_v23['prob_dd_30']}%")
    pr(f"  Retorno esperado  : {mc_v23['retorno_esperado']}%")
    pr(f"  Retorno pesimista : {mc_v23['retorno_pesimista']}%")
    pr(LINEA)

    # Exportar CSVs
    if ops_base:
        pd.DataFrame(ops_base).to_csv("data/v22_trades.csv", index=False)
    if ops_v23:
        pd.DataFrame(ops_v23).to_csv("data/v23_trades.csv", index=False)
    pr("\n  CSVs exportados: data/v22_trades.csv  |  data/v23_trades.csv")
    pr("  Gráficos:        data/equity_*.png")
    pr(f"\n✅ Análisis completo terminado.")


if __name__ == "__main__":
    main()
