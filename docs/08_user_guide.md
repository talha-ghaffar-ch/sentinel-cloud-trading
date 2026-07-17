# 08 — User Guide

## Client Journey

### Step 1 — Registration

Navigate to the platform URL and click **"Create one"** on the login page.

Fill in:
- Full name
- Email address
- Password (min 8 characters, 1 uppercase, 1 number)
- Confirm password

On success, you are redirected to the login page. Log in with your credentials.

---

### Step 2 — Dashboard

After logging in, the dashboard shows:

| Panel | Content |
|---|---|
| Account Status | Current status (`PENDING`, `VERIFIED`, `SUSPENDED`) |
| Total Trades | Number of trades executed through your terminal |
| Total Profit | Cumulative profit/loss in USD |
| Win Rate | Percentage of winning trades |
| Service Application | Current application status and next steps |

---

### Step 3 — Submit Service Application

Click **"Submit Application"** on the dashboard.

The application form collects:

**Personal Information:**
- Full name, phone number, residential address
- Source of income (e.g. Employment, Business)
- Reason for using the service

**MT5 Terminal Credentials:**
- MT5 account login number
- MT5 account password
- Broker server name (e.g. `MetaQuotes-Demo`, `ICMarkets-Live`)

**Terms and Conditions:**
- Must accept before submitting

After submission, status shows `PENDING`. The administrator reviews your application and either approves or rejects it within 24–48 hours.

---

### Step 4 — Verification & Terminal Access

When approved, your account status changes to `VERIFIED` and the **Terminal** link appears in the navigation bar. The admin has configured your dedicated trading node using the MT5 credentials you provided.

---

### Step 5 — Trading Terminal

The terminal page updates every second with live data from your trading engine.

#### Layout Overview

**Header Bar**
- System name and target asset (XAU/USD)
- Live uptime counter
- Real-time clock

**Performance Metrics Panel**
- Base capital, live balance, session P&L, open P&L
- Total trades, win rate, W/L ratio, peak balance

**Market Radar Panel**
- Live bid/ask prices and spread
- ATR volatility indicator
- Current position type and lot size

**Algorithmic Scanner Panel**
- Trend direction (BULLISH / BEARISH / NEUTRAL)
- EMA Delta, MACD/Signal line, RSI momentum bar
- AI signal badge (BUY / SELL / WAIT) with confidence percentage
- High-probability signal indicator (highlights when a strong setup is detected)
- System status text

**Live Chart**
- Real-time tick price trajectory (last 150 ticks)
- Dynamically scaled Y-axis

**Risk Node & Controls**
- Drawdown bar (colour-coded: green < 4%, yellow < 7%, red > 7%)
- Current drawdown percentage and system mode
- ARM / HALT trading button
- PANIC CLOSE and BYPASS COOLDOWN buttons
- REBOOT button (appears only when circuit breaker trips)

**System Logs**
- Last 25 engine log entries in real time

**AI Core Operations**
- Ticks processed and inference latency

**Trade History**
- Last 15 trades with type, timestamps, duration, and profit/loss

**Traffic Lights**
- CLOSED (red) — weekend, market closed
- POOR VOL (yellow) — low liquidity hours
- PERFECT (green) — peak trading hours (London/NY session)

**Control Buttons**
- CB: ON/OFF — circuit breaker toggle
- EMERGENCY STOP — closes all positions and terminates engine

---

### Step 6 — Arming the System

By default the system starts **DISARMED**. To enable automatic trade execution:

1. Click **"RESUME SCANNING (ARM SYSTEM)"**
2. Button turns yellow: **"HALT NEW TRADES (DISARM SYSTEM)"**
3. The engine will now execute trades automatically when AI conditions are met

To halt trading without closing open positions:
- Click **"HALT NEW TRADES"** — the engine stops opening new positions but manages existing ones

---

### Step 7 — Manual Commands

| Button | When to Use |
|---|---|
| **CLOSE ALL** | Closes all positions immediately (market order) |
| **BYPASS COOLDOWN** | Skip the post-trade wait period |
| **EMERGENCY STOP** | Extreme emergency — closes everything and shuts down engine |
| **REBOOT** | After circuit breaker trips — reset drawdown reference to resume |

---

### Step 8 — Trade History

Click **"History"** in the navigation to view your complete trade ledger pulled from PostgreSQL:

- Deal ticket, trade type (LONG/SHORT), lot size
- Open and close timestamps, duration
- Entry and exit prices
- Net profit/loss in USD
- AI confidence score at time of signal
- Drawdown percentage when trade was opened

---

## Admin Guide

### Reviewing Applications

1. Navigate to **Admin** in the top navigation
2. Applications are listed with applicant name, email, MT5 login, MT5 password (hidden by default), and server
3. Click **SHOW** next to the password field to reveal the MT5 password
4. Click **Approve** to grant terminal access — this simultaneously sets `applications.status = 'verified'` and `users.account_status = 'verified'`
5. Click **Reject** and enter a reason — the user can re-apply after rejection

### After Approving

1. Note the user's **User ID** shown in the users table (e.g. `user_a3f8c2`)
2. Note their MT5 login, password, and server from the application
3. On the Windows EC2:
   - Open the MT5 terminal for that user's node
   - Log in with their MT5 credentials
   - Update `start_sessions.bat` with `--user user_a3f8c2`
   - Restart the trading engine for that node

### Managing Users

- **Suspend** — disables account access without deleting data
- **User ID** — the key linking the web account to the trading engine; copy this when configuring new nodes

### Filtering Applications

Use the filter buttons above the table: **All / Pending / Under Review / Verified / Rejected**
