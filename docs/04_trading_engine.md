# 04 — Trading Engine

## Overview

The trading engine is a headless Python process that runs as a Windows Service on the EC2 instance. It operates in a continuous 1-second loop, connecting MetaTrader 5 to AWS cloud services. Each user receives a dedicated, isolated engine process.

**Launch command:**
```
python trading_engine.py --user user_abc123 --path "C:\...\User_01\terminal64.exe"
```

---

## Module Architecture

```
trading_engine.py
│
├── AIEngine          — Data, indicators, ML model, signal prediction
├── RiskManager       — Mode, lot sizing, consecutive loss tracking
├── TradingBot        — MT5 connection, orders, position management
└── CloudManager      — DynamoDB sync, RDS logging, command handling
```

---

## AIEngine

### Data Pipeline

On startup, the engine downloads 5,000 historical M1 (1-minute) candles from MT5:

```python
mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME_M1, 0, 5000)
```

### Technical Indicators

11 features are computed from OHLCV data using pandas:

| Feature | Computation |
|---|---|
| EMA(9) | 9-period exponential moving average of close |
| EMA(21) | 21-period exponential moving average of close |
| MACD | EMA(12) − EMA(26) |
| Signal Line | 9-period EMA of MACD |
| RSI(14) | Relative Strength Index over 14 periods |
| ATR(14) | Average True Range over 14 periods |
| Open, High, Low, Close, Volume | Raw OHLCV values |

**EMA Delta** (EMA9 − EMA21) determines trend direction:
- Positive → `BULLISH`
- Negative → `BEARISH`

### Model Training

A `HistGradientBoostingClassifier` (scikit-learn) is trained on startup:

```python
model = HistGradientBoostingClassifier(
    max_iter=100,
    learning_rate=0.1,
    random_state=42
)
```

**Target variable:** Binary — whether the next candle's close is higher than the current close.
**Train/test split:** 80/20, no shuffle (preserves temporal order)
**Typical accuracy:** 68–75% on test set

`HistGradientBoostingClassifier` was chosen over standard GBT because it handles missing values natively, trains significantly faster on large datasets, and produces well-calibrated probability estimates needed for the confidence threshold system.

### Prediction

On each tick, the model predicts against the latest 100 candles:

```python
prob = model.predict_proba(latest_features)[0]
conf_up   = prob[1]   # probability of price going up
conf_down = prob[0]   # probability of price going down

if   conf_up   > SIGNAL_CONFIDENCE_MIN → signal = "BUY",  confidence = conf_up
elif conf_down > SIGNAL_CONFIDENCE_MIN → signal = "SELL", confidence = conf_down
else                                   → signal = "WAIT", confidence = max(conf_up, conf_down)
```

> **Note:** `SIGNAL_CONFIDENCE_MIN` and the other strategy thresholds below are
> configurable via environment variables. The production-tuned values are
> proprietary and withheld from this public reference — the in-code defaults are
> neutral placeholders (see the notice at the top of `trading_engine.py`).

---

## RiskManager

### Operating Modes

| Mode | Trigger | Lot Size | Min Confidence |
|---|---|---|---|
| `NORMAL` | Default / after a win | `BASE_LOT` | `SIGNAL_CONFIDENCE_MIN` |
| `RECOVERY` | After any loss | `BASE_LOT × RECOVERY_MULTIPLIER^losses` (capped) | `SIGNAL_CONFIDENCE_RECOVERY` |

### Recovery Lot Calculation

```python
lot = min(
    BASE_LOT * (RECOVERY_MULTIPLIER ** consecutive_losses),
    BASE_LOT * RECOVERY_LOT_CAP_MULT   # hard cap
)
```

The scaling multiplier and cap are configurable via environment variables; the
production-tuned values are proprietary and are not published here.

### Win Rate Tracking

```python
win_rate = wins / total_trades
```

Updated after every closed trade and synced to DynamoDB for dashboard display.

---

## TradingBot

### MT5 Connection

```python
mt5.initialize(path=mt5_exe_path, portable=True)
```

`portable=True` allows multiple isolated terminal instances on the same machine without them interfering. The terminal must already be logged in with the user's credentials before the engine starts.

### Order Execution

All orders use ATR-based Stop Loss and Take Profit:

```
BUY order:
  entry = ask price
  SL    = entry - (ATR × ATR_SL_MULTIPLIER)
  TP    = entry + (ATR × ATR_SL_MULTIPLIER × RISK_REWARD)

SELL order:
  entry = bid price
  SL    = entry + (ATR × ATR_SL_MULTIPLIER)
  TP    = entry - (ATR × ATR_SL_MULTIPLIER × RISK_REWARD)
```

`ATR_SL_MULTIPLIER` and `RISK_REWARD` are configurable; production-tuned values
are proprietary and withheld from this public reference.

Order parameters:
```python
{
    "action":       TRADE_ACTION_DEAL,
    "symbol":       SYMBOL,
    "volume":       lot_size,
    "type":         ORDER_TYPE_BUY or ORDER_TYPE_SELL,
    "price":        current_price,
    "sl":           stop_loss,
    "tp":           take_profit,
    "deviation":    20,          # max slippage in points
    "magic":        777999,      # unique identifier for this engine's orders
    "type_filling": ORDER_FILLING_IOC
}
```

