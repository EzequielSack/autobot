"""
panel.py — Genera un panel SÚPER SIMPLE (panel.html) que muestra qué está
haciendo el bot, en lenguaje para cualquiera. Se actualiza solo cada 20s.

Uso:
    py panel.py        # queda corriendo y refresca panel.html
Después abrí panel.html con doble clic. La página se refresca sola.
"""

import os
import json
import time
from datetime import datetime, date
from pybit.unified_trading import HTTP
from secure_env import load_secure_env

load_secure_env()
TESTNET = os.getenv("TESTNET", "true").lower() == "true"
session = HTTP(testnet=TESTNET, api_key=os.getenv("BYBIT_API_KEY"),
               api_secret=os.getenv("BYBIT_API_SECRET"))

HERE      = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, "panel.html")
BASE_PATH = os.path.join(HERE, "panel_baseline.json")


def baseline_hoy(equity: float) -> float:
    """Guarda con cuánta plata arrancó el día para calcular la ganancia de hoy."""
    hoy = date.today().isoformat()
    data = {}
    if os.path.exists(BASE_PATH):
        try:
            data = json.load(open(BASE_PATH))
        except Exception:
            data = {}
    if data.get("fecha") != hoy:
        data = {"fecha": hoy, "equity_inicio": equity}
        json.dump(data, open(BASE_PATH, "w"))
    return data["equity_inicio"]


def leer_estado() -> dict:
    acc = session.get_wallet_balance(accountType="UNIFIED")["result"]["list"][0]
    total  = float(acc.get("totalEquity") or 0)
    libre  = float(acc.get("totalAvailableBalance") or 0)
    usado  = float(acc.get("totalInitialMargin") or 0)
    inicio = baseline_hoy(total)
    ganancia_hoy = total - inicio

    # Operaciones activas (órdenes esperando) y posiciones abiertas
    ordenes = 0
    posiciones = []
    for cat, sym in [("linear", "SOLUSDT"), ("linear", "ETHUSDT"), ("spot", "BTCUSDT")]:
        try:
            o = session.get_open_orders(category=cat, symbol=sym)["result"]["list"]
            ordenes += len(o)
        except Exception:
            pass
    for sym in ["SOLUSDT", "ETHUSDT"]:
        try:
            for p in session.get_positions(category="linear", symbol=sym)["result"]["list"]:
                if float(p.get("size", 0) or 0) > 0:
                    posiciones.append({
                        "par": sym.replace("USDT", ""),
                        "tipo": "Comprado" if p["side"] == "Buy" else "Vendido",
                        "pnl": round(float(p.get("unrealisedPnl", 0) or 0), 2),
                    })
        except Exception:
            pass

    return {
        "total": round(total, 2),
        "libre": round(libre, 2),
        "usado": round(usado, 2),
        "ganancia_hoy": round(ganancia_hoy, 2),
        "ordenes": ordenes,
        "posiciones": posiciones,
        "hora": datetime.now().strftime("%H:%M:%S"),
    }


def render(e: dict) -> str:
    gan = e["ganancia_hoy"]
    if gan > 0.01:
        estado_txt, estado_emoji, color = "EL BOT ESTÁ GANANDO", "🟢", "#16c784"
    elif gan < -0.01:
        estado_txt, estado_emoji, color = "EL BOT ESTÁ PERDIENDO", "🔴", "#ea3943"
    else:
        estado_txt, estado_emoji, color = "EL BOT ESTÁ EMPATANDO", "🟡", "#f0b90b"

    signo = "+" if gan >= 0 else ""
    pos_html = ""
    if e["posiciones"]:
        filas = "".join(
            f"<div class='pos'><b>{p['par']}</b> · {p['tipo']} "
            f"<span style='color:{'#16c784' if p['pnl']>=0 else '#ea3943'}'>"
            f"{'+' if p['pnl']>=0 else ''}{p['pnl']} USD</span></div>"
            for p in e["posiciones"])
        pos_html = f"<div class='card wide'><div class='lbl'>Operaciones abiertas ahora</div>{filas}</div>"
    else:
        pos_html = ("<div class='card wide'><div class='lbl'>Operaciones abiertas ahora</div>"
                    "<div class='pos'>Ninguna en este momento — esperando el mejor precio</div></div>")

    return f"""<!doctype html><html lang="es"><head>
<meta charset="utf-8"><meta http-equiv="refresh" content="20">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mi Bot · Panel simple</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; font-family:'Segoe UI',system-ui,sans-serif; }}
  body {{ background:#0e1116; color:#fff; padding:24px; }}
  h1 {{ font-size:26px; margin-bottom:4px; }}
  .sub {{ color:#8a93a2; font-size:14px; margin-bottom:20px; }}
  .estado {{ font-size:30px; font-weight:800; color:{color}; margin:10px 0 24px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; }}
  .card {{ background:#171c26; border-radius:18px; padding:22px; }}
  .card.wide {{ grid-column:1/-1; }}
  .lbl {{ color:#8a93a2; font-size:14px; margin-bottom:8px; }}
  .big {{ font-size:38px; font-weight:800; }}
  .pos {{ font-size:18px; padding:8px 0; border-top:1px solid #232a36; }}
  .pos:first-of-type {{ border-top:none; }}
  .foot {{ color:#5b6472; font-size:13px; margin-top:22px; }}
</style></head><body>
  <h1>🤖 Mi Bot de Trading</h1>
  <div class="sub">Se actualiza solo cada 20 segundos · última vez: {e['hora']}</div>
  <div class="estado">{estado_emoji} {estado_txt}</div>
  <div class="grid">
    <div class="card">
      <div class="lbl">💰 Plata total que tenés</div>
      <div class="big">${e['total']}</div>
    </div>
    <div class="card">
      <div class="lbl">📈 Ganancia de hoy</div>
      <div class="big" style="color:{color}">{signo}${gan}</div>
    </div>
    <div class="card">
      <div class="lbl">⚙️ Plata trabajando (en operaciones)</div>
      <div class="big">${e['usado']}</div>
    </div>
    <div class="card">
      <div class="lbl">🛟 Plata guardada (sin riesgo)</div>
      <div class="big">${e['libre']}</div>
    </div>
    <div class="card">
      <div class="lbl">🔢 Órdenes esperando comprar/vender</div>
      <div class="big">{e['ordenes']}</div>
    </div>
    {pos_html}
  </div>
  <div class="foot">Esto NO es asesoramiento financiero. El bot opera con dinero real.</div>
</body></html>"""


def run():
    print("Panel iniciado. Generando panel.html cada 20s...")
    print(f"Abrí con doble clic: {HTML_PATH}")
    while True:
        try:
            estado = leer_estado()
            with open(HTML_PATH, "w", encoding="utf-8") as f:
                f.write(render(estado))
            print(f"[{estado['hora']}] total=${estado['total']} "
                  f"hoy={estado['ganancia_hoy']:+} ordenes={estado['ordenes']}")
        except Exception as ex:
            print("Error:", ex)
        time.sleep(20)


if __name__ == "__main__":
    run()
