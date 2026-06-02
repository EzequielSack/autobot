"""
╔══════════════════════════════════════════════════════════╗
║   AUTOBOT — App de Escritorio (.exe)                     ║
║   Por Ezequiel Sack — Proyecto experimental              ║
║                                                          ║
║   El usuario abre el .exe, pega sus API Keys y arranca.  ║
║   El bot corre adentro de esta ventana.                  ║
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
#   CONFIGURACIÓN (igual al bot.py validado)
# ═══════════════════════════════════════════════════════════
SYMBOLS        = ["BTCUSDT"]
LEVERAGE       = 3
RISK_PER_TRADE = 0.005
TIMEFRAME      = "5"
TIMEFRAME_HTF  = "60"
LOOP_SLEEP     = 60

BB_PERIOD = 20
BB_STD    = 2.0
RSI_PERIOD     = 14
RSI_OVERSOLD   = 30
RSI_OVERBOUGHT = 70
ATR_PERIOD  = 14
ATR_SL_MULT = 1.2
ATR_TP_MULT = 4.2
EMA_HTF_PERIOD = 50
VOL_PERIOD = 20
VOL_MULT   = 1.5
ADX_PERIOD    = 14
ADX_THRESHOLD = 25

SESSION_START = 8
SESSION_END   = 22
HORAS_MALAS   = {10, 13, 19, 21}
DIAS_MALOS    = {4, 5}
MAX_PERDIDAS_CONSECUTIVAS = 4
PAUSA_MINUTOS = 180

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
#   MOTOR DEL BOT (corre en thread separado)
# ═══════════════════════════════════════════════════════════
class BotEngine:
    def __init__(self, api_key, api_secret, testnet, log_callback):
        # recv_window amplio para tolerar pequeñas diferencias de reloj
        self.testnet = testnet
        self.session = HTTP(testnet=testnet, api_key=api_key,
                            api_secret=api_secret, recv_window=15000)
        self.log = log_callback
        self.running = False
        self.thread = None
        self.perdidas_consecutivas = 0
        self.pausa_hasta = None
        self.stats = {"balance": 0, "pnl": 0, "pos": None, "ciclos": 0}

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
        # 1) Intentar disponible total de la cuenta
        for k in ("totalAvailableBalance", "totalEquity"):
            v = acc.get(k, "")
            if v not in ("", None):
                try: return float(v)
                except: pass
        # 2) Buscar USDT en las monedas
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
            self.stats["balance"] = bal   # guardar para mostrar al instante
            return True, f"Conectado · Saldo: ${bal:.2f} USDT"
        except Exception as e:
            txt = str(e)
            if "401" in txt or "10003" in txt or "invalid" in txt.lower():
                if self.testnet:
                    return False, (
                        "No se pudo autenticar con Bybit Testnet.\n\n"
                        "Las keys de TESTNET son distintas a las de tu cuenta real.\n"
                        "Tenés que crearlas en:\n"
                        "    testnet.bybit.com → Perfil → API Management\n\n"
                        "Pasos:\n"
                        "1. Entrá a testnet.bybit.com (no bybit.com)\n"
                        "2. Creá una cuenta o iniciá sesión\n"
                        "3. Generá una nueva API Key con permiso\n"
                        "   'Unified Trading · Read + Write'\n"
                        "4. Copiá Key y Secret, y pegálos acá\n\n"
                        "• ¿El API Secret está bien copiado? Se muestra una sola vez.\n"
                        "• ¿Tiene restricción de IP? Quitala."
                    )
                else:
                    return False, (
                        "No se pudo autenticar con Bybit.\n\n"
                        "Revisá esto:\n"
                        "• ¿Las keys son de tu cuenta real en bybit.com?\n"
                        "  (Si querés usar testnet, tildá 'Cuenta de prueba'\n"
                        "   y usá keys de testnet.bybit.com)\n"
                        "• ¿Copiaste bien el API Secret? Se muestra una sola vez.\n"
                        "• ¿La API Key tiene restricción de IP? Quitala.\n"
                        "• ¿Tenés VPN activa? Probá desactivándola.\n\n"
                        "Para crear una key nueva: bybit.com → Perfil\n"
                        "→ API Management → permisos 'Unified Trading · Read+Write'."
                    )
            if "10002" in txt or "timestamp" in txt.lower():
                return False, ("Tu reloj está desincronizado.\n"
                    "Sincronizá la hora de Windows: Configuración → Hora e idioma → "
                    "Sincronizar ahora.")
            if "10004" in txt or "sign" in txt.lower():
                return False, "Error de firma — el API Secret está incompleto o mal copiado. Volvé a copiarlo."
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
        df["vol_ok"]=df["volume"]>=df["vol_avg"]*VOL_MULT
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
        df=self.get_klines(symbol,TIMEFRAME_HTF,limit=100)
        if df.empty or len(df)<EMA_HTF_PERIOD: return "NEUTRAL"
        ema=df["close"].ewm(span=EMA_HTF_PERIOD,adjust=False).mean().iloc[-1]
        p=df["close"].iloc[-1]
        if p>ema*1.001: return "BULLISH"
        if p<ema*0.999: return "BEARISH"
        return "NEUTRAL"

    def get_signal(self, df, htf):
        mc=max(BB_PERIOD,RSI_PERIOD,ATR_PERIOD,ADX_PERIOD,VOL_PERIOD)+5
        if df.empty or len(df)<mc: return "NONE",1.0
        last=df.iloc[-1];prev=df.iloc[-2]
        p=last["close"];bl=last["bb_lower"];bu=last["bb_upper"]
        bw=last["bb_width"];rsi=last["rsi"];atr=last["atr"];vok=last["vol_ok"];adx=last["adx"]
        if bw<0.01 or atr<p*0.001 or not vok: return "NONE",1.0
        mult=0.5 if adx>ADX_THRESHOLD else 1.0
        if prev["close"]>bl and p<=bl and rsi<=RSI_OVERSOLD and htf=="BULLISH":
            return "LONG",mult
        if prev["close"]<bu and p>=bu and rsi>=RSI_OVERBOUGHT and htf=="BEARISH":
            return "SHORT",mult
        return "NONE",1.0

    # ── Sesión / breaker ───────────────────────────────────
    def en_sesion(self):
        n=datetime.now(timezone.utc)
        if n.weekday() in DIAS_MALOS: return False
        if not (SESSION_START<=n.hour<SESSION_END): return False
        if n.hour in HORAS_MALAS: return False
        return True

    def breaker_ok(self):
        if self.pausa_hasta is None: return True
        if datetime.now(timezone.utc)>=self.pausa_hasta:
            self.pausa_hasta=None;self.perdidas_consecutivas=0
            self.log("✅ Pausa terminada — operando normal","ok")
            return True
        return False

    # ── Órdenes ────────────────────────────────────────────
    def set_leverage(self,symbol):
        try:
            self.session.set_leverage(category="linear",symbol=symbol,
                buyLeverage=str(LEVERAGE),sellLeverage=str(LEVERAGE))
        except: pass

    def calc_qty(self,bal,atr,mult):
        r=bal*RISK_PER_TRADE*mult;sd=atr*ATR_SL_MULT
        return max(round((r*LEVERAGE)/sd,3),0.001)

    def place_order(self,symbol,side,qty,precio,atr):
        sd=atr*ATR_SL_MULT;td=atr*ATR_TP_MULT
        if side=="Buy": sl=round(precio-sd,2);tp=round(precio+td,2)
        else: sl=round(precio+sd,2);tp=round(precio-td,2)
        try:
            self.session.place_order(category="linear",symbol=symbol,side=side,
                orderType="Market",qty=str(qty),stopLoss=str(sl),takeProfit=str(tp),
                timeInForce="GoodTillCancel",reduceOnly=False)
            self.log(f"📤 {side} {qty} {symbol} @ {precio:.2f} | SL {sl} | TP {tp}","trade")
            return True
        except Exception as e:
            self.log(f"❌ Error orden: {e}","err")
            return False

    # ── Loop ───────────────────────────────────────────────
    def start(self):
        self.running=True
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()

    def stop(self):
        self.running=False
        self.log("⏹ Bot detenido por el usuario","sys")

    def run(self):
        self.log("="*50,"sys")
        self.log("🤖 AUTOBOT iniciado — por Ezequiel Sack","sys")
        self.log(f"Par: BTCUSDT | Riesgo: {RISK_PER_TRADE*100}% | Leverage: {LEVERAGE}x","sys")
        self.log("="*50,"sys")
        for s in SYMBOLS: self.set_leverage(s)

        ciclo=0
        while self.running:
            ciclo+=1;self.stats["ciclos"]=ciclo
            try:
                # Leer saldo siempre, aunque no se opere (sin pisar con 0 si falla)
                try:
                    b=self.get_balance()
                    if b and b>0: self.stats["balance"]=b
                except: pass
                if not self.en_sesion():
                    self.log(f"🕐 Fuera de sesión / día de descanso — saldo: ${self.stats['balance']:.2f} (esperando horario)","wait")
                    self._sleep(LOOP_SLEEP);continue
                if not self.breaker_ok():
                    rem=(self.pausa_hasta-datetime.now(timezone.utc)).seconds//60
                    self.log(f"⏸ Circuit breaker activo — {rem} min restantes","wait")
                    self._sleep(LOOP_SLEEP);continue

                bal=self.get_balance();self.stats["balance"]=bal
                if bal<10:
                    self.log("⚠ Balance insuficiente","err");self._sleep(LOOP_SLEEP*5);continue

                for symbol in SYMBOLS:
                    pos=self.get_open_position(symbol)
                    if pos:
                        pnl=float(pos.get("unrealisedPnl",0))
                        self.stats["pos"]=pos;self.stats["pnl"]=pnl
                        self.log(f"⏸ Posición abierta {symbol} | PnL: ${pnl:.4f}","pos")
                        continue
                    self.stats["pos"]=None;self.stats["pnl"]=0
                    htf=self.get_htf_trend(symbol)
                    if htf=="NEUTRAL":
                        self.log(f"🧭 {symbol} tendencia neutral — esperando","wait");continue
                    df=self.get_klines(symbol,TIMEFRAME,limit=200)
                    if df.empty: continue
                    df=self.add_sensors(df)
                    sig,mult=self.get_signal(df,htf)
                    if sig=="NONE":
                        self.log(f"🔍 {symbol} sin señal (tendencia {htf})","scan");continue
                    last=df.iloc[-1];precio=last["close"];atr=last["atr"]
                    qty=self.calc_qty(bal,atr,mult)
                    side="Buy" if sig=="LONG" else "Sell"
                    self.log(f"🎯 SEÑAL {sig} {symbol} @ {precio:.2f}","signal")
                    self.place_order(symbol,side,qty,precio,atr)

                self.log(f"⏱ Ciclo {ciclo} completo — próximo en {LOOP_SLEEP}s","scan")
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
        self.conn_queue=queue.Queue()
        self.setup_ui()
        self.poll_logs()
        self.poll_stats()

    def setup_ui(self):
        self.root.title("AUTOBOT · por Ezequiel Sack")
        self.root.configure(bg=BG)
        self.root.geometry("780x680")
        self.root.minsize(680, 600)

        # ── Header ──────────────────────────────────────────
        header=tk.Frame(self.root,bg=BG)
        header.pack(fill="x",padx=24,pady=(20,8))
        logo=tk.Label(header,text="AUTOBOT",font=("Segoe UI",20,"bold"),bg=BG,fg=TEXT)
        logo.pack(side="left")
        tk.Label(header,text="  por Ezequiel Sack",font=("Segoe UI",10),bg=BG,fg=MUT).pack(side="left",pady=(8,0))
        self.status_lbl=tk.Label(header,text="● Detenido",font=("Segoe UI",10,"bold"),bg=BG,fg=MUT)
        self.status_lbl.pack(side="right",pady=(6,0))

        tk.Label(self.root,text="⚗ Proyecto experimental y educativo · No es asesoramiento financiero",
                 font=("Segoe UI",8),bg=BG,fg=MUT).pack(anchor="w",padx=24)

        # ── Card de conexión ────────────────────────────────
        card=tk.Frame(self.root,bg=CARD,highlightbackground=BORD,highlightthickness=1)
        card.pack(fill="x",padx=24,pady=14)

        self.gate_done = self._cargar_gate()

        if not self.gate_done:
            # PASO 1: registro
            tk.Label(card,text="PASO 1 — CREÁ TU CUENTA EN BYBIT",font=("Segoe UI",9,"bold"),
                     bg=CARD,fg=GOLD).pack(anchor="w",padx=20,pady=(18,4))
            tk.Label(card,text="Para usar AUTOBOT necesitás una cuenta en Bybit creada con\nel link oficial de Ezequiel Sack. Es gratis y desbloquea el bot.",
                     font=("Segoe UI",9),bg=CARD,fg=MUT,justify="left").pack(anchor="w",padx=20)
            self._boton(card,"🚀  Crear mi cuenta en Bybit",self.abrir_registro,primary=True).pack(fill="x",padx=20,pady=(12,6))
            self.chk_var=tk.IntVar()
            chk=tk.Checkbutton(card,text="Ya creé mi cuenta en Bybit con el link",variable=self.chk_var,
                command=self.toggle_continuar,bg=CARD,fg=MUT,selectcolor=CARD,activebackground=CARD,
                activeforeground=TEXT,font=("Segoe UI",9),bd=0,highlightthickness=0)
            chk.pack(anchor="w",padx=18,pady=(4,4))
            self.btn_cont=self._boton(card,"Continuar al paso 2  →",self.pasar_a_api,primary=True)
            self.btn_cont.pack(fill="x",padx=20,pady=(2,18))
            self.btn_cont.config(state="disabled")
            self.card_form=None
            self._card_ref=card
        else:
            self._construir_form(card)

    def _construir_form(self, card):
        for w in card.winfo_children(): w.destroy()
        tk.Label(card,text="CONECTÁ TU CUENTA",font=("Segoe UI",9,"bold"),
                 bg=CARD,fg=GOLD).pack(anchor="w",padx=20,pady=(18,2))
        tk.Label(card,text="Tus claves se usan solo en esta app. Ezequiel Sack NO tiene acceso a tu dinero.",
                 font=("Segoe UI",8),bg=CARD,fg=MUT).pack(anchor="w",padx=20,pady=(0,10))

        self.key_entry=self._input(card,"API KEY")
        self.secret_entry=self._input(card,"API SECRET",show="•")

        self.testnet_var=tk.IntVar()
        tk.Checkbutton(card,text="Cuenta de prueba (Testnet)",variable=self.testnet_var,
            bg=CARD,fg=MUT,selectcolor=CARD,activebackground=CARD,activeforeground=TEXT,
            font=("Segoe UI",9),bd=0,highlightthickness=0).pack(anchor="w",padx=18,pady=(2,8))

        btnrow=tk.Frame(card,bg=CARD);btnrow.pack(fill="x",padx=20,pady=(2,18))
        self.btn_start=self._boton(btnrow,"▶  Iniciar bot",self.iniciar,primary=True)
        self.btn_start.pack(side="left",fill="x",expand=True,padx=(0,4))
        self.btn_stop=self._boton(btnrow,"⏹  Detener",self.detener,primary=False)
        self.btn_stop.pack(side="left",fill="x",expand=True,padx=(4,0))
        self.btn_stop.config(state="disabled")

    def _input(self,parent,label,show=""):
        tk.Label(parent,text=label,font=("Segoe UI",8,"bold"),bg=CARD,fg=MUT).pack(anchor="w",padx=20,pady=(4,2))
        e=tk.Entry(parent,font=("Segoe UI",11),bg="#1a1b28",fg=TEXT,insertbackground=GOLD,
                   relief="flat",show=show)
        e.pack(fill="x",padx=20,ipady=7)
        return e

    def _boton(self,parent,text,cmd,primary=True):
        bg=GOLD if primary else "#1a1b28"
        fg="#0a0b14" if primary else TEXT
        b=tk.Button(parent,text=text,command=cmd,font=("Segoe UI",11,"bold"),
                    bg=bg,fg=fg,relief="flat",cursor="hand2",bd=0,activebackground=GOLD2,pady=10)
        return b

    # ── Stats bar ───────────────────────────────────────────
    def _build_stats(self):
        if hasattr(self,"stats_frame"): return
        self.stats_frame=tk.Frame(self.root,bg=BG)
        self.stats_frame.pack(fill="x",padx=24,pady=(0,8))
        self.stat_bal=self._stat_box("SALDO","—")
        self.stat_pnl=self._stat_box("PnL ABIERTO","—")
        self.stat_pos=self._stat_box("POSICIÓN","Ninguna")
        self.stat_cic=self._stat_box("CICLOS","0")

    def _stat_box(self,label,val):
        f=tk.Frame(self.stats_frame,bg=CARD,highlightbackground=BORD,highlightthickness=1)
        f.pack(side="left",fill="x",expand=True,padx=3)
        tk.Label(f,text=label,font=("Segoe UI",7,"bold"),bg=CARD,fg=MUT).pack(anchor="w",padx=12,pady=(8,0))
        v=tk.Label(f,text=val,font=("Segoe UI",14,"bold"),bg=CARD,fg=TEXT)
        v.pack(anchor="w",padx=12,pady=(0,8))
        return v

    # ── Log / consola ───────────────────────────────────────
    def _build_console(self):
        if hasattr(self,"console"): return
        cont=tk.Frame(self.root,bg=CARD,highlightbackground=BORD,highlightthickness=1)
        cont.pack(fill="both",expand=True,padx=24,pady=(0,14))
        tk.Label(cont,text="ACTIVIDAD DEL BOT",font=("Segoe UI",8,"bold"),
                 bg=CARD,fg=MUT).pack(anchor="w",padx=14,pady=(10,4))
        self.console=tk.Text(cont,bg="#0d0e18",fg=TEXT,font=("Consolas",9),
                             relief="flat",height=12,wrap="word",bd=0)
        self.console.pack(fill="both",expand=True,padx=12,pady=(0,12))
        for tag,col in [("err",RED),("ok",GREEN),("trade",GOLD2),("signal",GOLD),
                        ("pos","#7da6ff"),("wait",MUT),("scan","#6a8caf"),("sys",GOLD)]:
            self.console.tag_config(tag,foreground=col)

    # ── Acciones gate ───────────────────────────────────────
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
        os.makedirs(base,exist_ok=True)
        return os.path.join(base,"gate.txt")

    def _cargar_gate(self):
        try: return os.path.exists(self._gate_path())
        except: return False

    def _guardar_gate(self):
        try:
            with open(self._gate_path(),"w") as f: f.write("1")
        except: pass

    # ── Acciones bot ────────────────────────────────────────
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
            self.engine=BotEngine(key,secret,testnet,self.log_threadsafe)
        except Exception as e:
            messagebox.showerror("Error",f"No se pudo iniciar:\n{e}")
            return

        # Feedback inmediato
        self.btn_start.config(state="disabled", text="Conectando...")
        self.status_lbl.config(text="● Conectando...", fg=GOLD)
        self.root.update_idletasks()

        # Test sincrónico (probado: funciona en el .exe)
        try:
            ok, msg = self.engine.test_conexion()
        except Exception as e:
            ok, msg = False, f"Error inesperado: {e}"

        if not ok:
            self.engine = None
            self.btn_start.config(state="normal", text="▶  Iniciar bot")
            self.status_lbl.config(text="● Detenido", fg=MUT)
            messagebox.showerror("No se pudo conectar", msg)
            return

        # ── Conexión OK: marcar ACTIVO de inmediato ──
        self.status_lbl.config(text="● Activo", fg=GREEN)
        self.btn_start.config(state="disabled", text="▶  Iniciar bot")
        self.btn_stop.config(state="normal")

        # Mostrar el saldo directamente en el cuadro (no depende de nada más)
        bal_ini = self.engine.stats.get("balance", 0)
        try:
            self.stat_bal.config(text=f"${bal_ini:,.2f}")
        except: pass

        self.root.update_idletasks()

        self.log(f"✅ {msg}", "ok")
        self.log("🟢 Cuenta conectada. El bot está activo y vigilando el mercado.", "ok")

        # Arrancar el motor (si algo falla acá, el estado ya quedó Activo)
        try:
            self.engine.start()
        except Exception as e:
            self.log(f"⚠ {e}", "err")

    def detener(self):
        if self.engine: self.engine.stop()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.status_lbl.config(text="● Detenido",fg=MUT)

    # ── Logging thread-safe ─────────────────────────────────
    def log_threadsafe(self,msg,tag="sys"):
        self.log_queue.put((msg,tag))

    def poll_logs(self):
        try:
            while True:
                msg,tag=self.log_queue.get_nowait()
                if hasattr(self,"console"):
                    ts=datetime.now().strftime("%H:%M:%S")
                    self.console.insert("end",f"[{ts}] {msg}\n",tag)
                    self.console.see("end")
                    lines=int(self.console.index("end-1c").split(".")[0])
                    if lines>500: self.console.delete("1.0","100.0")
        except queue.Empty:
            pass
        self.root.after(300,self.poll_logs)

    def poll_stats(self):
        if self.engine and hasattr(self,"stat_bal"):
            s=self.engine.stats
            self.stat_bal.config(text=f"${s['balance']:.2f}")
            pnl=s["pnl"]
            self.stat_pnl.config(text=f"{'+' if pnl>=0 else ''}${pnl:.4f}",
                                 fg=GREEN if pnl>=0 else RED)
            if s["pos"]:
                side="LONG 🟢" if s["pos"].get("side")=="Buy" else "SHORT 🔴"
                self.stat_pos.config(text=side)
            else:
                self.stat_pos.config(text="Ninguna")
            self.stat_cic.config(text=str(s["ciclos"]))
        self.root.after(1000,self.poll_stats)


def main():
    root=tk.Tk()
    # Icono opcional
    try:
        if hasattr(sys,"_MEIPASS"):
            ico=os.path.join(sys._MEIPASS,"autobot.ico")
            if os.path.exists(ico): root.iconbitmap(ico)
    except: pass
    AutobotApp(root)
    root.mainloop()


if __name__=="__main__":
    main()
