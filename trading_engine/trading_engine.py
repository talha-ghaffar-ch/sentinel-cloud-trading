"""
trading_engine.py — Sentinel Cloud Trading — Headless Trading Node

A per-user, headless trading process that runs on a Windows host alongside a
MetaTrader 5 terminal. It trains a gradient-boosting classifier on recent price
action, generates BUY/SELL/WAIT signals, manages risk, executes orders through
the MT5 API, and syncs live state + completed trades to AWS (DynamoDB + RDS).

Modules:
    AIEngine      — feature engineering + model training + inference
    RiskManager   — win/loss streak tracking, position sizing, recovery mode
    TradingBot    — MT5 connection, order execution, main trading loop
    CloudManager  — DynamoDB state sync, RDS trade ledger, notifications

────────────────────────────────────────────────────────────────────────────
PROPRIETARY STRATEGY NOTICE
    The production-tuned strategy parameters — signal-confidence gates,
    recovery-mode position sizing, and ATR-based stop/target multipliers —
    constitute the proprietary edge of this system and are intentionally
    withheld from this public reference. Every such value is sourced from an
    environment variable; the in-code defaults are neutral, conservative
    placeholders that keep the engine fully runnable for demonstration.
    See the "STRATEGY PARAMETERS" block below.
────────────────────────────────────────────────────────────────────────────
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
import datetime
import argparse
import boto3
import psycopg2
import os
from decimal import Decimal
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

# Load .env file if present (pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # On EC2 with OS-level env vars this is fine

# ==========================================
# CONFIGURATION — ALL FROM ENVIRONMENT
# ==========================================
SYMBOL              = os.environ.get("TRADING_SYMBOL",      "XAUUSDm")
TIMEFRAME           = mt5.TIMEFRAME_M1
MAGIC_NUMBER        = int(os.environ.get("MAGIC_NUMBER",    "777999"))
MAX_TRADES          = 1
TRAIN_BARS          = 5000
BASE_LOT            = float(os.environ.get("BASE_LOT",      "0.01"))
COOLDOWN_SECONDS    = int(os.environ.get("COOLDOWN_SECONDS","180"))
MAX_DAILY_DRAWDOWN  = float(os.environ.get("MAX_DAILY_DRAWDOWN", "10.0"))

# ==========================================
# STRATEGY PARAMETERS  (proprietary — see notice at top of file)
# ------------------------------------------
# The production-tuned values are withheld from this public reference.
# The defaults below are neutral placeholders; set the real, tuned values
# via environment variables in a private deployment.
# ==========================================
RECOVERY_MULTIPLIER        = float(os.environ.get("RECOVERY_MULTIPLIER",        "1.0"))   # placeholder
RISK_REWARD                = float(os.environ.get("RISK_REWARD",                "2.0"))   # placeholder
SIGNAL_CONFIDENCE_MIN      = float(os.environ.get("SIGNAL_CONFIDENCE_MIN",      "0.60"))  # placeholder
SIGNAL_CONFIDENCE_RECOVERY = float(os.environ.get("SIGNAL_CONFIDENCE_RECOVERY", "0.70"))  # placeholder
RECOVERY_LOT_CAP_MULT      = float(os.environ.get("RECOVERY_LOT_CAP_MULT",      "3.0"))   # placeholder
ATR_SL_MULTIPLIER          = float(os.environ.get("ATR_SL_MULTIPLIER",          "1.5"))   # placeholder

# ── AWS ───────────────────────────────────────────────────────
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

DYNAMO_LIVE_STATE_TABLE   = os.environ.get("DYNAMO_LIVE_STATE_TABLE",   "Sentinel_Live_State")
DYNAMO_COMMANDS_TABLE     = os.environ.get("DYNAMO_COMMANDS_TABLE",     "Sentinel_Commands")
DYNAMO_NOTIFICATIONS_TABLE= os.environ.get("DYNAMO_NOTIFICATIONS_TABLE","Sentinel_Notifications")

# ── RDS POSTGRESQL — loaded from environment, never hardcoded ─
RDS_HOST     = os.environ.get("RDS_HOST",     "")
RDS_NAME     = os.environ.get("RDS_NAME",     "sentinel")
RDS_USER     = os.environ.get("RDS_USER",     "sentinel_app")
RDS_PASSWORD = os.environ.get("RDS_PASSWORD", "")
RDS_PORT     = os.environ.get("RDS_PORT",     "5432")

if not RDS_HOST or not RDS_PASSWORD:
    raise EnvironmentError(
        "RDS_HOST and RDS_PASSWORD must be set as environment variables. "
        "Copy .env.template to .env and fill in credentials."
    )

# ==========================================
# GLOBAL STATE & LOGGING
# ==========================================
bot_state = {
    "start_time": time.time(),
    "status": "INITIALIZING SYSTEM...",
    "start_balance": 0.0,
    "balance": 0.0,
    "equity": 0.0,
    "peak_balance": 0.0,
    "pnl": 0.0,
    "open_pnl": 0.0,
    "drawdown": 0.0,
    "mode": "NORMAL",
    "last_signal": "WAIT",
    "confidence": 0.0,
    "win_rate": 0.0,
    "wins": 0,
    "losses": 0,
    "total_trades": 0,
    "active_trades_count": 0,
    "current_lot": BASE_LOT,
    "bid": 0.0,
    "ask": 0.0,
    "spread": 0,
    "atr": 0.0,
    "ema_delta": 0.0,
    "momentum": 50.0,
    "macd": 0.0,
    "signal_line": 0.0,
    "trend": "NEUTRAL",
    "logs": [],
    "manual_command": None,
    "current_position_type": "NONE",
    "trading_enabled": False,
    "last_trade_close_time": 0.0,
    "cooldown_remaining": 0,
    "active_trade_confidence": 0.0,
    "high_prob_signal": None,
    "high_prob_conf": 0.0,
    "ticks_processed": 0,
    "inference_ms": 0.0,
    "trade_history": [],
    "processed_deal_tickets": [],
    "circuit_breaker_tripped": False,
    "circuit_breaker_enabled": True
}

def log_event(msg):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    entry = f"[SYS] [{timestamp}] {msg}"
    print(entry)
    bot_state["logs"].insert(0, entry)
    if len(bot_state["logs"]) > 15:
        bot_state["logs"].pop()

# ==========================================
# AWS CLOUD SYNC MODULE
# ==========================================
class CloudManager:
    def __init__(self, user_id):
        self.user_id = user_id
        self.dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        self.live_table = self.dynamodb.Table(DYNAMO_LIVE_STATE_TABLE)
        self.cmd_table  = self.dynamodb.Table(DYNAMO_COMMANDS_TABLE)
        self._aws_error_count = 0  # Track consecutive failures
        log_event(f"AWS Link Established. Syncing to DynamoDB [{AWS_REGION}] as {self.user_id}.")

    def float_to_dec(self, val):
        return Decimal(str(round(val, 5)))

    def log_trade_to_rds(self, deal_ticket, symbol, trade_type, lot_size,
                         open_time, close_time, duration, open_price,
                         close_price, profit, drawdown, ai_conf):
        try:
            conn = psycopg2.connect(
                host=RDS_HOST, database=RDS_NAME,
                user=RDS_USER, password=RDS_PASSWORD, port=RDS_PORT,
                connect_timeout=10
            )
            conn.autocommit = True
            cursor = conn.cursor()
            query = """
            INSERT INTO trade_history (
                deal_ticket, user_id, symbol, trade_type, lot_size,
                open_time, close_time, duration_interval, open_price,
                close_price, profit_usd, drawdown_at_execution, ai_confidence_score
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (deal_ticket) DO NOTHING;
            """
            cursor.execute(query, (
                deal_ticket, self.user_id, symbol, trade_type, lot_size,
                open_time, close_time, duration, open_price,
                close_price, profit, drawdown, ai_conf
            ))
            cursor.close()
            conn.close()
            log_event(f"RDS LEDGER: Trade {deal_ticket} permanently secured in AWS.")
        except Exception as e:
            log_event(f"RDS LOGGING ERROR: {e}")

    def _push_notification(self, ntype: str, message: str):
        """Push an alert to Sentinel_Notifications for the web dashboard to display."""
        try:
            now_ms = int(time.time() * 1000)
            self.cmd_table  # just to keep reference; use notifications table
            notif_table = self.dynamodb.Table(DYNAMO_NOTIFICATIONS_TABLE)
            notif_table.put_item(Item={
                "user_id":    self.user_id,
                "created_at": now_ms,
                "type":       ntype,
                "message":    message,
                "read":       False,
                "ttl":        int(time.time()) + (7 * 86400)  # 7-day TTL
            })
        except Exception:
            pass  # Notification failure must never halt trading

    def sync(self):
        try:
            # 1. Read commands from cloud
            response = self.live_table.get_item(Key={'user_id': self.user_id})
            if 'Item' in response:
                cloud_cmd = response['Item'].get('COMMAND_QUEUE', 'NONE')
                if cloud_cmd != 'NONE':
                    bot_state['manual_command'] = cloud_cmd
                    log_event(f"CLOUD COMMAND RECEIVED: {cloud_cmd}")

                    # Archive command to Sentinel_Commands for audit trail
                    try:
                        self.cmd_table.put_item(Item={
                            "user_id":   self.user_id,
                            "timestamp": int(time.time() * 1000),
                            "command":   cloud_cmd,
                            "ttl":       int(time.time()) + (30 * 86400)
                        })
                    except Exception:
                        pass

                    self.live_table.update_item(
                        Key={'user_id': self.user_id},
                        UpdateExpression="SET COMMAND_QUEUE = :val",
                        ExpressionAttributeValues={':val': 'NONE'}
                    )

            # 2. Push state to cloud
            payload = {
                'user_id': self.user_id,
                'last_updated': self.float_to_dec(time.time()),
                'COMMAND_QUEUE': 'NONE',
                'system_status': {
                    'status_text':             bot_state['status'],
                    'trading_enabled':         bot_state['trading_enabled'],
                    'circuit_breaker_enabled': bot_state['circuit_breaker_enabled'],
                    'circuit_breaker_tripped': bot_state['circuit_breaker_tripped'],
                    'mode':                    bot_state['mode'],
                    'cooldown_remaining_sec':  bot_state['cooldown_remaining']
                },
                'performance_metrics': {
                    'start_balance':      self.float_to_dec(bot_state['start_balance']),
                    'live_balance':       self.float_to_dec(bot_state['balance']),
                    'equity':             self.float_to_dec(bot_state['equity']),
                    'peak_balance':       self.float_to_dec(bot_state['peak_balance']),
                    'session_pnl':        self.float_to_dec(bot_state['pnl']),
                    'open_pnl':           self.float_to_dec(bot_state['open_pnl']),
                    'drawdown_pct':       self.float_to_dec(bot_state['drawdown']),
                    'active_trades_count':bot_state['active_trades_count'],
                    'total_trades':       bot_state['total_trades'],
                    'win_rate':           self.float_to_dec(bot_state['win_rate']),
                    'wins':               bot_state['wins'],
                    'losses':             bot_state['losses']
                },
                'algo_scanner': {
                    'trend_vector':          bot_state['trend'],
                    'ema_delta':             self.float_to_dec(bot_state['ema_delta']),
                    'macd':                  self.float_to_dec(bot_state['macd']),
                    'momentum_rsi':          self.float_to_dec(bot_state['momentum']),
                    'ai_signal':             bot_state['last_signal'],
                    'ai_confidence':         self.float_to_dec(bot_state['confidence']),
                    'current_position_type': bot_state['current_position_type'],
                    'high_prob_signal':      bot_state['high_prob_signal'] if bot_state['high_prob_signal'] else "NONE"
                },
                'ui_arrays': {
                    'logs':          bot_state['logs'],
                    'trade_history': bot_state['trade_history']
                }
            }
            self.live_table.put_item(Item=payload)
            self._aws_error_count = 0  # Reset on success

            # Push notification if circuit breaker just tripped
            if bot_state['circuit_breaker_tripped']:
                self._push_notification(
                    "CIRCUIT_BREAKER",
                    f"Circuit breaker tripped at {bot_state['drawdown']:.2f}% drawdown. Trading halted."
                )

        except Exception as e:
            self._aws_error_count += 1
            log_event(f"AWS SYNC ERROR (#{self._aws_error_count}): {e}")
            # After 10 consecutive failures, log clearly (used to fail silently)
            if self._aws_error_count >= 10:
                log_event("WARNING: DynamoDB unreachable for 10+ cycles. Dashboard data is stale.")

# ==========================================
# MODULE 1: AI & DATA PROCESSING
# ==========================================
class AIEngine:
    def __init__(self):
        self.model = HistGradientBoostingClassifier(max_iter=100, learning_rate=0.1, random_state=42)
        self.is_trained = False

    def add_indicators(self, df):
        df = df.copy()
        df['EMA_9']  = df['close'].ewm(span=9,  adjust=False).mean()
        df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD']        = exp1 - exp2
        df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
        delta = df['close'].diff()
        gain  = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs    = gain / loss
        df['RSI']    = 100 - (100 / (1 + rs))
        high_low     = df['high'] - df['low']
        high_close   = np.abs(df['high'] - df['close'].shift())
        low_close    = np.abs(df['low']  - df['close'].shift())
        ranges       = pd.concat([high_low, high_close, low_close], axis=1)
        df['ATR']    = ranges.max(axis=1).rolling(14).mean()
        df['Target'] = (df['close'].shift(-1) > df['close']).astype(int)
        return df.dropna()

    def train(self, historical_data):
        df = self.add_indicators(historical_data)
        features = ['open', 'high', 'low', 'close', 'tick_volume',
                    'EMA_9', 'EMA_21', 'MACD', 'Signal_Line', 'RSI', 'ATR']
        X = df[features]
        y = df['Target']
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
        self.model.fit(X_train, y_train)
        acc = accuracy_score(y_test, self.model.predict(X_test))
        log_event(f"AI Core Online. Accuracy: {acc:.2%}")
        self.is_trained = True

    def predict(self, current_data):
        fallback = {"sig": "WAIT", "conf": 0.0, "atr": 0.0, "ema_d": 0.0,
                    "mom": 50.0, "trend": "NEUTRAL", "macd": 0.0, "sig_line": 0.0}
        if not self.is_trained: return fallback
        df = self.add_indicators(current_data)
        if len(df) == 0: return fallback
        latest   = df.iloc[-1:]
        features = ['open', 'high', 'low', 'close', 'tick_volume',
                    'EMA_9', 'EMA_21', 'MACD', 'Signal_Line', 'RSI', 'ATR']
        prob     = self.model.predict_proba(latest[features])[0]
        inds = {
            "atr":     latest['ATR'].values[0],
            "ema_d":   latest['EMA_9'].values[0] - latest['EMA_21'].values[0],
            "mom":     latest['RSI'].values[0],
            "macd":    latest['MACD'].values[0],
            "sig_line":latest['Signal_Line'].values[0]
        }
        inds["trend"] = "BULLISH" if inds["ema_d"] > 0 else "BEARISH"
        conf_up, conf_down = prob[1], prob[0]
        if conf_up > SIGNAL_CONFIDENCE_MIN:
            inds["sig"], inds["conf"] = "BUY",  conf_up
        elif conf_down > SIGNAL_CONFIDENCE_MIN:
            inds["sig"], inds["conf"] = "SELL", conf_down
        else:
            inds["sig"], inds["conf"] = "WAIT", max(conf_up, conf_down)
        return inds

# ==========================================
# MODULE 2: RISK MANAGEMENT
# ==========================================
class RiskManager:
    def __init__(self):
        self.mode = "NORMAL"
        self.consecutive_losses = 0

    def evaluate_last_trade(self, profit):
        if profit >= 0:
            self.mode = "NORMAL"
            self.consecutive_losses = 0
            bot_state['wins'] += 1
            log_event(f"TRADE CLOSED: PROFIT +${profit:.2f}. Streak reset.")
        else:
            self.mode = "RECOVERY"
            self.consecutive_losses += 1
            bot_state['losses'] += 1
            log_event(f"TRADE CLOSED: LOSS -${abs(profit):.2f}. Entering Recovery Protocol.")
        bot_state['total_trades'] += 1
        bot_state['win_rate'] = bot_state['wins'] / bot_state['total_trades']
        bot_state['mode']     = self.mode

    def get_trade_parameters(self):
        lot_size       = BASE_LOT
        min_confidence = SIGNAL_CONFIDENCE_MIN
        if self.mode == "RECOVERY":
            lot_size = min(
                round(BASE_LOT * (RECOVERY_MULTIPLIER ** max(1, self.consecutive_losses)), 2),
                BASE_LOT * RECOVERY_LOT_CAP_MULT
            )
            min_confidence = SIGNAL_CONFIDENCE_RECOVERY
        bot_state['current_lot'] = lot_size
        return lot_size, min_confidence

# ==========================================
# MODULE 3: MT5 TRADING BOT ENGINE
# ==========================================
class TradingBot:
    def __init__(self, mt5_path=None, user_id="user_01"):
        self.mt5_path  = mt5_path
        self.user_id   = user_id
        self.ai        = AIEngine()
        self.risk      = RiskManager()
        self.cloud     = CloudManager(user_id)
        self.is_running= False

    def connect(self):
        log_event(f"INITIALIZING TERMINAL AT: {self.mt5_path}...")
        authorized = mt5.initialize(path=self.mt5_path, portable=True) if self.mt5_path else mt5.initialize()
        if not authorized:
            log_event("FATAL: MT5 initialization failed")
            mt5.shutdown()
            return False
        acc = mt5.account_info()
        if acc is None:
            log_event("FATAL: Failed to retrieve account info.")
            mt5.shutdown()
            return False
        log_event(f"LINK ESTABLISHED. Target: {SYMBOL} | Acc: {acc.login}")
        bot_state['start_balance'] = acc.balance
        bot_state['peak_balance']  = acc.balance
        return True

    def get_data(self, num_bars):
        rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, num_bars)
        if rates is None: return pd.DataFrame()
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df

    def place_order(self, order_type, lot, price, sl, tp):
        req_type = "LONG" if order_type == mt5.ORDER_TYPE_BUY else "SHORT"
        request  = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       SYMBOL,
            "volume":       float(lot),
            "type":         order_type,
            "price":        price,
            "sl":           sl,
            "tp":           tp,
            "deviation":    20,
            "magic":        MAGIC_NUMBER,
            "comment":      "AI_" + self.risk.mode,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log_event(f"EXECUTION FAILED: {result.comment}")
            return False
        log_event(f"EXECUTION CONFIRMED: {req_type} {lot} lots @ {price:.2f}")
        return True

    def close_all_positions(self):
        positions = mt5.positions_get(symbol=SYMBOL)
        if not positions: return
        for pos in positions:
            tick       = mt5.symbol_info_tick(SYMBOL)
            order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price      = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask
            request    = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       SYMBOL,
                "volume":       pos.volume,
                "type":         order_type,
                "position":     pos.ticket,
                "price":        price,
                "deviation":    20,
                "magic":        MAGIC_NUMBER,
                "comment":      "MANUAL_OVERRIDE",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            res = mt5.order_send(request)
            if res.retcode == mt5.TRADE_RETCODE_DONE:
                log_event(f"OVERRIDE SUCCESS: Position {pos.ticket} Closed.")

    def run_live(self):
        if not self.connect(): return
        self.is_running = True
        log_event("DOWNLOADING DATA...")
        hist_data = self.get_data(TRAIN_BARS)
        if hist_data.empty:
            log_event("FATAL: No data found. Check symbol name.")
            return
        self.ai.train(hist_data)
        bot_state['status'] = "SCANNING MARKET (ACTIVE)"
        last_positions = 0

        while self.is_running:
            try:
                acc_info = mt5.account_info()
                if acc_info:
                    bot_state['balance'] = acc_info.balance
                    bot_state['equity']  = acc_info.equity
                    bot_state['pnl']     = acc_info.equity - bot_state['start_balance']
                    if acc_info.balance > bot_state['peak_balance']:
                        bot_state['peak_balance'] = acc_info.balance
                    dd_usd = bot_state['peak_balance'] - acc_info.equity
                    bot_state['drawdown'] = (dd_usd / bot_state['peak_balance']) * 100 if bot_state['peak_balance'] > 0 else 0

                    if (bot_state['circuit_breaker_enabled']
                            and bot_state['drawdown'] >= MAX_DAILY_DRAWDOWN
                            and bot_state['trading_enabled']):
                        log_event(f"CIRCUIT BREAKER TRIPPED! {bot_state['drawdown']:.2f}% DRAWDOWN.")
                        self.close_all_positions()
                        bot_state['trading_enabled']       = False
                        bot_state['status']                = "LOCKED: DD LIMIT HIT"
                        bot_state['circuit_breaker_tripped'] = True

                tick = mt5.symbol_info_tick(SYMBOL)
                if tick:
                    bot_state['bid'] = tick.bid
                    bot_state['ask'] = tick.ask

                positions    = mt5.positions_get(symbol=SYMBOL)
                num_positions= len(positions) if positions else 0
                bot_state['active_trades_count'] = num_positions

                if num_positions > 0:
                    bot_state['current_position_type'] = "LONG" if positions[0].type == mt5.ORDER_TYPE_BUY else "SHORT"
                    bot_state['open_pnl'] = sum([pos.profit for pos in positions])
                else:
                    bot_state['current_position_type'] = "NONE"
                    bot_state['open_pnl'] = 0.0

                if num_positions < last_positions:
                    bot_state['last_trade_close_time'] = time.time()
                    time.sleep(0.5)
                    deals = mt5.history_deals_get(
                        datetime.datetime.now() - datetime.timedelta(days=1),
                        datetime.datetime.now() + datetime.timedelta(days=1)
                    )
                    if deals:
                        for last_deal in [d for d in deals if d.entry == 1]:
                            if (last_deal.time > bot_state['start_time']
                                    and last_deal.ticket not in bot_state.get('processed_deal_tickets', [])):
                                bot_state.setdefault('processed_deal_tickets', []).append(last_deal.ticket)
                                self.risk.evaluate_last_trade(last_deal.profit)
                                pos_id   = last_deal.position_id
                                pos_deals= mt5.history_deals_get(position=pos_id)
                                c_dt     = datetime.datetime.fromtimestamp(last_deal.time)
                                o_dt     = c_dt
                                if pos_deals and len(pos_deals) >= 2:
                                    in_d       = pos_deals[0]
                                    o_dt       = datetime.datetime.fromtimestamp(in_d.time)
                                    t_type     = "LONG" if in_d.type == mt5.DEAL_TYPE_BUY else "SHORT"
                                    open_price = in_d.price
                                else:
                                    t_type     = "LONG" if last_deal.type == mt5.DEAL_TYPE_SELL else "SHORT"
                                    open_price = 0.0
                                dur = str(c_dt - o_dt).split('.')[0]
                                hist_entry = {
                                    "type": t_type, "profit": last_deal.profit,
                                    "open": o_dt.strftime("%H:%M:%S"),
                                    "close": c_dt.strftime("%H:%M:%S"), "dur": dur
                                }
                                bot_state['trade_history'].insert(0, hist_entry)
                                if len(bot_state['trade_history']) > 15:
                                    bot_state['trade_history'].pop()
                                self.cloud.log_trade_to_rds(
                                    deal_ticket=last_deal.ticket, symbol=SYMBOL,
                                    trade_type=t_type, lot_size=last_deal.volume,
                                    open_time=o_dt, close_time=c_dt,
                                    duration=str(c_dt - o_dt), open_price=open_price,
                                    close_price=last_deal.price, profit=last_deal.profit,
                                    drawdown=bot_state['drawdown'],
                                    ai_conf=bot_state['active_trade_confidence']
                                )

                last_positions = num_positions

                # Execute Remote Commands from DynamoDB
                cmd = bot_state.get('manual_command')
                if cmd == 'EMERGENCY_STOP':
                    self.close_all_positions()
                    bot_state['status'] = "SYSTEM TERMINATED"
                    self.is_running = False
                    mt5.shutdown()
                    break
                elif cmd == 'RESTART':
                    self.close_all_positions()
                    bot_state['status'] = "RESTARTING"
                elif cmd == 'CLOSE_ALL':
                    self.close_all_positions()
                elif cmd == 'TOGGLE_TRADE':
                    bot_state['trading_enabled'] = not bot_state['trading_enabled']
                elif cmd == 'TOGGLE_CB':
                    bot_state['circuit_breaker_enabled'] = not bot_state['circuit_breaker_enabled']
                elif cmd == 'BYPASS':
                    bot_state['last_trade_close_time'] = 0.0
                elif cmd == 'REBOOT':
                    bot_state['circuit_breaker_tripped'] = False
                    bot_state['drawdown']                = 0.0
                    bot_state['peak_balance']            = bot_state['balance']
                bot_state['manual_command'] = None

                time_since_close            = time.time() - bot_state['last_trade_close_time']
                bot_state['cooldown_remaining'] = int(COOLDOWN_SECONDS - time_since_close) if time_since_close < COOLDOWN_SECONDS else 0

                df   = self.get_data(100)
                inds = self.ai.predict(df)
                bot_state['last_signal'] = inds['sig']
                bot_state['confidence']  = inds['conf']
                bot_state['atr']         = inds['atr']
                bot_state['ema_delta']   = inds['ema_d']
                bot_state['momentum']    = inds['mom']
                bot_state['trend']       = inds['trend']
                bot_state['macd']        = inds['macd']

                if num_positions > 0 and inds['sig'] in ["BUY", "SELL"] and inds['conf'] >= SIGNAL_CONFIDENCE_RECOVERY:
                    bot_state['high_prob_signal'] = inds['sig']
                else:
                    bot_state['high_prob_signal'] = None

                if num_positions < MAX_TRADES and bot_state['trading_enabled'] and bot_state['cooldown_remaining'] == 0:
                    lot, min_conf = self.risk.get_trade_parameters()
                    if inds['sig'] == "BUY" and inds['conf'] >= min_conf and bot_state['trend'] == "BULLISH":
                        price = mt5.symbol_info_tick(SYMBOL).ask
                        self.place_order(mt5.ORDER_TYPE_BUY, lot, price,
                                         price - (inds['atr'] * ATR_SL_MULTIPLIER),
                                         price + (inds['atr'] * ATR_SL_MULTIPLIER * RISK_REWARD))
                    elif inds['sig'] == "SELL" and inds['conf'] >= min_conf and bot_state['trend'] == "BEARISH":
                        price = mt5.symbol_info_tick(SYMBOL).bid
                        self.place_order(mt5.ORDER_TYPE_SELL, lot, price,
                                         price + (inds['atr'] * ATR_SL_MULTIPLIER),
                                         price - (inds['atr'] * ATR_SL_MULTIPLIER * RISK_REWARD))

                # PUSH DATA TO CLOUD EVERY CYCLE
                self.cloud.sync()
                time.sleep(1)

            except Exception as e:
                log_event(f"SYSTEM ERROR: {e}")
                time.sleep(5)

# ==========================================
# EXECUTION ENTRY POINT
# ==========================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Headless Trading Node")
    parser.add_argument("--path", type=str, default=None, help="Path to terminal64.exe")
    parser.add_argument("--user", type=str, default="user_01", help="Database User ID mapping")
    args = parser.parse_args()

    print(f"BOOTING HEADLESS NODE FOR {args.user.upper()}...")
    bot = TradingBot(mt5_path=args.path, user_id=args.user)
    bot.run_live()
