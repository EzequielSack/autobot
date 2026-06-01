"""
AUTOBOT — Tu dinero trabajando solo
UI Premium — mismo sistema de diseño que P2P Radar
"""

import streamlit as st
import pandas as pd
import os, time
from datetime import datetime

# ── Bybit session ──────────────────────────────────────────
def get_session(api_key, api_secret, testnet=False):
    try:
        from pybit.unified_trading import HTTP
        return HTTP(testnet=testnet, api_key=api_key, api_secret=api_secret)
    except:
        return None

def get_wallet(session):
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        coin = resp["result"]["list"][0]["coin"][0]
        return {
            "equity":      float(coin.get("equity", 0)),
            "disponible":  float(coin.get("availableToWithdraw", 0)),
            "pnl_abierto": float(coin.get("unrealisedPnl", 0)),
        }
    except:
        return None

def get_posiciones(session):
    try:
        out = []
        resp = session.get_positions(category="linear", symbol="BTCUSDT")
        for p in resp["result"]["list"]:
            if float(p.get("size", 0)) > 0:
                out.append(p)
        return out
    except:
        return []

def leer_trades():
    ruta = "data/trades.csv"
    if os.path.exists(ruta):
        try: return pd.read_csv(ruta)
        except: pass
    return pd.DataFrame()

REFERRAL = "https://partner.bybit.com/b/59453"

# ── Page config ───────────────────────────────────────────
st.set_page_config(page_title="AUTOBOT", page_icon="🤖", layout="wide",
                   initial_sidebar_state="collapsed")

# ── CSS — mismo sistema que P2P Radar ─────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

body, p, span, div, h1, h2, h3, h4, label, input, button, td, th {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

/* ── chrome ── */
#MainMenu, footer, header, [data-testid="stToolbar"], [data-testid="stDecoration"] {
  display: none !important; visibility: hidden !important;
}
.stApp { background: #0d1117 !important; }
.main .block-container {
  padding-top: 0 !important; padding-bottom: 3rem !important;
  max-width: 100% !important; padding-left: 0 !important; padding-right: 0 !important;
}

/* ── scrollbar ── */
::-webkit-scrollbar { width: 3px; height: 3px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.06); border-radius: 3px; }

/* ── tabs ── */
.stTabs [data-baseweb="tab-list"] {
  background: rgba(255,255,255,0.018) !important;
  border: 1px solid rgba(255,255,255,0.045) !important;
  border-radius: 10px !important; padding: 3px !important; gap: 1px !important;
  margin: 0 28px 20px !important;
}
.stTabs [data-baseweb="tab"] {
  background: transparent !important; color: rgba(255,255,255,0.25) !important;
  font-size: 12px !important; font-weight: 500 !important;
  padding: 7px 18px !important; border-radius: 7px !important; border: none !important;
}
.stTabs [aria-selected="true"] {
  color: rgba(255,255,255,0.85) !important;
  background: rgba(255,255,255,0.07) !important; font-weight: 700 !important;
}
.stTabs [data-baseweb="tab-panel"] { padding: 0 !important; }

/* ── inputs ── */
.stTextInput input {
  background: rgba(255,255,255,0.03) !important;
  border: 1px solid rgba(255,255,255,0.07) !important;
  border-radius: 10px !important; color: #fff !important; font-size: 13px !important;
  height: 46px !important;
}
.stTextInput input:focus {
  border-color: rgba(0,214,143,0.35) !important;
  box-shadow: 0 0 0 2px rgba(0,214,143,0.07) !important;
}
.stTextInput label { font-size: 10px !important; color: rgba(255,255,255,0.3) !important;
  text-transform: uppercase !important; letter-spacing: 1.5px !important; font-weight: 700 !important; }

/* ── buttons ── */
.stButton > button {
  background: rgba(0,214,143,0.09) !important;
  border: 1px solid rgba(0,214,143,0.28) !important;
  border-radius: 10px !important; color: #00d68f !important;
  font-weight: 700 !important; font-size: 13px !important;
  height: 46px !important; width: 100% !important;
  transition: all .15s ease !important;
}
.stButton > button:hover {
  background: rgba(0,214,143,0.16) !important;
  border-color: rgba(0,214,143,0.5) !important;
  box-shadow: 0 0 20px rgba(0,214,143,0.15) !important;
}

