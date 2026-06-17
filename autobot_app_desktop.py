"""
╔══════════════════════════════════════════════════════════╗
║   AUTOBOT v4.1 — App de Escritorio · Estrategia GRID      ║
║   Por Ezequiel Sack — Proyecto experimental               ║
║                                                          ║
║   Estrategia de GRILLA (grid) con 3 niveles de riesgo:    ║
║     • BTC en SPOT  → SIN apalancamiento (en todos)        ║
║     • SOL y ETH en futuros con órdenes maker              ║
║       Conservador 2x · Medio 3x · Agresivo 5x             ║
║     • Compra barato / vende caro en cada oscilación       ║
║     • Se ajusta SOLO al capital de cada usuario           ║
║                                                          ║
║   Ezequiel Sack NO tiene acceso a ningún fondo.           ║
║   No es asesoramiento financiero. Podés perder tu capital.║
╚══════════════════════════════════════════════════════════╝
"""

import threading
import time
import queue
import os
import sys
from datetime import datetime, date
from decimal import Decimal, ROUND_DOWN

import tkinter as tk
from tkinter import ttk, messagebox

try:
    from pybit.unified_trading import HTTP
except ImportError:
    HTTP = None

# ═══════════════════════════════════════════════════════════
#   PARÁMETROS DE LA ESTRATEGIA GRID (conservadora)
# ═══════════════════════════════════════════════════════════
LOOP_SLEEP       = 15         # segundos entre revisiones

# ── NIVELES DE RIESGO (el usuario elige antes de iniciar) ─────────────────────
# BTC SIEMPRE en spot 0x (ancla segura, no liquidable) en TODOS los modos.
# A mayor apalancamiento → más inventario y volumen, PERO freno (stop) más
# ajustado para que la liquidación nunca se alcance. La liquidación a Nx está
# a ~(100/N)% en contra; el freno cierra MUCHO antes.
RISK_MODES = {
    #              apalanc.   inventario/par   freno        niveles
    "Conservador": {"lev": 2, "alloc_fut": 0.12, "stop_fut": 0.030, "niveles_fut": 8},
    "Medio":       {"lev": 3, "alloc_fut": 0.18, "stop_fut": 0.025, "niveles_fut": 8},
    "Agresivo":    {"lev": 5, "alloc_fut": 0.25, "stop_fut": 0.020, "niveles_fut": 8},
}
MODO_DEFAULT     = "Conservador"

ALLOC_SPOT_PCT   = 0.18       # 18% para BTC spot (igual en todos los modos)
SPACING_FUT      = 0.0025     # 0.25% entre niveles (futuros)

NIVELES_SPOT     = 6          # compras escalonadas en BTC spot
SPACING_SPOT     = 0.010      # 1.0% entre niveles (spot, fee más caro)

MAKER_FUT        = 0.0002     # 0.02%
MAKER_SPOT       = 0.0010     # 0.10%

FUTUROS = ["SOLUSDT", "ETHUSDT"]
SPOT    = "BTCUSDT"

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


def _round_down(value: Decimal, unit: Decimal) -> Decimal:
    if unit == 0:
        return value
    return (value / unit).quantize(Decimal("1"), rounding=ROUND_DOWN) * unit


