"""
╔══════════════════════════════════════════════════════════╗
║   AUTOBOT v3.0 — App de Escritorio Multi-par             ║
║   Por Ezequiel Sack — Proyecto experimental              ║
║                                                          ║
║   Opera BTC + ETH + SOL simultáneamente.                 ║
║   Solo 1 posición a la vez (elige el mejor par).         ║
║   Parámetros optimizados por backtest de 1 año.          ║
║                                                          ║
║   Ezequiel Sack NO tiene acceso a ningún fondo.          ║
╚══════════════════════════════════════════════════════════╝
"""

import threading
import time
import queue
import os
import sys
from datetime import datetime, timezone, timedelta

import tkinter as tk
from tkinter import ttk, messagebox

# Dependencias del bot
try:
    from pybit.unified_trading import HTTP
    import pandas as pd
    import numpy as np
except ImportError:
    HTTP = None

# ═══════════════════════════════════════════════════════════
#   CONFIGURACIÓN MULTI-PAR (validada por backtest de 1 año)
# ═══════════════════════════════════════════════════════════
LEVERAGE       = 3
RISK_PER_TRADE = 0.005          # 0.5% base
TIMEFRAME      = "5"            # 5 minutos — ejecución
TIMEFRAME_HTF  = "60"           # 1 hora — filtro tendencia
LOOP_SLEEP     = 60             # segundos entre ciclos

# Parámetros generales
BB_PERIOD = 20
BB_STD    = 2.0
RSI_PERIOD     = 14
ATR_PERIOD     = 14
EMA_HTF_PERIOD = 50
VOL_PERIOD     = 20
ADX_PERIOD     = 14
ADX_THRESHOLD  = 25

# ── Configuración POR PAR (cada par tiene sus umbrales) ──
# Resultados backtest 1 año:
#   BTC: PF 2.40, DD -16%, 62 trades  ← validado
#   ETH: PF 1.50, DD -22%, 177 trades ← optimizado
#   SOL: PF 1.40, DD -22%, 251 trades, WR 50.6% ← optimizado
PARES_CONFIG = {
    "BTCUSDT": {
        "priority": 1, "risk_weight": 0.50, "min_score": 60,
        "rsi_oversold": 30, "rsi_overbought": 70,
        "sl_mult": 1.2, "tp_mult": 4.2,
        "vol_mult": 1.5, "bb_min_width": 0.010,
    },
    "ETHUSDT": {
        "priority": 2, "risk_weight": 0.30, "min_score": 65,
        "rsi_oversold": 25, "rsi_overbought": 70,
        "sl_mult": 1.5, "tp_mult": 3.5,
        "vol_mult": 1.8, "bb_min_width": 0.012,
    },
    "SOLUSDT": {
        "priority": 3, "risk_weight": 0.20, "min_score": 75,
        "rsi_oversold": 25, "rsi_overbought": 65,
        "sl_mult": 1.5, "tp_mult": 2.5,
        "vol_mult": 1.8, "bb_min_width": 0.012,
    },
}
SYMBOLS = list(PARES_CONFIG.keys())   # los 3 pares operan real

# Sesión 24/7 — modo máximo volumen (sin filtros horarios)
# Decisión del usuario: priorizar oportunidades sobre filtros conservadores.
# Trade-off: los backtests con PF 1.40-2.40 fueron CON filtros — en vivo puede variar.
SESSION_START = 0
SESSION_END   = 24
HORAS_MALAS   = set()      # sin horas malas
DIAS_MALOS    = set()      # opera los 7 días
MAX_PERDIDAS_CONSECUTIVAS = 4
PAUSA_MINUTOS = 180
DAILY_LOSS_LIMIT = 0.04   # 4% diario → pausa hasta nuevo día UTC
MAX_POSICIONES_ABIERTAS = 1   # solo una posición a la vez

REFERRAL = "https://partner.bybit.com/b/59453"
YOUTUBE  = "https://www.youtube.com/@EzequielSack/"
TELEGRAM = "https://t.me/EzequielSackTelegram"

# Colores (impronta Ezequiel Sack)
BG     = "#0a0b14"
CARD   = "#12131f"
GOLD   = "#c9844a"
GOLD2  = "#e8a862"
TEXT   = "#f4f0e8"
MUT    = "#8a857d"
GREEN  = "#4caf7d"
RED    = "#e05555"
BORD   = "#2a2620"