/* ── link button ── */
.stLinkButton > a {
  background: rgba(0,214,143,0.09) !important;
  border: 1px solid rgba(0,214,143,0.28) !important;
  border-radius: 10px !important; color: #00d68f !important;
  font-weight: 700 !important; font-size: 13px !important;
  padding: 12px 20px !important; text-align: center !important;
  display: block !important; text-decoration: none !important;
  transition: all .15s ease !important;
}
.stLinkButton > a:hover { background: rgba(0,214,143,0.16) !important; }

/* ── form ── */
[data-testid="stForm"] {
  background: rgba(255,255,255,0.025) !important;
  border: 1px solid rgba(255,255,255,0.07) !important;
  border-radius: 14px !important; padding: 24px !important;
}

/* ── checkbox ── */
.stCheckbox label { color: rgba(255,255,255,0.45) !important; font-size: 12px !important; }

/* ── alerts ── */
[data-testid="stSuccess"] { background: rgba(0,214,143,0.04) !important; border: 1px solid rgba(0,214,143,0.15) !important; border-radius: 10px !important; }
[data-testid="stError"]   { background: rgba(239,83,80,0.04) !important;  border: 1px solid rgba(239,83,80,0.15) !important;  border-radius: 10px !important; }
[data-testid="stWarning"] { background: rgba(255,215,64,0.04) !important; border: 1px solid rgba(255,215,64,0.15) !important; border-radius: 10px !important; }

/* ── expander ── */
details summary { color: rgba(255,255,255,0.5) !important; font-size: 13px !important; }

@keyframes fadeUp { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }
@keyframes blink  { 0%,100%{opacity:1} 50%{opacity:.3} }
@keyframes pulse  { 0%,100%{box-shadow:0 0 0 0 rgba(0,214,143,0.4)} 70%{box-shadow:0 0 0 8px rgba(0,214,143,0)} }
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────
for k, v in [("conectado", False), ("api_key", ""), ("api_secret", ""), ("session", None)]:
    if k not in st.session_state: st.session_state[k] = v

# ══════════════════════════════════════════════════════════
#   TOPBAR
# ══════════════════════════════════════════════════════════
estado_txt = "ACTIVO" if st.session_state.conectado else "DESCONECTADO"
estado_css = "rgba(0,214,143,0.07)" if st.session_state.conectado else "rgba(255,255,255,0.04)"
estado_col = "#00d68f" if st.session_state.conectado else "rgba(255,255,255,0.25)"
dot_css    = f"background:{estado_col}; animation: {'blink 2s ease infinite' if st.session_state.conectado else 'none'}"