# ═══════════════════════════════════════════════════════════
#   MOTOR GRID (futuros SOL/ETH + spot BTC), auto-dimensionado
# ═══════════════════════════════════════════════════════════
class GridBotEngine:
    def __init__(self, api_key, api_secret, testnet, log_callback, modo="Conservador"):
        self.testnet = testnet
        self.modo = modo if modo in RISK_MODES else "Conservador"
        self.mc = RISK_MODES[self.modo]
        self.session = HTTP(testnet=testnet, api_key=api_key,
                            api_secret=api_secret, recv_window=15000)
        self.log = log_callback
        self.running = False
        self.thread = None
        self.equity_inicio_dia = 0.0
        self.fecha_dia = None
        # órdenes activas por mercado: sym -> {orderId: {side, price}}
        self.activos = {s: {} for s in FUTUROS + [SPOT]}
        self.specs = {}        # sym -> {tick, step, min_qty/min_amt, qty, niveles, cat, spacing}
        self.pausados = set()
        self.stats = {
            "equity": 0.0, "libre": 0.0, "usado": 0.0, "ganancia_hoy": 0.0,
            "ordenes": 0, "posiciones": [], "ciclos": 0, "estado": "—",
        }

    # ── Balance / conexión ─────────────────────────────────
    def _acc(self):
        return self.session.get_wallet_balance(accountType="UNIFIED")["result"]["list"][0]

    def get_equity(self):
        try:
            a = self._acc()
            return float(a.get("totalEquity") or 0)
        except Exception:
            return 0.0

    def test_conexion(self):
        try:
            a = self._acc()
            eq = float(a.get("totalEquity") or 0)
            return True, f"Conectado · Capital: ${eq:.2f} USDT", eq
        except Exception as e:
            txt = str(e)
            if "401" in txt or "10003" in txt or "invalid" in txt.lower():
                return False, ("No se pudo entrar a tu cuenta de Bybit.\n\n"
                               "• ¿Las claves son de tu cuenta real?\n"
                               "• ¿Copiaste bien el Secret? Se ve una sola vez.\n"
                               "• ¿La clave tiene restricción de IP? Quitala.\n"
                               "• ¿Tenés VPN? Probá sin VPN."), 0.0
            if "10002" in txt or "timestamp" in txt.lower():
                return False, "Tu reloj está desincronizado. Sincronizá la hora de Windows.", 0.0
            return False, f"Error de conexión:\n{txt}", 0.0

    # ── Auto-dimensionado según el capital del usuario ─────
    def _instrument(self, cat, sym):
        it = self.session.get_instruments_info(category=cat, symbol=sym)["result"]["list"][0]
        tick = Decimal(it["priceFilter"]["tickSize"])
        if cat == "linear":
            return tick, Decimal(it["lotSizeFilter"]["qtyStep"]), \
                   Decimal(it["lotSizeFilter"]["minOrderQty"]), None
        return tick, Decimal(it["lotSizeFilter"]["basePrecision"]), None, \
               Decimal(it["lotSizeFilter"]["minOrderAmt"])

    def _precio(self, cat, sym):
        return float(self.session.get_tickers(category=cat, symbol=sym)
                     ["result"]["list"][0]["lastPrice"])

    def _dimensionar(self, equity):
        """Calcula qty y niveles para cada mercado según el capital. Salta el que no entra."""
        # Futuros
        for sym in FUTUROS:
            try:
                tick, step, min_qty, _ = self._instrument("linear", sym)
                price = self._precio("linear", sym)
                alloc = equity * self.mc["alloc_fut"]
                niv = self.mc["niveles_fut"]
                qty = _round_down(Decimal(str(alloc / price / niv)), step)
                niveles = niv
                if qty < min_qty:
                    # No entran todos los niveles → ver cuántos de tamaño mínimo entran
                    posibles = int((Decimal(str(alloc / price)) / min_qty))
                    if posibles < 1:
                        self.log(f"⚠ {sym}: capital chico, no se opera este par", "wait")
                        continue
                    qty = _round_down(min_qty, step)
                    niveles = min(niv, posibles)
                self.specs[sym] = {"cat": "linear", "tick": tick, "step": step,
                                   "qty": qty, "niveles": niveles, "spacing": SPACING_FUT,
                                   "stop": self.mc["stop_fut"]}
            except Exception as e:
                self.log(f"⚠ {sym}: {e}", "err")
        # Spot BTC
        try:
            tick, bprec, _, min_amt = self._instrument("spot", SPOT)
            price = self._precio("spot", SPOT)
            alloc = equity * ALLOC_SPOT_PCT
            qty = _round_down(Decimal(str(alloc / price / NIVELES_SPOT)), bprec)
            niveles = NIVELES_SPOT
            notional = float(qty) * price
            if notional < float(min_amt):
                posibles = int(alloc / (float(min_amt)))
                if posibles < 1:
                    self.log("⚠ BTC: capital chico para spot, no se opera BTC", "wait")
                else:
                    qty = _round_down(Decimal(str(float(min_amt) * 1.05 / price)), bprec)
                    niveles = min(NIVELES_SPOT, posibles)
                    self.specs[SPOT] = {"cat": "spot", "tick": tick, "step": bprec,
                                        "qty": qty, "niveles": niveles, "spacing": SPACING_SPOT}
            else:
                self.specs[SPOT] = {"cat": "spot", "tick": tick, "step": bprec,
                                    "qty": qty, "niveles": niveles, "spacing": SPACING_SPOT}
        except Exception as e:
            self.log(f"⚠ BTC spot: {e}", "err")

    # ── Órdenes ────────────────────────────────────────────
    def _set_leverage(self, sym):
        try:
            self.session.set_leverage(category="linear", symbol=sym,
                buyLeverage=str(self.mc["lev"]), sellLeverage=str(self.mc["lev"]))
        except Exception:
            pass

    def _place(self, sym, side, qty, price):
        sp = self.specs[sym]
        try:
            r = self.session.place_order(
                category=sp["cat"], symbol=sym, side=side, orderType="Limit",
                qty=str(qty), price=str(price), timeInForce="PostOnly",
                **({"reduceOnly": False} if sp["cat"] == "linear" else {}))
            return r["result"]["orderId"]
        except Exception as e:
            self.log(f"❌ orden {sym}: {e}", "err")
            return None

    def _open_ids(self, sym):
        sp = self.specs[sym]
        r = self.session.get_open_orders(category=sp["cat"], symbol=sym)["result"]["list"]
        return {o["orderId"] for o in r}

    def _sembrar(self, sym):
        sp = self.specs[sym]
        price = self._precio(sp["cat"], sym)
        sp["centro"] = price
        tick, step, qty = sp["tick"], sp["step"], sp["qty"]
        if sp["cat"] == "linear":
            self._set_leverage(sym)
            for i in range(1, sp["niveles"] + 1):
                bp = _round_down(Decimal(str(price * (1 - sp["spacing"] * i))), tick)
                spp = _round_down(Decimal(str(price * (1 + sp["spacing"] * i))), tick)
                for side, p in (("Buy", bp), ("Sell", spp)):
                    oid = self._place(sym, side, qty, p)
                    if oid: self.activos[sym][oid] = {"side": side, "price": p}
        else:
            for i in range(1, sp["niveles"] + 1):
                bp = _round_down(Decimal(str(price * (1 - sp["spacing"] * i))), tick)
                oid = self._place(sym, "Buy", qty, bp)
                if oid: self.activos[sym][oid] = {"side": "Buy", "price": bp}
        self.log(f"🌱 {sym.replace('USDT','')}: grilla puesta ({len(self.activos[sym])} órdenes)", "sys")

    def _flatten(self, sym):
        """Cierra inventario de futuros a mercado (anti-tendencia)."""
        try:
            for p in self.session.get_positions(category="linear", symbol=sym)["result"]["list"]:
                size = float(p.get("size", 0) or 0)
                if size > 0:
                    cs = "Sell" if p["side"] == "Buy" else "Buy"
                    self.session.place_order(category="linear", symbol=sym, side=cs,
                        orderType="Market", qty=str(size), reduceOnly=True)
                    self.log(f"🛑 {sym}: posición cerrada para protegerte", "err")
        except Exception as e:
            self.log(f"flatten {sym}: {e}", "err")

    def _btc_balance(self):
        try:
            a = self.session.get_wallet_balance(accountType="UNIFIED", coin="BTC")["result"]["list"][0]
            for c in a.get("coin", []):
                if c.get("coin") == "BTC":
                    v = c.get("walletBalance") or "0"
                    return Decimal(v) if v not in ("", None) else Decimal("0")
        except Exception:
            pass
        return Decimal("0")

    def _reconciliar(self, sym):
        sp = self.specs[sym]
        price = self._precio(sp["cat"], sym)
        # Stop sólo en futuros
        if sp["cat"] == "linear" and "centro" in sp:
            desvio = abs(price - sp["centro"]) / sp["centro"]
            if desvio > sp["stop"]:
                self.log(f"🚨 {sym.replace('USDT','')}: mercado se movió mucho — pauso y protejo", "err")
                try: self.session.cancel_all_orders(category="linear", symbol=sym)
                except Exception: pass
                self._flatten(sym)
                self.activos[sym].clear()
                self.pausados.add(sym)
                return
        abiertas = self._open_ids(sym)
        llenas = [oid for oid in self.activos[sym] if oid not in abiertas]
        inv = self._btc_balance() if sp["cat"] == "spot" else None
        for oid in llenas:
            nivel = self.activos[sym].pop(oid)
            step_price = Decimal(str(sp.get("centro", price) * sp["spacing"]))
            if nivel["side"] == "Buy":
                op = {"side": "Sell", "price": _round_down(nivel["price"] + step_price, sp["tick"])}
            else:
                op = {"side": "Buy", "price": _round_down(nivel["price"] - step_price, sp["tick"])}
            # Reglas spot: no comprar de más, sólo vender si hay BTC
            if sp["cat"] == "spot":
                if op["side"] == "Buy" and inv is not None and \
                   inv >= sp["qty"] * sp["niveles"]:
                    continue
                if op["side"] == "Sell" and inv is not None and inv < sp["qty"]:
                    continue
            self.log(f"✅ {sym.replace('USDT','')}: operación cerrada con ganancia", "trade")
            noid = self._place(sym, op["side"], sp["qty"], op["price"])
            if noid: self.activos[sym][noid] = op

    # ── Stats para el panel claro ──────────────────────────
    def _refrescar_stats(self):
        try:
            a = self._acc()
            eq = float(a.get("totalEquity") or 0)
            libre = float(a.get("totalAvailableBalance") or 0)
            usado = float(a.get("totalInitialMargin") or 0)
            hoy = date.today()
            if self.fecha_dia != hoy:
                self.fecha_dia = hoy
                self.equity_inicio_dia = eq
            ganancia = eq - self.equity_inicio_dia
            ordenes = sum(len(v) for v in self.activos.values())
            posiciones = []
            for sym in FUTUROS:
                for p in self.session.get_positions(category="linear", symbol=sym)["result"]["list"]:
                    if float(p.get("size", 0) or 0) > 0:
                        posiciones.append({
                            "par": sym.replace("USDT", ""),
                            "pnl": round(float(p.get("unrealisedPnl", 0) or 0), 2)})
            estado = ("🟢 GANANDO" if ganancia > 0.01 else
                      "🔴 PERDIENDO" if ganancia < -0.01 else "🟡 EMPATANDO")
            self.stats.update({"equity": round(eq, 2), "libre": round(libre, 2),
                "usado": round(usado, 2), "ganancia_hoy": round(ganancia, 2),
                "ordenes": ordenes, "posiciones": posiciones, "estado": estado})
        except Exception:
            pass

    # ── Loop ───────────────────────────────────────────────
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.log("⏹ Bot detenido. Cancelando órdenes...", "sys")
        for sym in self.specs:
            try:
                self.session.cancel_all_orders(category=self.specs[sym]["cat"], symbol=sym)
            except Exception:
                pass

    def run(self):
        self.log("=" * 46, "sys")
        self.log("🤖 AUTOBOT v4.1 — Estrategia GRID", "sys")
        self.log(f"Modo: {self.modo}  ·  SOL/ETH a {self.mc['lev']}x  ·  BTC spot 0x", "sys")
        self.log(f"Freno de seguridad: corta al {self.mc['stop_fut']*100:.1f}% del centro", "sys")
        self.log("=" * 46, "sys")
        equity = self.get_equity()
        if equity < 10:
            self.log("⚠ Capital muy bajo (mínimo ~$20). Cargá más USDT.", "err")
            return
        self._dimensionar(equity)
        if not self.specs:
            self.log("⚠ No se pudo armar ninguna grilla con tu capital.", "err")
            return
        for sym in self.specs:
            r = self.specs[sym]
            self.log(f"📐 {sym.replace('USDT','')}: {r['niveles']} niveles × {r['qty']}", "sys")
            self._sembrar(sym)

        ciclo = 0
        while self.running:
            ciclo += 1
            self.stats["ciclos"] = ciclo
            for sym in list(self.specs.keys()):
                if sym in self.pausados:
                    continue
                try:
                    self._reconciliar(sym)
                except Exception as e:
                    self.log(f"⚠ {sym}: {e}", "err")
            self._refrescar_stats()
            self._sleep(LOOP_SLEEP)

    def _sleep(self, secs):
        for _ in range(secs):
            if not self.running: break
            time.sleep(1)