### Auto-Execution Conditions

A new position is only opened when **all** conditions are met simultaneously:

```
✓ No existing open position (MAX_TRADES = 1)
✓ trading_enabled = True (system is ARMED)
✓ cooldown_remaining = 0 (post-trade cooldown elapsed)
✓ circuit_breaker_tripped = False
✓ Signal = BUY  AND confidence ≥ min_conf AND trend = BULLISH
  OR
  Signal = SELL AND confidence ≥ min_conf AND trend = BEARISH
```

### Circuit Breaker

```python
drawdown_pct = (peak_balance - equity) / peak_balance × 100

if drawdown_pct >= MAX_DAILY_DRAWDOWN (default: 10%):
    close_all_positions()
    trading_enabled = False
    circuit_breaker_tripped = True
    status = "LOCKED: DD LIMIT HIT"
```

The system can only be resumed by the admin/user sending a `REBOOT` command from the web terminal, which resets the peak balance reference point.

### Post-Trade Cooldown

After any position closes, a configurable cooldown period (default: 180 seconds) prevents immediate re-entry. This prevents overtrading in volatile conditions. The cooldown can be bypassed via the `BYPASS` command.

---

## CloudManager

### DynamoDB Sync Cycle (every ~1 second)

**Step 1 — Read commands:**
```python
item = live_table.get_item(Key={"user_id": self.user_id})
command = item["COMMAND_QUEUE"]  # e.g. "TOGGLE_TRADE"
```

**Step 2 — Clear command queue:**
```python
live_table.update_item(
    Key={"user_id": self.user_id},
    UpdateExpression="SET COMMAND_QUEUE = :v",
    ExpressionAttributeValues={":v": "NONE"}
)
```

**Step 3 — Write full state:**
```python
live_table.put_item(Item={
    "user_id": ...,
    "last_updated": Decimal(str(time.time())),
    "system_status": {...},
    "performance_metrics": {...},
    "algo_scanner": {...},
    "ai_core": {...},
    "ui_arrays": {
        "logs": [...],         # last 25 log entries
        "trade_history": [...],# last 15 trades
        "live_prices": [...],  # last 150 tick prices
        "live_times": [...]    # corresponding timestamps
    }
})
```

All float values are converted to `Decimal` before writing (DynamoDB requirement).

### RDS Trade Logging

When the engine detects a newly closed position:

```python
INSERT INTO trade_history (
    deal_ticket, user_id, symbol, trade_type, lot_size,
    open_time, close_time, duration_interval, open_price,
    close_price, profit_usd, drawdown_at_execution, ai_confidence_score
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (deal_ticket) DO NOTHING;
```

---

## Command Reference

| Command | Effect |
|---|---|
| `TOGGLE_TRADE` | Arms or disarms the auto-trading system |
| `CLOSE_ALL` | Immediately closes all open positions |
| `EMERGENCY_STOP` | Closes all positions and terminates the engine process |
| `BYPASS` | Reset cooldown timer to 0 immediately |
| `TOGGLE_CB` | Enable or disable the circuit breaker |
| `REBOOT` | Reset peak balance reference and clear circuit breaker trip flag |

---

## Engine Configuration

All parameters are loaded from environment variables:

| Variable | Default | Description |
|---|---|---|
| `TRADING_SYMBOL` | `XAUUSDm` | MT5 symbol to trade |
| `MAGIC_NUMBER` | `777999` | Identifies this engine's orders in MT5 |
| `BASE_LOT` | `0.01` | Starting lot size |
| `MAX_DAILY_DRAWDOWN` | `10.0` | % drawdown that trips circuit breaker |
| `COOLDOWN_SECONDS` | `180` | Post-trade wait time |

### Strategy parameters (proprietary)

These control the strategy's edge. The defaults below are **neutral placeholders**;
the production-tuned values are proprietary and are set via environment variables in
a private deployment.

| Variable | Placeholder default | Description |
|---|---|---|
| `SIGNAL_CONFIDENCE_MIN` | `0.60` | Min model confidence to open a trade (NORMAL) |
| `SIGNAL_CONFIDENCE_RECOVERY` | `0.70` | Min confidence in RECOVERY mode |
| `RECOVERY_MULTIPLIER` | `1.0` | Per-loss lot scaling factor in RECOVERY mode |
| `RECOVERY_LOT_CAP_MULT` | `3.0` | Hard cap on recovery lot as a multiple of `BASE_LOT` |
| `RISK_REWARD` | `2.0` | Take-profit to stop-loss ratio |
| `ATR_SL_MULTIPLIER` | `1.5` | ATR multiple used for stop-loss distance |

---

## Performance Metrics Tracked

| Metric | Source |
|---|---|
| Balance, Equity | `mt5.account_info()` |
| Peak Balance | Tracked in memory, updated when balance exceeds previous peak |
| Drawdown % | `(peak - equity) / peak × 100` |
| Session P&L | `equity - start_balance` |
| Open P&L | Sum of `profit` across all open positions |
| Win Rate | `wins / total_trades` |
| Ticks Processed | Incremented each main loop iteration |
| Inference Latency | `time.perf_counter()` delta around `ai.predict()` call |