st.markdown(f"""
<div style="
  display:flex; align-items:center; justify-content:space-between;
  padding: 20px 28px;
  border-bottom: 1px solid rgba(255,255,255,0.05);
  background: rgba(255,255,255,0.01);
  margin-bottom: 4px;
">
  <div style="display:flex;align-items:center;gap:12px">
    <span style="font-size:22px; font-weight:900; color:#fff; letter-spacing:-1px">AUTO<span style="color:#00d68f">BOT</span></span>
    <span style="font-size:10px; color:rgba(255,255,255,0.15); text-transform:uppercase; letter-spacing:2px; border-left:1px solid rgba(255,255,255,0.08); padding-left:12px">Tu dinero trabajando solo</span>
  </div>
  <div style="
    display:flex; align-items:center; gap:7px;
    background:{estado_css}; border:1px solid {estado_col}33;
    border-radius:20px; padding:6px 14px;
    font-size:11px; color:{estado_col}; font-weight:700; letter-spacing:0.5px;
  ">
    <div style="width:6px;height:6px;border-radius:50%;{dot_css}"></div>
    {estado_txt}
  </div>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════
#   TABS
# ══════════════════════════════════════════════════════════
if st.session_state.conectado:
    tab1, tab2, tab3 = st.tabs(["📊   Dashboard", "📋   Operaciones", "⚙️   Mi cuenta"])
else:
    tab1, tab2, tab3 = st.tabs(["🏠   ¿Qué es AUTOBOT?", "🚀   Cómo empezar", "🔑   Conectar cuenta"])


# ══════════════════════════════════════════════════════════
#   TAB 1
# ══════════════════════════════════════════════════════════
with tab1:

    if not st.session_state.conectado:

        # Hero
        st.markdown("""
        <div style="
          padding: 48px 28px 36px;
          animation: fadeUp .5s cubic-bezier(0.16,1,0.3,1) both;
        ">
          <div style="font-size:11px;font-weight:700;color:rgba(0,214,143,0.7);letter-spacing:2.5px;text-transform:uppercase;margin-bottom:16px">
            Estrategia validada · 1 año de datos reales · BTC/USDT
          </div>
          <h1 style="font-size:48px;font-weight:900;color:#fff;letter-spacing:-2.5px;line-height:1.05;margin:0 0 16px">
            Tu plata trabajando<br>mientras vos dormís
          </h1>
          <p style="font-size:16px;color:rgba(255,255,255,0.35);font-weight:400;line-height:1.6;max-width:520px;margin:0">
            AUTOBOT compra y vende Bitcoin automáticamente usando inteligencia artificial.
            Vos ponés la plata. Él hace el trabajo.
          </p>
        </div>
        """, unsafe_allow_html=True)

        # KPIs del backtest
        st.markdown("""
        <div style="padding:0 28px;display:grid;grid-template-columns:2fr 1fr 1fr;gap:10px;animation:fadeUp .5s .1s cubic-bezier(0.16,1,0.3,1) both">

          <div style="
            background:linear-gradient(140deg,rgba(0,214,143,0.08),rgba(255,255,255,0.02));
            border:1px solid rgba(0,214,143,0.22); border-radius:16px; padding:24px 28px;
            position:relative; overflow:hidden;
          ">
            <div style="position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent 5%,rgba(0,214,143,0.5) 50%,transparent 95%)"></div>
            <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:14px">Retorno en 1 año (backtest)</div>
            <div style="font-size:52px;font-weight:900;color:#00d68f;letter-spacing:-3px;line-height:1">+699%</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:8px">$300 → $2.397 · datos reales de Bybit</div>
          </div>

          <div style="background:rgba(255,255,255,0.028);border:1px solid rgba(255,255,255,0.07);border-radius:16px;padding:24px 20px">
            <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:14px">Peor caída histórica</div>
            <div style="font-size:36px;font-weight:900;color:#fff;letter-spacing:-1.5px;line-height:1">-13%</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:8px">Riesgo controlado al 0.5%</div>
          </div>

          <div style="background:rgba(255,255,255,0.028);border:1px solid rgba(255,255,255,0.07);border-radius:16px;padding:24px 20px">
            <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:14px">Gana por cada $1 que pierde</div>
            <div style="font-size:36px;font-weight:900;color:#fff;letter-spacing:-1.5px;line-height:1">$2.83</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:8px">Profit Factor · 103 operaciones</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)

        # Cómo funciona
        st.markdown("""
        <div style="padding:0 28px;animation:fadeUp .5s .2s cubic-bezier(0.16,1,0.3,1) both">
          <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.18);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:20px">Cómo funciona</div>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">

            <div style="background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:22px 20px">
              <div style="font-size:28px;margin-bottom:12px">🔍</div>
              <div style="font-size:13px;font-weight:700;color:#fff;margin-bottom:6px">Analiza el mercado</div>
              <div style="font-size:12px;color:rgba(255,255,255,0.3);line-height:1.6">Revisa Bitcoin cada 5 minutos usando 6 indicadores simultáneos. Si todos confirman, entra.</div>
            </div>

            <div style="background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:22px 20px">
              <div style="font-size:28px;margin-bottom:12px">⚡</div>
              <div style="font-size:13px;font-weight:700;color:#fff;margin-bottom:6px">Opera automático</div>
              <div style="font-size:12px;color:rgba(255,255,255,0.3);line-height:1.6">Entra, pone el stop loss y el take profit solo. Sin que vos hagas nada. 24 horas al día.</div>
            </div>

            <div style="background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:22px 20px">
              <div style="font-size:28px;margin-bottom:12px">🛡️</div>
              <div style="font-size:13px;font-weight:700;color:#fff;margin-bottom:6px">Se protege solo</div>
              <div style="font-size:12px;color:rgba(255,255,255,0.3);line-height:1.6">Si hay 4 pérdidas seguidas, para 3 horas. Nunca arriesga más del 0.5% por operación.</div>
            </div>

          </div>
        </div>

        <div style="margin:24px 28px 0;padding:16px 20px;
          background:rgba(255,215,64,0.03);border:1px solid rgba(255,215,64,0.12);border-radius:12px;
          font-size:12px;color:rgba(255,255,255,0.35);line-height:1.7;
          animation:fadeUp .5s .3s cubic-bezier(0.16,1,0.3,1) both">
          ⚠️ <strong style="color:rgba(255,255,255,0.55)">Advertencia importante:</strong>
          El trading tiene riesgo real. Los resultados del backtest son históricos y no garantizan ganancias futuras.
          AUTOBOT fue diseñado para minimizar pérdidas, pero ningún bot es infalible.
          Solo operá con plata que podés perder.
        </div>
        """, unsafe_allow_html=True)

    else:
        # ── DASHBOARD CONECTADO ────────────────────────────
        wallet    = get_wallet(st.session_state.session)
        posiciones = get_posiciones(st.session_state.session)
        df_t      = leer_trades()

        # Stats desde CSV
        wins = losses = total_cerradas = 0
        wr = 0.0
        if not df_t.empty and "resultado" in df_t.columns:
            cerradas       = df_t[df_t["resultado"].notna() & (df_t["resultado"] != "")]
            total_cerradas = len(cerradas)
            wins           = (cerradas["resultado"].str.upper() == "WIN").sum()
            losses         = (cerradas["resultado"].str.upper() == "LOSS").sum()
            wr             = round(wins / total_cerradas * 100) if total_cerradas > 0 else 0

        # KPI strip
        if wallet:
            pnl   = wallet["pnl_abierto"]
            pcol  = "#00d68f" if pnl >= 0 else "#ef5350"
            psign = "+" if pnl >= 0 else ""
            st.markdown(f"""
            <div style="padding:16px 28px;display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:10px;animation:fadeUp .4s cubic-bezier(0.16,1,0.3,1) both">

              <div style="background:linear-gradient(140deg,rgba(0,214,143,0.07),rgba(255,255,255,0.02));border:1px solid rgba(0,214,143,0.2);border-radius:16px;padding:20px 24px;position:relative;overflow:hidden">
                <div style="position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent 5%,rgba(0,214,143,0.5) 50%,transparent 95%)"></div>
                <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:12px">Tu saldo total</div>
                <div style="font-size:44px;font-weight:900;color:#00d68f;letter-spacing:-2.5px;line-height:1">${wallet['equity']:,.2f}</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:6px">USDT · disponible: ${wallet['disponible']:,.2f}</div>
              </div>

              <div style="background:rgba(255,255,255,0.028);border:1px solid rgba(255,255,255,0.07);border-radius:16px;padding:20px">
                <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:12px">Ganancia ahora</div>
                <div style="font-size:28px;font-weight:900;color:{pcol};letter-spacing:-1px;line-height:1">{psign}${pnl:.4f}</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:6px">Posición abierta</div>
              </div>

              <div style="background:rgba(255,255,255,0.028);border:1px solid rgba(255,255,255,0.07);border-radius:16px;padding:20px">
                <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:12px">Win Rate</div>
                <div style="font-size:28px;font-weight:900;color:#fff;letter-spacing:-1px;line-height:1">{wr}%</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:6px">{total_cerradas} operaciones cerradas</div>
              </div>

              <div style="background:rgba(255,255,255,0.028);border:1px solid rgba(255,255,255,0.07);border-radius:16px;padding:20px">
                <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:12px">Ganadas / Perdidas</div>
                <div style="font-size:28px;font-weight:900;color:#fff;letter-spacing:-1px;line-height:1"><span style="color:#00d68f">{wins}</span> / <span style="color:#ef5350">{losses}</span></div>
                <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:6px">Histórico total</div>
              </div>

            </div>
            """, unsafe_allow_html=True)

        # Posición abierta
        st.markdown("""
        <div style="padding:8px 28px 4px">
          <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.18);text-transform:uppercase;letter-spacing:2.5px">Operación abierta ahora</div>
        </div>
        """, unsafe_allow_html=True)

        if posiciones:
            for pos in posiciones:
                lado  = "🟢 Compró Bitcoin" if pos.get("side") == "Buy" else "🔴 Vendió Bitcoin"
                pnl   = float(pos.get("unrealisedPnl", 0))
                entry = float(pos.get("avgPrice", 0))
                sl    = pos.get("stopLoss", "—")
                tp    = pos.get("takeProfit", "—")
                pcol  = "#00d68f" if pnl >= 0 else "#ef5350"
                psign = "+" if pnl >= 0 else ""
                st.markdown(f"""
                <div style="
                  margin:8px 28px 0;
                  background:rgba(255,255,255,0.025);
                  border:1px solid rgba(255,255,255,0.08);
                  border-left:3px solid #c9994a;
                  border-radius:14px; padding:18px 22px;
                  display:flex; align-items:center; justify-content:space-between;
                ">
                  <div>
                    <div style="font-size:15px;font-weight:700;color:#fff;margin-bottom:4px">{lado}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.3)">Entró a <strong style="color:rgba(255,255,255,0.6)">${entry:,.2f}</strong> · SL: {sl} · TP: {tp}</div>
                  </div>
                  <div style="text-align:right">
                    <div style="font-size:11px;color:rgba(255,255,255,0.3);margin-bottom:4px">P&L en tiempo real</div>
                    <div style="font-size:28px;font-weight:900;color:{pcol};letter-spacing:-1px">{psign}${pnl:.4f}</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="margin:8px 28px 0;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:24px;text-align:center;color:rgba(255,255,255,0.2);font-size:13px">
              😴  Sin operación abierta — el bot está esperando la señal correcta
            </div>
            """, unsafe_allow_html=True)

        # Equity curve
        if not df_t.empty and "capital_post" in df_t.columns and len(df_t) > 1:
            st.markdown("""
            <div style="padding:24px 28px 8px">
              <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.18);text-transform:uppercase;letter-spacing:2.5px">Evolución de tu capital</div>
            </div>
            """, unsafe_allow_html=True)
            try:
                import plotly.graph_objects as go
                capital_ini = 300.0
                equity = [capital_ini] + list(df_t["capital_post"].dropna())
                color  = "#00d68f" if equity[-1] >= capital_ini else "#ef5350"
                fill   = "rgba(0,214,143,0.06)" if equity[-1] >= capital_ini else "rgba(239,83,80,0.06)"
                fig = go.Figure()
                fig.add_trace(go.Scatter(y=equity, mode="lines",
                    line=dict(color=color, width=2), fill="tozeroy",
                    fillcolor=fill, name="Capital"))
                fig.add_hline(y=capital_ini, line_dash="dash",
                              line_color="rgba(255,255,255,0.08)")
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="rgba(255,255,255,0.3)", family="Inter"),
                    margin=dict(l=60,r=20,t=10,b=10), height=220, showlegend=False,
                    xaxis=dict(showgrid=False, showticklabels=False, showline=False),
                    yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.04)",
                               tickprefix="$", tickfont=dict(color="rgba(255,255,255,0.2)", size=11)),
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            except:
                pass