# ═══════════════════════════════════════════════════════════
#   MOTOR DEL BOT MULTI-PAR
# ═══════════════════════════════════════════════════════════
class BotEngine:
    def __init__(self, api_key, api_secret, testnet, log_callback):
        self.testnet = testnet
        self.session = HTTP(testnet=testnet, api_key=api_key,
                            api_secret=api_secret, recv_window=15000)
        self.log = log_callback
        self.running = False
        self.thread = None
        self.perdidas_consecutivas = 0
        self.pausa_hasta = None
        self.daily_pause_until = None   # pausa por daily loss limit
        self.capital_dia_inicio = 0
        self.fecha_dia_actual = None
        self.stats = {
            "balance": 0, "pnl": 0, "pos": None, "pos_symbol": None,
            "ciclos": 0, "pares_activos": "BTC + ETH + SOL"
        }
        # Cache HTF trend por símbolo: {sym: (timestamp, trend)}
        # Se invalida cada 5 min (suficiente para EMA 1h que cambia lento)
        self._htf_cache = {}
        self._htf_cache_ttl = 300  # segundos

    # ── Datos ──────────────────────────────────────────────
    def get_klines(self, symbol, interval, limit=200):
        try:
            r = self.session.get_kline(category="linear", symbol=symbol,
                                       interval=interval, limit=limit)
            d = r["result"]["list"]
            df = pd.DataFrame(d, columns=["timestamp","open","high","low","close","volume","turnover"])
            df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
            return df.sort_values("timestamp").reset_index(drop=True)
        except Exception as e:
            self.log(f"⚠ Error datos {symbol}: {e}", "err")
            return pd.DataFrame()

    def _parse_balance(self, r):
        """Extrae el saldo USDT tolerando campos vacíos en cuentas Unified."""
        acc = r["result"]["list"][0]
        for k in ("totalAvailableBalance", "totalEquity"):
            v = acc.get(k, "")
            if v not in ("", None):
                try: return float(v)
                except: pass
        for c in acc.get("coin", []):
            if c.get("coin") == "USDT":
                for k in ("availableToWithdraw", "walletBalance", "equity"):
                    v = c.get(k, "")
                    if v not in ("", None):
                        try: return float(v)
                        except: pass
        return 0.0

    def get_balance(self):
        try:
            r = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            return self._parse_balance(r)
        except Exception as e:
            self.log(f"⚠ Error balance: {e}", "err")
            return 0.0

    def test_conexion(self):
        """Prueba la conexión y devuelve (ok:bool, mensaje_claro:str)."""
        try:
            r = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            bal = self._parse_balance(r)
            self.stats["balance"] = bal
            return True, f"Conectado · Saldo: ${bal:.2f} USDT"
        except Exception as e:
            txt = str(e)
            if "401" in txt or "10003" in txt or "invalid" in txt.lower():
                if self.testnet:
                    return False, (
                        "No se pudo autenticar con Bybit Testnet.\n\n"
                        "Las keys de TESTNET son distintas a las de tu cuenta real.\n"
                        "Tenés que crearlas en testnet.bybit.com → API Management."
                    )
                return False, (
                    "No se pudo autenticar con Bybit.\n\n"
                    "Revisá:\n"
                    "• ¿Las keys son de tu cuenta real en bybit.com?\n"
                    "• ¿Copiaste bien el API Secret? Se muestra una sola vez.\n"
                    "• ¿La API Key tiene restricción de IP? Quitala.\n"
                    "• ¿Tenés VPN activa? Probá desactivándola."
                )
            if "10002" in txt or "timestamp" in txt.lower():
                return False, "Tu reloj está desincronizado.\nSincronizá la hora de Windows."
            if "10004" in txt or "sign" in txt.lower():
                return False, "Error de firma — el API Secret está mal copiado."
            return False, f"Error de conexión:\n{txt}"

    def get_open_position(self, symbol):
        try:
            r = self.session.get_positions(category="linear", symbol=symbol)
            for p in r["result"]["list"]:
                if float(p.get("size", 0)) > 0:
                    return p
            return {}
        except Exception:
            return {}

    def get_any_open_position(self):
        """Busca posición abierta en cualquiera de los 3 pares."""
        for s in SYMBOLS:
            p = self.get_open_position(s)
            if p:
                return s, p
        return None, {}

    # ── Sensores ───────────────────────────────────────────
    def add_sensors(self, df):
        df["bb_mid"]=df["close"].rolling(BB_PERIOD).mean()
        df["bb_std"]=df["close"].rolling(BB_PERIOD).std()
        df["bb_upper"]=df["bb_mid"]+BB_STD*df["bb_std"]
        df["bb_lower"]=df["bb_mid"]-BB_STD*df["bb_std"]
        df["bb_width"]=(df["bb_upper"]-df["bb_lower"])/df["bb_mid"]
        delta=df["close"].diff()
        gain=delta.clip(lower=0).rolling(RSI_PERIOD).mean()
        loss=(-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
        df["rsi"]=100-(100/(1+gain/loss.replace(0,np.nan)))
        df["tr"]=np.maximum(df["high"]-df["low"],np.maximum(abs(df["high"]-df["close"].shift(1)),abs(df["low"]-df["close"].shift(1))))
        df["atr"]=df["tr"].rolling(ATR_PERIOD).mean()
        df["vol_avg"]=df["volume"].rolling(VOL_PERIOD).mean()
        h,l,c=df["high"],df["low"],df["close"]
        pdm=h.diff();mdm=l.diff().abs()
        pdm=pdm.where((pdm>mdm)&(pdm>0),0);mdm=mdm.where((mdm>pdm)&(mdm>0),0)
        tr=np.maximum(h-l,np.maximum(abs(h-c.shift(1)),abs(l-c.shift(1))))
        atr_=pd.Series(tr).rolling(ADX_PERIOD).mean()
        pdi=100*pdm.rolling(ADX_PERIOD).mean()/atr_
        mdi=100*mdm.rolling(ADX_PERIOD).mean()/atr_
        dx=(abs(pdi-mdi)/(pdi+mdi).replace(0,np.nan))*100
        df["adx"]=dx.rolling(ADX_PERIOD).mean()
        return df

    def get_htf_trend(self, symbol):
        # Cache de 5 min — EMA 1h cambia lento, evitamos 60 llamadas/hora innecesarias
        ahora = time.time()
        ent = self._htf_cache.get(symbol)
        if ent and (ahora - ent[0]) < self._htf_cache_ttl:
            return ent[1]
        df = self.get_klines(symbol, TIMEFRAME_HTF, limit=100)
        if df.empty or len(df) < EMA_HTF_PERIOD:
            trend = "NEUTRAL"
        else:
            ema = df["close"].ewm(span=EMA_HTF_PERIOD, adjust=False).mean().iloc[-1]
            p = df["close"].iloc[-1]
            if   p > ema * 1.001: trend = "BULLISH"
            elif p < ema * 0.999: trend = "BEARISH"
            else:                  trend = "NEUTRAL"
        self._htf_cache[symbol] = (ahora, trend)
        return trend

    def get_signal(self, df, htf, symbol):
        """Señal con umbrales específicos del par."""
        cfg = PARES_CONFIG[symbol]
        rsi_os = cfg["rsi_oversold"]
        rsi_ob = cfg["rsi_overbought"]
        vol_mult_par = cfg["vol_mult"]
        bb_min = cfg["bb_min_width"]

        mc=max(BB_PERIOD,RSI_PERIOD,ATR_PERIOD,ADX_PERIOD,VOL_PERIOD)+5
        if df.empty or len(df)<mc: return "NONE", 1.0
        last=df.iloc[-1]; prev=df.iloc[-2]
        p=last["close"]; bl=last["bb_lower"]; bu=last["bb_upper"]
        bw=last["bb_width"]; rsi=last["rsi"]; atr=last["atr"]; adx=last["adx"]
        vol_ok = last["volume"] >= last["vol_avg"] * vol_mult_par

        if pd.isna(bw) or bw < bb_min: return "NONE", 1.0
        if pd.isna(atr) or atr < p*0.001: return "NONE", 1.0
        if not vol_ok: return "NONE", 1.0

        mult = 0.5 if (not pd.isna(adx) and adx > ADX_THRESHOLD) else 1.0

        if prev["close"]>bl and p<=bl and (not pd.isna(rsi)) and rsi<=rsi_os and htf=="BULLISH":
            return "LONG", mult
        if prev["close"]<bu and p>=bu and (not pd.isna(rsi)) and rsi>=rsi_ob and htf=="BEARISH":
            return "SHORT", mult
        return "NONE", 1.0

    def calc_signal_score(self, df, htf, signal, symbol):
        """Score 0-100 para comparar señales de distintos pares y elegir la mejor."""
        if signal == "NONE": return 0
        last = df.iloc[-1]
        rsi = last["rsi"]; adx = last["adx"]; atr = last["atr"]; p = last["close"]
        cfg = PARES_CONFIG[symbol]
        score = 50
        # RSI extremo → +pts
        if signal == "LONG":
            score += max(0, (cfg["rsi_oversold"] - rsi) * 2)
        else:
            score += max(0, (rsi - cfg["rsi_overbought"]) * 2)
        # ADX alto (tendencia fuerte) → +pts
        if not pd.isna(adx) and adx > 25:
            score += min(15, adx - 25)
        # ATR razonable → +pts
        if not pd.isna(atr):
            atr_pct = atr / p
            if 0.001 <= atr_pct <= 0.01:
                score += 10
        # Tendencia confirmada → +pts
        if htf in ("BULLISH", "BEARISH"):
            score += 10
        return min(int(score), 100)

    # ── Sesión / breakers ──────────────────────────────────
    def en_sesion(self):
        n=datetime.now(timezone.utc)
        if n.weekday() in DIAS_MALOS: return False
        if not (SESSION_START<=n.hour<SESSION_END): return False
        if n.hour in HORAS_MALAS: return False
        return True

    def breaker_ok(self):
        if self.pausa_hasta is None: return True
        if datetime.now(timezone.utc)>=self.pausa_hasta:
            self.pausa_hasta=None; self.perdidas_consecutivas=0
            self.log("✅ Pausa terminada — operando normal","ok")
            return True
        return False

    def daily_breaker_ok(self):
        """Pausa hasta nuevo día UTC si se perdió DAILY_LOSS_LIMIT del capital."""
        if self.daily_pause_until is None: return True
        if datetime.now(timezone.utc) >= self.daily_pause_until:
            self.daily_pause_until = None
            self.log("✅ Nuevo día — daily limit reseteado","ok")
            return True
        return False

    def check_daily_loss(self, balance_actual):
        """Si la pérdida del día supera el límite, pausa hasta UTC siguiente."""
        hoy = datetime.now(timezone.utc).date()
        if self.fecha_dia_actual != hoy:
            self.fecha_dia_actual = hoy
            self.capital_dia_inicio = balance_actual
            return
        if self.capital_dia_inicio > 0:
            perdida_pct = (self.capital_dia_inicio - balance_actual) / self.capital_dia_inicio
            if perdida_pct >= DAILY_LOSS_LIMIT:
                manana = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0)
                self.daily_pause_until = manana
                self.log(f"🛑 DAILY STOP: -{perdida_pct*100:.1f}% — pausa hasta nuevo día UTC", "err")

    # ── Órdenes ────────────────────────────────────────────
    def set_leverage(self,symbol):
        try:
            self.session.set_leverage(category="linear",symbol=symbol,
                buyLeverage=str(LEVERAGE),sellLeverage=str(LEVERAGE))
        except: pass

    def calc_qty(self, bal, atr, mult, symbol):
        """Tamaño de posición usando risk_weight y sl_mult específicos del par."""
        cfg = PARES_CONFIG[symbol]
        risk_w = cfg["risk_weight"]
        sl_mult = cfg["sl_mult"]
        r = bal * RISK_PER_TRADE * mult * risk_w
        sd = atr * sl_mult
        return max(round((r*LEVERAGE)/sd, 3), 0.001)

    def place_order(self, symbol, side, qty, precio, atr):
        """Ejecuta orden con SL/TP específicos del par."""
        cfg = PARES_CONFIG[symbol]
        sl_mult = cfg["sl_mult"]
        tp_mult = cfg["tp_mult"]
        sd = atr * sl_mult
        td = atr * tp_mult
        # Redondeo según par
        decimales = 2 if symbol == "BTCUSDT" else (2 if symbol == "ETHUSDT" else 4)
        if side=="Buy":
            sl = round(precio - sd, decimales)
            tp = round(precio + td, decimales)
        else:
            sl = round(precio + sd, decimales)
            tp = round(precio - td, decimales)
        try:
            self.session.place_order(category="linear", symbol=symbol, side=side,
                orderType="Market", qty=str(qty), stopLoss=str(sl), takeProfit=str(tp),
                timeInForce="GoodTillCancel", reduceOnly=False)
            self.log(f"📤 [{symbol}] {side} {qty} @ {precio} | SL {sl} | TP {tp}", "trade")
            return True
        except Exception as e:
            self.log(f"❌ Error orden {symbol}: {e}", "err")
            return False

    # ── Loop ───────────────────────────────────────────────
    def start(self):
        self.running=True
        self.thread=threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running=False
        self.log("⏹ Bot detenido por el usuario","sys")

    def run(self):
        self.log("="*50,"sys")
        self.log("🤖 AUTOBOT v3.0 — Multi-par BTC + ETH + SOL","sys")
        self.log(f"Riesgo base: {RISK_PER_TRADE*100}% | Leverage: {LEVERAGE}x","sys")
        self.log(f"BTC: risk×0.5 SL 1.2x TP 4.2x","sys")
        self.log(f"ETH: risk×0.3 SL 1.5x TP 3.5x","sys")
        self.log(f"SOL: risk×0.2 SL 1.5x TP 2.5x","sys")
        self.log("="*50,"sys")
        for s in SYMBOLS: self.set_leverage(s)

        ciclo=0
        while self.running:
            ciclo+=1
            self.stats["ciclos"]=ciclo
            try:
                # Leer saldo siempre
                try:
                    b=self.get_balance()
                    if b and b>0: self.stats["balance"]=b
                except: pass

                if not self.en_sesion():
                    self.log(f"🕐 Fuera de sesión — saldo: ${self.stats['balance']:.2f}","wait")
                    self._sleep(LOOP_SLEEP); continue
                if not self.breaker_ok():
                    rem=(self.pausa_hasta-datetime.now(timezone.utc)).seconds//60
                    self.log(f"⏸ Circuit breaker — {rem} min restantes","wait")
                    self._sleep(LOOP_SLEEP); continue
                if not self.daily_breaker_ok():
                    self.log("⏸ Daily stop activo hasta nuevo día UTC","wait")
                    self._sleep(LOOP_SLEEP); continue

                bal = self.get_balance(); self.stats["balance"]=bal
                if bal < 10:
                    self.log("⚠ Balance insuficiente","err")
                    self._sleep(LOOP_SLEEP*5); continue

                self.check_daily_loss(bal)

                # ¿Hay posición abierta en algún par?
                pos_sym, pos = self.get_any_open_position()
                if pos:
                    pnl = float(pos.get("unrealisedPnl",0))
                    self.stats["pos"]=pos
                    self.stats["pos_symbol"]=pos_sym
                    self.stats["pnl"]=pnl
                    self.log(f"⏸ Posición abierta: {pos_sym} | PnL: ${pnl:.4f}","pos")
                    self._sleep(LOOP_SLEEP); continue

                self.stats["pos"]=None
                self.stats["pos_symbol"]=None
                self.stats["pnl"]=0

                # Escanear los 3 pares en PARALELO (3-4x más rápido)
                from concurrent.futures import ThreadPoolExecutor

                def _eval_par(sym):
                    """Evalúa un par. Devuelve dict si pasa filtros, None si no."""
                    htf = self.get_htf_trend(sym)
                    if htf == "NEUTRAL":
                        return {"sym": sym, "skip": "tendencia 1h neutral"}
                    df = self.get_klines(sym, TIMEFRAME, limit=200)
                    if df.empty:
                        return None
                    df = self.add_sensors(df)
                    sig, mult = self.get_signal(df, htf, sym)
                    score = self.calc_signal_score(df, htf, sig, sym)
                    return {"sym": sym, "sig": sig, "mult": mult,
                            "df": df, "score": score, "htf": htf}

                with ThreadPoolExecutor(max_workers=3) as ex:
                    resultados = list(ex.map(_eval_par, SYMBOLS))

                candidatos = []
                for r in resultados:
                    if r is None: continue
                    if "skip" in r:
                        self.log(f"🧭 [{r['sym']}] {r['skip']}", "scan")
                        continue
                    sym = r["sym"]
                    min_sc = PARES_CONFIG[sym]["min_score"]
                    if r["sig"] != "NONE" and r["score"] >= min_sc:
                        self.log(f"✅ [{sym}] {r['sig']} | score {r['score']} (min {min_sc})", "signal")
                        candidatos.append(r)
                    else:
                        razon = "sin señal" if r["sig"] == "NONE" else f"score {r['score']} < {min_sc}"
                        self.log(f"🔍 [{sym}] {razon}", "scan")

                if not candidatos:
                    self.log(f"⏱ Ciclo {ciclo} — sin candidatos","scan")
                    self._sleep(LOOP_SLEEP); continue

                # Elegir el de mayor score (desempate por prioridad)
                mejor = sorted(candidatos,
                    key=lambda c: (-c["score"], PARES_CONFIG[c["sym"]]["priority"]))[0]
                sym = mejor["sym"]; sig = mejor["sig"]
                last = mejor["df"].iloc[-1]
                precio = float(last["close"])
                atr = float(last["atr"])
                qty = self.calc_qty(bal, atr, mejor["mult"], sym)
                side = "Buy" if sig=="LONG" else "Sell"
                self.log(f"🎯 ENTRA: [{sym}] {sig} @ {precio} | score {mejor['score']}","signal")
                self.place_order(sym, side, qty, precio, atr)

                self._sleep(LOOP_SLEEP)
            except Exception as e:
                self.log(f"⚠ Error en ciclo: {e}","err")
                self._sleep(LOOP_SLEEP)

    def _sleep(self,secs):
        for _ in range(secs):
            if not self.running: break
            time.sleep(1)


