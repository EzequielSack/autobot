# AUTOBOT v3.0 — Bot de Trading Multi-par para Bybit Futures

**El bot monitorea BTC, ETH y SOL simultáneamente, pero solo opera uno a la vez.
Elige el mejor par según calidad de señal y prioridad. BTC es la base, ETH es el
segundo mercado serio y SOL es par de oportunidad con riesgo reducido.
ETH y SOL corren en modo paper primero.**

6 Sensores: Bollinger Bands + RSI + ATR + EMA 1h + Volumen + ADX  
Capital sugerido: $300 USDT | Apalancamiento: 3x | Riesgo base: 0.5%/trade × risk_weight

---

## Estructura del proyecto

```
ezbot/
├── bot.py              # Bot principal (loop de trading)
├── backtest.py         # Backtest 6 meses con datos reales de Bybit
├── trailing.py         # Monitor de trailing stop (corre en paralelo)
├── dashboard.py        # Dashboard en consola (Rich)
├── requirements.txt    # Dependencias
├── .env                # Credenciales (no subir a git)
├── data/
│   ├── trades.csv          # Registro de operaciones del bot
│   ├── backtest_results.csv  # Operaciones simuladas del backtest
│   └── equity_curve.png    # Gráfico generado por el backtest
└── logs/
    ├── ezbot_YYYYMMDD.log  # Log del bot principal
    ├── trailing.log        # Log del monitor de trailing
    └── dashboard.log       # Log del dashboard
```

---

## Instalación

### 1. Clonar o descargar el proyecto

```bash
git clone https://github.com/ezequielsack/ezbot
cd ezbot
```

### 2. Crear entorno virtual

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / Mac
source venv/bin/activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar credenciales

Crear el archivo `.env` en la raíz del proyecto:

```env
BYBIT_API_KEY=tu_api_key_aqui
BYBIT_API_SECRET=tu_api_secret_aqui
TESTNET=true
```

> Para empezar en **testnet** (sin dinero real), usá `TESTNET=true`.  
> Para operar en **mainnet** con dinero real, cambiá a `TESTNET=false`.

Obtener API keys en: https://testnet.bybit.com (testnet) o https://bybit.com (mainnet)

---

## Uso

### Backtest (siempre primero)

Valida la estrategia antes de arriesgar capital real:

```bash
python backtest.py
```

- Descarga ~6 meses de datos de Bybit (endpoint público, sin API key)
- Simula todas las operaciones con los 6 sensores
- Genera `data/equity_curve.png` y `data/backtest_results.csv`
- Tarda ~5-10 minutos (descarga ~50.000 velas por par)

### Bot principal

```bash
python bot.py
```

### Trailing stop (en otra terminal, simultáneo al bot)

```bash
python trailing.py
```

### Dashboard (en otra terminal)

```bash
python dashboard.py
```

---

## Los 6 Sensores

| # | Sensor | Parámetro | Rol |
|---|--------|-----------|-----|
| 1 | Bollinger Bands | Period=20, Std=2.0 | Detecta extremos del rango |
| 2 | RSI | Period=14, OB=65, OS=35 | Temperatura del mercado |
| 3 | ATR | Period=14, SL=1.5×, TP=3.0× | Calcula SL y TP automáticos |
| 4 | EMA 1h | Period=50 | Filtra la tendencia mayor |
| 5 | Volumen | Period=20, Mult=1.2× | Confirma participación real |
| 6 | ADX | Period=14, Umbral=25 | Mide fuerza de tendencia |

### Lógica de señal

**LONG:** precio cruza BB inferior + RSI ≤ 35 + EMA 1h alcista + volumen OK  
**SHORT:** precio cruza BB superior + RSI ≥ 65 + EMA 1h bajista + volumen OK  
**ADX > 25:** posición reducida al 50% (modo tormenta)

---

## Trailing Stop (trailing.py)

| Avance desde entrada | Acción |
|----------------------|--------|
| ≥ 0.5 × ATR | SL se mueve al breakeven (precio de entrada) |
| ≥ 1.0 × ATR | SL se mueve a +0.5×ATR de ganancia |

El trailing solo avanza, nunca retrocede.

---

## Gestión de riesgo

- Riesgo por operación: 2% del capital
- Stop Loss automático: 1.5 × ATR
- Take Profit automático: 3.0 × ATR (ratio 1:2)
- Una sola posición abierta por par a la vez
- Verificación de tendencia en 1h antes de entrar

---

## Disclaimer

> Este bot es educativo. El trading en futuros tiene riesgo real de pérdida total del capital.  
> Usá solo lo que puedas perder. No es asesoramiento financiero.