# ══════════════════════════════════════════════════════════
#   TAB 2
# ══════════════════════════════════════════════════════════
with tab2:

    if not st.session_state.conectado:

        st.markdown("""
        <div style="padding:28px 28px 0;animation:fadeUp .4s cubic-bezier(0.16,1,0.3,1) both">
          <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.18);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:24px">Empezar en 3 pasos</div>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:32px">

            <div style="background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:16px;padding:28px 24px;animation:fadeUp .4s .05s cubic-bezier(0.16,1,0.3,1) both">
              <div style="font-size:11px;font-weight:800;color:rgba(0,214,143,0.7);letter-spacing:2px;text-transform:uppercase;margin-bottom:16px">Paso 01</div>
              <div style="font-size:16px;font-weight:800;color:#fff;margin-bottom:10px">Abrí tu cuenta en Bybit</div>
              <div style="font-size:12px;color:rgba(255,255,255,0.3);line-height:1.7;margin-bottom:20px">
                Bybit es donde el bot va a operar. Es gratis, tarda 5 minutos y es una de las exchanges más grandes del mundo.
              </div>
            </div>

            <div style="background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:16px;padding:28px 24px;animation:fadeUp .4s .10s cubic-bezier(0.16,1,0.3,1) both">
              <div style="font-size:11px;font-weight:800;color:rgba(0,214,143,0.7);letter-spacing:2px;text-transform:uppercase;margin-bottom:16px">Paso 02</div>
              <div style="font-size:16px;font-weight:800;color:#fff;margin-bottom:10px">Cargá USDT</div>
              <div style="font-size:12px;color:rgba(255,255,255,0.3);line-height:1.7">
                USDT es el dólar digital que usa el bot. Podés cargar desde $100 en adelante.<br><br>
                Recomendamos empezar con <strong style="color:rgba(255,255,255,0.6)">$300</strong> para que los resultados tengan sentido.
              </div>
            </div>

            <div style="background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:16px;padding:28px 24px;animation:fadeUp .4s .15s cubic-bezier(0.16,1,0.3,1) both">
              <div style="font-size:11px;font-weight:800;color:rgba(0,214,143,0.7);letter-spacing:2px;text-transform:uppercase;margin-bottom:16px">Paso 03</div>
              <div style="font-size:16px;font-weight:800;color:#fff;margin-bottom:10px">Conectá AUTOBOT</div>
              <div style="font-size:12px;color:rgba(255,255,255,0.3);line-height:1.7">
                En Bybit creás una "API Key" — es como una llave que le das al bot para operar por vos.<br><br>
                Después la pegás en la pestaña <strong style="color:rgba(255,255,255,0.6)">"Conectar cuenta"</strong>.
              </div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.link_button("👉 Crear cuenta en Bybit gratis", REFERRAL, use_container_width=True)

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # FAQ
        st.markdown("""<div style="padding:0 28px 8px">
          <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.18);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:16px">Preguntas frecuentes</div>
        </div>""", unsafe_allow_html=True)

        with st.expander("¿Es seguro? ¿Me pueden robar la plata?"):
            st.markdown("""
            La API Key que le das al bot tiene **solo permiso para operar** — no puede retirar plata ni transferir nada.
            Si alguien obtuviera la clave, lo peor que puede pasar es que vean tu saldo. No pueden sacar un centavo.
            """)
        with st.expander("¿Puedo perder todo?"):
            st.markdown("""
            El bot tiene protecciones automáticas. Cada operación arriesga máximo el **0.5% del capital**.
            Si tenés $300, la pérdida máxima por operación son $1.50.
            Si hay 4 pérdidas seguidas, para automáticamente 3 horas.
            """)
        with st.expander("¿Cuánto gana?"):
            st.markdown("""
            No podemos garantizar ganancias. En el backtest de 1 año con datos reales de Bybit,
            $300 se convirtieron en $2.397. Pero eso es pasado — el futuro puede ser diferente.
            El Profit Factor es 2.83, lo que significa que históricamente gana $2.83 por cada $1 que pierde.
            """)
        with st.expander("¿El bot opera solo o tengo que hacer algo?"):
            st.markdown("""
            Solo tenés que **dejarlo corriendo**. Opera automáticamente de lunes a jueves y los domingos
            (el análisis mostró que viernes y sábado son días malos para operar).
            """)
        with st.expander("¿Qué pasa si el mercado se cae fuerte?"):
            st.markdown("""
            Cada operación tiene un Stop Loss automático. Si el precio va en contra, el bot sale
            antes de que la pérdida sea mayor. Nunca se queda "atrapado" esperando que recupere.
            """)

    else:
        # ── HISTORIAL ──────────────────────────────────────
        df_t = leer_trades()
        st.markdown("""<div style="padding:8px 28px 16px">
          <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.18);text-transform:uppercase;letter-spacing:2.5px">Últimas operaciones</div>
        </div>""", unsafe_allow_html=True)

        if df_t.empty:
            st.markdown("""<div style="margin:0 28px;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:32px;text-align:center;color:rgba(255,255,255,0.2);font-size:13px">
              Todavía no hay operaciones registradas. El bot las irá agregando automáticamente.
            </div>""", unsafe_allow_html=True)
        else:
            ultimas = df_t.tail(20).iloc[::-1]
            for idx, (_, row) in enumerate(ultimas.iterrows()):
                resultado = str(row.get("resultado", "")).upper()
                if resultado == "WIN":
                    bcol, icol, emoji, label = "#00d68f", "rgba(0,214,143,0.15)", "✅", "Ganó"
                elif resultado == "LOSS":
                    bcol, icol, emoji, label = "#ef5350", "rgba(239,83,80,0.15)", "❌", "Perdió"
                else:
                    bcol, icol, emoji, label = "#c9994a", "rgba(201,153,74,0.15)", "⏳", "Abierta"

                side_txt = "Compró BTC" if str(row.get("side","")) == "Buy" else "Vendió BTC"
                fecha    = str(row.get("timestamp",""))[:16].replace("T"," ")
                precio   = float(row.get("entry_price", 0))
                delay    = min(idx * 0.04, 0.3)

                st.markdown(f"""
                <div style="
                  margin: 0 28px 8px;
                  background: rgba(255,255,255,0.025);
                  border: 1px solid rgba(255,255,255,0.07);
                  border-left: 3px solid {bcol};
                  border-radius: 12px; padding: 14px 20px;
                  display: flex; align-items: center; justify-content: space-between;
                  animation: fadeUp .4s {delay}s cubic-bezier(0.16,1,0.3,1) both;
                ">
                  <div style="display:flex;align-items:center;gap:12px">
                    <span style="font-size:18px">{emoji}</span>
                    <div>
                      <div style="font-size:13px;font-weight:700;color:#fff">{side_txt}</div>
                      <div style="font-size:11px;color:rgba(255,255,255,0.25);margin-top:2px">{fecha}</div>
                    </div>
                  </div>
                  <div style="font-size:13px;color:rgba(255,255,255,0.4)">Entrada: <strong style="color:rgba(255,255,255,0.7)">${precio:,.0f}</strong></div>
                  <div style="background:{icol};border-radius:6px;padding:4px 12px;font-size:11px;font-weight:700;color:{bcol}">{label}</div>
                </div>
                """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════
#   TAB 3
# ══════════════════════════════════════════════════════════
with tab3:

    if not st.session_state.conectado:

        col1, col2 = st.columns([1, 1], gap="large")
        with col1:
            st.markdown("""
            <div style="padding:28px 0 0 28px;animation:fadeUp .4s cubic-bezier(0.16,1,0.3,1) both">
              <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.18);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:16px">Conectá tu cuenta</div>
              <div style="font-size:13px;color:rgba(255,255,255,0.3);line-height:1.7;margin-bottom:24px">
                Necesitás crear una API Key en Bybit.<br><br>
                <strong style="color:rgba(255,255,255,0.55)">Cómo hacerlo:</strong><br>
                1. Bybit.com → tu perfil → "API Management"<br>
                2. "Create New Key" → System-generated<br>
                3. Permisos: <strong style="color:rgba(255,255,255,0.55)">Contract → Read + Write</strong><br>
                4. Copiá y pegá abajo
              </div>
            </div>
            """, unsafe_allow_html=True)

        with col2:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            with st.form("login"):
                api_key    = st.text_input("API Key",    type="password", placeholder="Pegá tu API Key")
                api_secret = st.text_input("API Secret", type="password", placeholder="Pegá tu API Secret")
                testnet    = st.checkbox("Cuenta de prueba (testnet)", value=False)
                submit     = st.form_submit_button("Conectar mi cuenta →")

            if submit:
                if not api_key or not api_secret:
                    st.error("Completá los dos campos.")
                else:
                    with st.spinner("Conectando..."):
                        sess   = get_session(api_key, api_secret, testnet)
                        wallet = get_wallet(sess) if sess else None
                    if wallet:
                        st.session_state.update({
                            "conectado": True, "api_key": api_key,
                            "api_secret": api_secret, "session": sess
                        })
                        st.success(f"✅ Conectado · Saldo: ${wallet['equity']:,.2f} USDT")
                        time.sleep(1); st.rerun()
                    else:
                        st.error("❌ No se pudo conectar. Revisá las API Keys.")

        st.markdown("""
        <div style="margin:16px 28px 0;padding:14px 18px;
          background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.05);border-radius:12px;
          font-size:11px;color:rgba(255,255,255,0.25);line-height:1.7">
          🔒 <strong style="color:rgba(255,255,255,0.4)">Tus claves son privadas.</strong>
          No se guardan en ningún servidor. Se usan solo localmente para conectarse a tu cuenta de Bybit.
          Si cerrás la página, tenés que volver a conectarte.
        </div>
        """, unsafe_allow_html=True)

    else:
        # ── CUENTA ────────────────────────────────────────
        wallet = get_wallet(st.session_state.session)
        st.markdown(f"""
        <div style="padding:28px 28px 0;animation:fadeUp .4s cubic-bezier(0.16,1,0.3,1) both">
          <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.18);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:20px">Mi cuenta</div>
          <div style="background:rgba(0,214,143,0.04);border:1px solid rgba(0,214,143,0.15);border-radius:14px;padding:20px 24px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between">
            <div>
              <div style="font-size:13px;font-weight:700;color:#fff;margin-bottom:4px">✅ Cuenta conectada</div>
              <div style="font-size:12px;color:rgba(255,255,255,0.3)">Saldo: <strong style="color:rgba(255,255,255,0.6)">${wallet['equity']:,.2f} USDT</strong> · Disponible: ${wallet['disponible']:,.2f}</div>
            </div>
          </div>

          <div style="font-size:9px;font-weight:700;color:rgba(255,255,255,0.18);text-transform:uppercase;letter-spacing:2.5px;margin-bottom:16px">Configuración activa del bot</div>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
            <div style="background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:16px 20px">
              <div style="font-size:9px;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2px;margin-bottom:8px">Riesgo por operación</div>
              <div style="font-size:24px;font-weight:800;color:#fff">0.5%</div>
              <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:4px">Máxima pérdida por trade</div>
            </div>
            <div style="background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:16px 20px">
              <div style="font-size:9px;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2px;margin-bottom:8px">Ratio ganancia/pérdida</div>
              <div style="font-size:24px;font-weight:800;color:#fff">1 : 3.5</div>
              <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:4px">Por cada $1 arriesgado, busca $3.5</div>
            </div>
            <div style="background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:16px 20px">
              <div style="font-size:9px;color:rgba(255,255,255,0.2);text-transform:uppercase;letter-spacing:2px;margin-bottom:8px">Días que opera</div>
              <div style="font-size:18px;font-weight:800;color:#fff">Lun–Jue + Dom</div>
              <div style="font-size:11px;color:rgba(255,255,255,0.2);margin-top:4px">Viernes y sábado: descanso</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1, 1, 3])
        with col1:
            if st.button("🔌 Desconectar"):
                st.session_state.update({"conectado": False, "api_key": "", "api_secret": "", "session": None})
                st.rerun()