# ═══════════════════════════════════════════════════════════
#   INTERFAZ GRÁFICA
# ═══════════════════════════════════════════════════════════
class AutobotApp:
    def __init__(self, root):
        self.root=root
        self.engine=None
        self.log_queue=queue.Queue()
        self.setup_ui()
        self.poll_logs()
        self.poll_stats()

    def setup_ui(self):
        self.root.title("AUTOBOT v3.0 · por Ezequiel Sack")
        self.root.configure(bg=BG)
        self.root.geometry("820x720")
        self.root.minsize(720, 620)

        # ── Header ──────────────────────────────────────────
        header=tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(20,8))
        logo=tk.Label(header, text="AUTOBOT", font=("Segoe UI",20,"bold"), bg=BG, fg=TEXT)
        logo.pack(side="left")
        tk.Label(header, text="  v3.0 · por Ezequiel Sack",
                 font=("Segoe UI",10), bg=BG, fg=MUT).pack(side="left", pady=(8,0))
        self.status_lbl=tk.Label(header, text="● Detenido", font=("Segoe UI",10,"bold"), bg=BG, fg=MUT)
        self.status_lbl.pack(side="right", pady=(6,0))

        tk.Label(self.root,
                 text="⚗ Experimental · Multi-par BTC+ETH+SOL · No es asesoramiento financiero",
                 font=("Segoe UI",8), bg=BG, fg=MUT).pack(anchor="w", padx=24)

        # ── Card de conexión ────────────────────────────────
        card=tk.Frame(self.root, bg=CARD, highlightbackground=BORD, highlightthickness=1)
        card.pack(fill="x", padx=24, pady=14)

        self.gate_done = self._cargar_gate()
        if not self.gate_done:
            tk.Label(card, text="PASO 1 — CREÁ TU CUENTA EN BYBIT",
                     font=("Segoe UI",9,"bold"), bg=CARD, fg=GOLD).pack(anchor="w", padx=20, pady=(18,4))
            tk.Label(card, text="Para usar AUTOBOT necesitás una cuenta en Bybit con el link de\nEzequiel Sack. Es gratis y desbloquea el bot.",
                     font=("Segoe UI",9), bg=CARD, fg=MUT, justify="left").pack(anchor="w", padx=20)
            self._boton(card, "🚀  Crear mi cuenta en Bybit",
                        self.abrir_registro, primary=True).pack(fill="x", padx=20, pady=(12,6))
            self.chk_var=tk.IntVar()
            chk=tk.Checkbutton(card, text="Ya creé mi cuenta en Bybit con el link",
                variable=self.chk_var, command=self.toggle_continuar,
                bg=CARD, fg=MUT, selectcolor=CARD, activebackground=CARD,
                activeforeground=TEXT, font=("Segoe UI",9), bd=0, highlightthickness=0)
            chk.pack(anchor="w", padx=18, pady=(4,4))
            self.btn_cont=self._boton(card, "Continuar al paso 2  →",
                                       self.pasar_a_api, primary=True)
            self.btn_cont.pack(fill="x", padx=20, pady=(2,18))
            self.btn_cont.config(state="disabled")
            self.card_form=None
            self._card_ref=card
        else:
            self._construir_form(card)

    def _construir_form(self, card):
        for w in card.winfo_children(): w.destroy()
        tk.Label(card, text="CONECTÁ TU CUENTA · MULTI-PAR (BTC + ETH + SOL)",
                 font=("Segoe UI",9,"bold"), bg=CARD, fg=GOLD).pack(anchor="w", padx=20, pady=(18,2))
        tk.Label(card, text="Tus claves se usan solo en esta app. Ezequiel Sack NO tiene acceso a tu dinero.",
                 font=("Segoe UI",8), bg=CARD, fg=MUT).pack(anchor="w", padx=20, pady=(0,10))

        self.key_entry=self._input(card,"API KEY")
        self.secret_entry=self._input(card,"API SECRET", show="•")

        self.testnet_var=tk.IntVar()
        tk.Checkbutton(card, text="Cuenta de prueba (Testnet)",
            variable=self.testnet_var,
            bg=CARD, fg=MUT, selectcolor=CARD, activebackground=CARD,
            activeforeground=TEXT, font=("Segoe UI",9), bd=0, highlightthickness=0
        ).pack(anchor="w", padx=18, pady=(2,8))

        btnrow=tk.Frame(card, bg=CARD); btnrow.pack(fill="x", padx=20, pady=(2,18))
        self.btn_start=self._boton(btnrow, "▶  Iniciar bot multi-par",
                                    self.iniciar, primary=True)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0,4))
        self.btn_stop=self._boton(btnrow, "⏹  Detener", self.detener, primary=False)
        self.btn_stop.pack(side="left", fill="x", expand=True, padx=(4,0))
        self.btn_stop.config(state="disabled")

    def _input(self, parent, label, show=""):
        tk.Label(parent, text=label, font=("Segoe UI",8,"bold"),
                 bg=CARD, fg=MUT).pack(anchor="w", padx=20, pady=(4,2))
        e=tk.Entry(parent, font=("Segoe UI",11), bg="#1a1b28", fg=TEXT,
                   insertbackground=GOLD, relief="flat", show=show)
        e.pack(fill="x", padx=20, ipady=7)
        return e

    def _boton(self, parent, text, cmd, primary=True):
        bg=GOLD if primary else "#1a1b28"
        fg="#0a0b14" if primary else TEXT
        return tk.Button(parent, text=text, command=cmd,
                font=("Segoe UI",11,"bold"), bg=bg, fg=fg,
                relief="flat", cursor="hand2", bd=0,
                activebackground=GOLD2, pady=10)

    # ── Stats bar (5 cards: saldo, PnL, posición, par, ciclos) ──
    def _build_stats(self):
        if hasattr(self,"stats_frame"): return
        self.stats_frame=tk.Frame(self.root, bg=BG)
        self.stats_frame.pack(fill="x", padx=24, pady=(0,8))
        self.stat_bal=self._stat_box("SALDO", "—")
        self.stat_pnl=self._stat_box("PnL ABIERTO", "—")
        self.stat_pos=self._stat_box("POSICIÓN", "Ninguna")
        self.stat_par=self._stat_box("PARES", "BTC+ETH+SOL")
        self.stat_cic=self._stat_box("CICLOS", "0")

    def _stat_box(self, label, val):
        f=tk.Frame(self.stats_frame, bg=CARD,
                   highlightbackground=BORD, highlightthickness=1)
        f.pack(side="left", fill="x", expand=True, padx=3)
        tk.Label(f, text=label, font=("Segoe UI",7,"bold"),
                 bg=CARD, fg=MUT).pack(anchor="w", padx=12, pady=(8,0))
        v=tk.Label(f, text=val, font=("Segoe UI",13,"bold"), bg=CARD, fg=TEXT)
        v.pack(anchor="w", padx=12, pady=(0,8))
        return v

    # ── Consola ────────────────────────────────────────────
    def _build_console(self):
        if hasattr(self,"console"): return
        cont=tk.Frame(self.root, bg=CARD,
                      highlightbackground=BORD, highlightthickness=1)
        cont.pack(fill="both", expand=True, padx=24, pady=(0,14))
        tk.Label(cont, text="ACTIVIDAD DEL BOT (escaneando BTC + ETH + SOL)",
                 font=("Segoe UI",8,"bold"), bg=CARD, fg=MUT).pack(anchor="w", padx=14, pady=(10,4))
        self.console=tk.Text(cont, bg="#0d0e18", fg=TEXT, font=("Consolas",9),
                             relief="flat", height=14, wrap="word", bd=0)
        self.console.pack(fill="both", expand=True, padx=12, pady=(0,12))
        for tag,col in [("err",RED),("ok",GREEN),("trade",GOLD2),("signal",GOLD),
                        ("pos","#7da6ff"),("wait",MUT),("scan","#6a8caf"),("sys",GOLD)]:
            self.console.tag_config(tag,foreground=col)

    # ── Gate registro ───────────────────────────────────────
    def abrir_registro(self):
        import webbrowser
        webbrowser.open(REFERRAL)

    def toggle_continuar(self):
        if self.chk_var.get():
            self.btn_cont.config(state="normal")
        else:
            self.btn_cont.config(state="disabled")

    def pasar_a_api(self):
        self._guardar_gate()
        self._construir_form(self._card_ref)

    def _gate_path(self):
        base=os.path.join(os.path.expanduser("~"),".autobot")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base,"gate.txt")

    def _cargar_gate(self):
        try: return os.path.exists(self._gate_path())
        except: return False

    def _guardar_gate(self):
        try:
            with open(self._gate_path(),"w") as f: f.write("1")
        except: pass

    # ── Acciones bot ───────────────────────────────────────
    def iniciar(self):
        if HTTP is None:
            messagebox.showerror("Error","Falta instalar pybit. Contactá a Ezequiel Sack.")
            return
        key=self.key_entry.get().strip()
        secret=self.secret_entry.get().strip()
        if not key or not secret:
            messagebox.showwarning("Faltan datos","Completá API Key y API Secret.")
            return
        testnet=bool(self.testnet_var.get())

        self._build_stats()
        self._build_console()

        try:
            self.engine=BotEngine(key, secret, testnet, self.log_threadsafe)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo iniciar:\n{e}")
            return

        self.btn_start.config(state="disabled", text="Conectando...")
        self.status_lbl.config(text="● Conectando...", fg=GOLD)
        self.root.update_idletasks()

        try:
            ok, msg = self.engine.test_conexion()
        except Exception as e:
            ok, msg = False, f"Error inesperado: {e}"

        if not ok:
            self.engine = None
            self.btn_start.config(state="normal", text="▶  Iniciar bot multi-par")
            self.status_lbl.config(text="● Detenido", fg=MUT)
            messagebox.showerror("No se pudo conectar", msg)
            return

        # Conexión OK: marcar Activo
        self.status_lbl.config(text="● Activo", fg=GREEN)
        self.btn_start.config(state="disabled", text="▶  Iniciar bot multi-par")
        self.btn_stop.config(state="normal")

        bal_ini = self.engine.stats.get("balance", 0)
        try: self.stat_bal.config(text=f"${bal_ini:,.2f}")
        except: pass

        self.root.update_idletasks()
        self.log(f"✅ {msg}", "ok")
        self.log("🟢 Cuenta conectada. Escaneando BTC + ETH + SOL.", "ok")

        try:
            self.engine.start()
        except Exception as e:
            self.log(f"⚠ {e}", "err")

    def detener(self):
        if self.engine: self.engine.stop()
        self.btn_start.config(state="normal", text="▶  Iniciar bot multi-par")
        self.btn_stop.config(state="disabled")
        self.status_lbl.config(text="● Detenido", fg=MUT)

    # ── Logging thread-safe ────────────────────────────────
    def log_threadsafe(self, msg, tag="sys"):
        self.log_queue.put((msg,tag))

    def poll_logs(self):
        try:
            while True:
                msg,tag = self.log_queue.get_nowait()
                if hasattr(self,"console"):
                    ts=datetime.now().strftime("%H:%M:%S")
                    self.console.insert("end", f"[{ts}] {msg}\n", tag)
                    self.console.see("end")
                    lines=int(self.console.index("end-1c").split(".")[0])
                    if lines>500: self.console.delete("1.0","100.0")
        except queue.Empty:
            pass
        self.root.after(300, self.poll_logs)

    def log(self, msg, tag="sys"):
        self.log_threadsafe(msg, tag)

    def poll_stats(self):
        if self.engine and hasattr(self,"stat_bal"):
            s=self.engine.stats
            self.stat_bal.config(text=f"${s['balance']:,.2f}")
            pnl=s["pnl"]
            self.stat_pnl.config(text=f"{'+' if pnl>=0 else ''}${pnl:.4f}",
                                 fg=GREEN if pnl>=0 else RED)
            if s.get("pos"):
                side_txt = "LONG 🟢" if s["pos"].get("side")=="Buy" else "SHORT 🔴"
                sym_short = (s.get("pos_symbol") or "").replace("USDT","")
                self.stat_pos.config(text=f"{sym_short} {side_txt}")
            else:
                self.stat_pos.config(text="Ninguna")
            self.stat_cic.config(text=str(s["ciclos"]))
        self.root.after(1000, self.poll_stats)


def main():
    root=tk.Tk()
    try:
        if hasattr(sys,"_MEIPASS"):
            ico=os.path.join(sys._MEIPASS,"autobot.ico")
            if os.path.exists(ico): root.iconbitmap(ico)
    except: pass
    AutobotApp(root)
    root.mainloop()


if __name__=="__main__":
    main()