# ═══════════════════════════════════════════════════════════
#   INTERFAZ GRÁFICA
# ═══════════════════════════════════════════════════════════
class AutobotApp:
    def __init__(self, root):
        self.root = root
        self.engine = None
        self.log_queue = queue.Queue()
        self.setup_ui()
        self.poll_logs()
        self.poll_stats()

    def setup_ui(self):
        self.root.title("AUTOBOT v4.0 · por Ezequiel Sack")
        self.root.configure(bg=BG)
        self.root.geometry("840x760")
        self.root.minsize(740, 660)

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 8))
        tk.Label(header, text="AUTOBOT", font=("Segoe UI", 20, "bold"), bg=BG, fg=TEXT).pack(side="left")
        tk.Label(header, text="  v4.0 · por Ezequiel Sack",
                 font=("Segoe UI", 10), bg=BG, fg=MUT).pack(side="left", pady=(8, 0))
        self.status_lbl = tk.Label(header, text="● Detenido", font=("Segoe UI", 10, "bold"), bg=BG, fg=MUT)
        self.status_lbl.pack(side="right", pady=(6, 0))

        tk.Label(self.root,
                 text="⚗ Experimental · Grid configurable (BTC sin apalancamiento) · No es asesoramiento financiero",
                 font=("Segoe UI", 8), bg=BG, fg=MUT).pack(anchor="w", padx=24)

        card = tk.Frame(self.root, bg=CARD, highlightbackground=BORD, highlightthickness=1)
        card.pack(fill="x", padx=24, pady=14)
        self.gate_done = self._cargar_gate()
        if not self.gate_done:
            tk.Label(card, text="PASO 1 — CREÁ TU CUENTA EN BYBIT",
                     font=("Segoe UI", 9, "bold"), bg=CARD, fg=GOLD).pack(anchor="w", padx=20, pady=(18, 4))
            tk.Label(card, text="Para usar AUTOBOT necesitás una cuenta en Bybit con el link de\nEzequiel Sack. Es gratis y desbloquea el bot.",
                     font=("Segoe UI", 9), bg=CARD, fg=MUT, justify="left").pack(anchor="w", padx=20)
            self._boton(card, "🚀  Crear mi cuenta en Bybit",
                        self.abrir_registro, primary=True).pack(fill="x", padx=20, pady=(12, 6))
            self.chk_var = tk.IntVar()
            tk.Checkbutton(card, text="Ya creé mi cuenta en Bybit con el link",
                variable=self.chk_var, command=self.toggle_continuar,
                bg=CARD, fg=MUT, selectcolor=CARD, activebackground=CARD,
                activeforeground=TEXT, font=("Segoe UI", 9), bd=0, highlightthickness=0
            ).pack(anchor="w", padx=18, pady=(4, 4))
            self.btn_cont = self._boton(card, "Continuar al paso 2  →", self.pasar_a_api, primary=True)
            self.btn_cont.pack(fill="x", padx=20, pady=(2, 18))
            self.btn_cont.config(state="disabled")
            self._card_ref = card
        else:
            self._construir_form(card)

    def _construir_form(self, card):
        for w in card.winfo_children(): w.destroy()
        tk.Label(card, text="CONECTÁ TU CUENTA",
                 font=("Segoe UI", 9, "bold"), bg=CARD, fg=GOLD).pack(anchor="w", padx=20, pady=(18, 2))
        tk.Label(card, text="Tus claves se usan solo en esta app. Ezequiel Sack NO tiene acceso a tu dinero.",
                 font=("Segoe UI", 8), bg=CARD, fg=MUT).pack(anchor="w", padx=20, pady=(0, 10))
        self.key_entry = self._input(card, "API KEY")
        self.secret_entry = self._input(card, "API SECRET", show="•")
        self.testnet_var = tk.IntVar()
        tk.Checkbutton(card, text="Cuenta de prueba (Testnet)",
            variable=self.testnet_var, bg=CARD, fg=MUT, selectcolor=CARD,
            activebackground=CARD, activeforeground=TEXT, font=("Segoe UI", 9),
            bd=0, highlightthickness=0).pack(anchor="w", padx=18, pady=(2, 8))

        # ── Selector de nivel de riesgo (apalancamiento) ──────────
        tk.Label(card, text="NIVEL DE RIESGO", font=("Segoe UI", 8, "bold"),
                 bg=CARD, fg=MUT).pack(anchor="w", padx=20, pady=(4, 2))
        self.root.option_add("*TCombobox*Listbox.background", "#1a1b28")
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", GOLD)
        try:
            _st = ttk.Style(); _st.theme_use("clam")
            _st.configure("Dark.TCombobox", fieldbackground="#1a1b28", background="#1a1b28",
                foreground=TEXT, arrowcolor=GOLD, bordercolor=BORD)
        except Exception:
            pass
        self.modo_var = tk.StringVar(value=MODO_DEFAULT)
        self.modo_combo = ttk.Combobox(card, textvariable=self.modo_var,
            values=list(RISK_MODES.keys()), state="readonly",
            font=("Segoe UI", 11), style="Dark.TCombobox")
        self.modo_combo.pack(fill="x", padx=20, ipady=3)
        self.modo_hint = tk.Label(card, text=self._modo_texto(MODO_DEFAULT),
            font=("Segoe UI", 8), bg=CARD, fg=MUT, justify="left", wraplength=740)
        self.modo_hint.pack(anchor="w", padx=20, pady=(5, 10))
        self.modo_combo.bind("<<ComboboxSelected>>",
            lambda e: self.modo_hint.config(text=self._modo_texto(self.modo_var.get())))

        btnrow = tk.Frame(card, bg=CARD); btnrow.pack(fill="x", padx=20, pady=(2, 18))
        self.btn_start = self._boton(btnrow, "▶  Iniciar bot", self.iniciar, primary=True)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_stop = self._boton(btnrow, "⏹  Detener", self.detener, primary=False)
        self.btn_stop.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.btn_stop.config(state="disabled")

    def _input(self, parent, label, show=""):
        tk.Label(parent, text=label, font=("Segoe UI", 8, "bold"),
                 bg=CARD, fg=MUT).pack(anchor="w", padx=20, pady=(4, 2))
        e = tk.Entry(parent, font=("Segoe UI", 11), bg="#1a1b28", fg=TEXT,
                     insertbackground=GOLD, relief="flat", show=show)
        e.pack(fill="x", padx=20, ipady=7)
        return e

    def _boton(self, parent, text, cmd, primary=True):
        bg = GOLD if primary else "#1a1b28"
        fg = "#0a0b14" if primary else TEXT
        return tk.Button(parent, text=text, command=cmd, font=("Segoe UI", 11, "bold"),
                bg=bg, fg=fg, relief="flat", cursor="hand2", bd=0,
                activebackground=GOLD2, pady=10)

    def _modo_texto(self, m):
        d = {
            "Conservador": "🟢 Conservador — SOL/ETH 2x · BTC sin apalancamiento · freno 3%. El más seguro (recomendado).",
            "Medio": "🟡 Medio — SOL/ETH 3x · más inventario y volumen · freno 2.5%. Más riesgo si hay tendencia fuerte.",
            "Agresivo": "🔴 Agresivo — SOL/ETH 5x · máximo volumen · freno 2%. ⚠️ Riesgo alto. BTC igual sin apalancamiento.",
        }
        return d.get(m, "")

    # ── Panel claro (cards para cualquiera) ────────────────
    def _build_panel(self):
        if hasattr(self, "panel_frame"): return
        self.panel_frame = tk.Frame(self.root, bg=BG)
        self.panel_frame.pack(fill="x", padx=24, pady=(0, 8))
        row1 = tk.Frame(self.panel_frame, bg=BG); row1.pack(fill="x")
        self.c_estado = self._panel_box(row1, "¿CÓMO VA?", "—", big=True)
        self.c_total  = self._panel_box(row1, "💰 PLATA TOTAL", "—")
        self.c_hoy    = self._panel_box(row1, "📈 GANANCIA DE HOY", "—")
        row2 = tk.Frame(self.panel_frame, bg=BG); row2.pack(fill="x", pady=(6, 0))
        self.c_trab   = self._panel_box(row2, "⚙️ PLATA TRABAJANDO", "—")
        self.c_guard  = self._panel_box(row2, "🛟 PLATA GUARDADA", "—")
        self.c_ord    = self._panel_box(row2, "🔢 ÓRDENES ACTIVAS", "0")

    def _panel_box(self, parent, label, val, big=False):
        f = tk.Frame(parent, bg=CARD, highlightbackground=BORD, highlightthickness=1)
        f.pack(side="left", fill="x", expand=True, padx=3)
        tk.Label(f, text=label, font=("Segoe UI", 7, "bold"), bg=CARD, fg=MUT).pack(anchor="w", padx=12, pady=(8, 0))
        v = tk.Label(f, text=val, font=("Segoe UI", 15 if big else 13, "bold"), bg=CARD, fg=TEXT)
        v.pack(anchor="w", padx=12, pady=(0, 8))
        return v

    def _build_console(self):
        if hasattr(self, "console"): return
        cont = tk.Frame(self.root, bg=CARD, highlightbackground=BORD, highlightthickness=1)
        cont.pack(fill="both", expand=True, padx=24, pady=(6, 14))
        tk.Label(cont, text="ACTIVIDAD DEL BOT (comprando barato y vendiendo caro)",
                 font=("Segoe UI", 8, "bold"), bg=CARD, fg=MUT).pack(anchor="w", padx=14, pady=(10, 4))
        self.console = tk.Text(cont, bg="#0d0e18", fg=TEXT, font=("Consolas", 9),
                               relief="flat", height=12, wrap="word", bd=0)
        self.console.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        for tag, col in [("err", RED), ("ok", GREEN), ("trade", GREEN), ("sys", GOLD),
                         ("wait", MUT)]:
            self.console.tag_config(tag, foreground=col)

    # ── Gate registro ──────────────────────────────────────
    def abrir_registro(self):
        import webbrowser; webbrowser.open(REFERRAL)

    def toggle_continuar(self):
        self.btn_cont.config(state="normal" if self.chk_var.get() else "disabled")

    def pasar_a_api(self):
        self._guardar_gate(); self._construir_form(self._card_ref)

    def _gate_path(self):
        base = os.path.join(os.path.expanduser("~"), ".autobot")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "gate.txt")

    def _cargar_gate(self):
        try: return os.path.exists(self._gate_path())
        except Exception: return False

    def _guardar_gate(self):
        try:
            with open(self._gate_path(), "w") as f: f.write("1")
        except Exception: pass

    # ── Acciones ───────────────────────────────────────────
    def iniciar(self):
        if HTTP is None:
            messagebox.showerror("Error", "Falta instalar pybit. Contactá a Ezequiel Sack.")
            return
        key = self.key_entry.get().strip()
        secret = self.secret_entry.get().strip()
        if not key or not secret:
            messagebox.showwarning("Faltan datos", "Completá API Key y API Secret.")
            return
        testnet = bool(self.testnet_var.get())
        modo = self.modo_var.get() if hasattr(self, "modo_var") else MODO_DEFAULT
        if modo == "Agresivo":
            if not messagebox.askyesno("Modo Agresivo — ¿estás seguro?",
                "El modo AGRESIVO usa 5x de apalancamiento y más inventario en SOL y ETH.\n\n"
                "Genera más volumen, pero el riesgo de perder es MUCHO mayor si el mercado "
                "se mueve fuerte en una dirección.\n\n"
                "(BTC sigue sin apalancamiento.)\n\n¿Querés continuar en modo Agresivo?"):
                return
        self._build_panel(); self._build_console()
        try:
            self.engine = GridBotEngine(key, secret, testnet, self.log_threadsafe, modo)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo iniciar:\n{e}"); return
        self.btn_start.config(state="disabled", text="Conectando...")
        self.status_lbl.config(text="● Conectando...", fg=GOLD)
        self.root.update_idletasks()
        try:
            ok, msg, eq = self.engine.test_conexion()
        except Exception as e:
            ok, msg, eq = False, f"Error inesperado: {e}", 0.0
        if not ok:
            self.engine = None
            self.btn_start.config(state="normal", text="▶  Iniciar bot")
            self.status_lbl.config(text="● Detenido", fg=MUT)
            messagebox.showerror("No se pudo conectar", msg); return
        self.status_lbl.config(text="● Activo", fg=GREEN)
        self.btn_stop.config(state="normal")
        self.log(f"✅ {msg}", "ok")
        self.log("🟢 Cuenta conectada. Armando la grilla según tu capital...", "ok")
        try:
            self.engine.start()
        except Exception as e:
            self.log(f"⚠ {e}", "err")

    def detener(self):
        if self.engine: self.engine.stop()
        self.btn_start.config(state="normal", text="▶  Iniciar bot")
        self.btn_stop.config(state="disabled")
        self.status_lbl.config(text="● Detenido", fg=MUT)

    # ── Logging / polling ──────────────────────────────────
    def log_threadsafe(self, msg, tag="sys"):
        self.log_queue.put((msg, tag))

    def poll_logs(self):
        try:
            while True:
                msg, tag = self.log_queue.get_nowait()
                if hasattr(self, "console"):
                    ts = datetime.now().strftime("%H:%M:%S")
                    self.console.insert("end", f"[{ts}] {msg}\n", tag)
                    self.console.see("end")
                    if int(self.console.index("end-1c").split(".")[0]) > 500:
                        self.console.delete("1.0", "100.0")
        except queue.Empty:
            pass
        self.root.after(300, self.poll_logs)

    def log(self, msg, tag="sys"):
        self.log_threadsafe(msg, tag)

    def poll_stats(self):
        if self.engine and hasattr(self, "c_total"):
            s = self.engine.stats
            color = GREEN if s["ganancia_hoy"] >= 0 else RED
            self.c_estado.config(text=s["estado"], fg=color if s["estado"] != "—" else TEXT)
            self.c_total.config(text=f"${s['equity']:,.2f}")
            signo = "+" if s["ganancia_hoy"] >= 0 else ""
            self.c_hoy.config(text=f"{signo}${s['ganancia_hoy']:.2f}", fg=color)
            self.c_trab.config(text=f"${s['usado']:,.2f}")
            self.c_guard.config(text=f"${s['libre']:,.2f}")
            self.c_ord.config(text=str(s["ordenes"]))
        self.root.after(1500, self.poll_stats)


def main():
    root = tk.Tk()
    try:
        if hasattr(sys, "_MEIPASS"):
            ico = os.path.join(sys._MEIPASS, "autobot.ico")
            if os.path.exists(ico): root.iconbitmap(ico)
    except Exception:
        pass
    AutobotApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
