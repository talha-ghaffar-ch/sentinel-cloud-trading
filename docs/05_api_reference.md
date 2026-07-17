# 05 — API Reference

## Authentication

All routes except `/login` and `/register` require an active Flask session (set at login). Session tokens are stored in signed, HTTP-only cookies.

The terminal and history pages additionally require `account_status = verified` in the session — enforced by the `@verified_required` decorator.

Admin routes require `role = admin` — enforced by the `@admin_required` decorator.

---

## Rate Limiting

| Endpoint | Limit |
|---|---|
| `POST /login` | 10 requests per minute per IP |
| `POST /register` | 5 requests per hour per IP |
| `GET /api/terminal/state` | 120 requests per minute |
| `POST /api/terminal/command` | 30 requests per minute |

---

## Page Routes

### `GET /`
Redirects to `/dashboard` if logged in, otherwise to `/login`.

---

### `GET /login` · `POST /login`

**GET:** Renders the login form.

**POST parameters:**

| Field | Type | Required |
|---|---|---|
| `email` | string | Yes |
| `password` | string | Yes |

**POST response:**
- Success → redirect to `/dashboard` (user) or `/admin` (admin)
- Failure → re-render login with flash message

---

### `GET /register` · `POST /register`

**POST parameters:**

| Field | Type | Validation |
|---|---|---|
| `full_name` | string | Required |
| `email` | string | Valid email format, not already registered |
| `password` | string | Min 8 chars, 1 uppercase, 1 number |
| `confirm_password` | string | Must match `password` |

**On success:** User record created, redirect to `/login`

---

### `GET /dashboard`
**Auth:** Login required

Returns the user's dashboard showing account status, application status, and trading statistics aggregated from `trade_history`.

---

### `GET /apply` · `POST /apply`
**Auth:** Login required

**GET:** Renders KYC application form. Redirects to dashboard if active application already exists.

**POST parameters:**

| Field | Required | Description |
|---|---|---|
| `full_name` | Yes | Legal name |
| `phone_number` | No | Contact number |
| `address` | No | Residential address |
| `income_source` | No | Source of trading capital |
| `reason_for_use` | Yes | Service justification |
| `mt5_login` | Yes | MT5 account number |
| `mt5_password` | Yes | MT5 account password |
| `mt5_server` | Yes | Broker server name |
| `terms_accepted` | Yes | Checkbox — must be checked |

---

### `GET /terminal`
**Auth:** Verified user required

Renders the live trading terminal page. All data is loaded client-side via JavaScript polling `/api/terminal/state`.

---

### `GET /history`
**Auth:** Verified user required

Renders the trade history page. Fetches last 100 trades from PostgreSQL `trade_history` table filtered by `user_id`.

---

### `GET /logout`
Clears the session and redirects to `/login`.

---

## API Endpoints

### `GET /api/terminal/state`
**Auth:** Verified user required
**Rate limit:** 120/min

Returns the current live state for the authenticated user from DynamoDB.

**Response (200 OK):**
```json
{
  "user_id": "user_abc123",
  "last_updated": 1234567890.123,
  "COMMAND_QUEUE": "NONE",
  "system_status": {
    "status_text": "SCANNING MARKET (ACTIVE)",
    "trading_enabled": true,
    "circuit_breaker_enabled": true,
    "circuit_breaker_tripped": false,
    "mode": "NORMAL",
    "cooldown_remaining_sec": 0,
    "uptime_sec": 3842
  },
  "performance_metrics": {
    "start_balance": 1000.00,
    "live_balance": 1048.50,
    "equity": 1052.30,
    "peak_balance": 1055.00,
    "session_pnl": 48.50,
    "open_pnl": 3.80,
    "drawdown_pct": 0.25,
    "active_trades_count": 1,
    "total_trades": 14,
    "win_rate": 0.714,
    "wins": 10,
    "losses": 4,
    "current_lot": 0.01,
    "spread": 28
  },
  "algo_scanner": {
    "trend_vector": "BULLISH",
    "ema_delta": 0.524,
    "macd": 0.0034,
    "signal_line": 0.0028,
    "momentum_rsi": 62.4,
    "ai_signal": "BUY",
    "ai_confidence": 0.78,
    "current_position_type": "LONG",
    "high_prob_signal": "NONE",
    "atr": 1.842,
    "bid": 2314.50,
    "ask": 2314.80
  },
  "ai_core": {
    "ticks_processed": 14832,
    "inference_ms": 2.4
  },
  "ui_arrays": {
    "logs": ["[SYS] [14:32:01.123] EXECUTION CONFIRMED: LONG 0.01 lots @ 2314.80"],
    "trade_history": [
      { "type": "LONG", "profit": 12.40, "open": "14:15:00", "close": "14:32:00", "dur": "0:17:00" }
    ],
    "live_prices": [2314.50, 2314.60, 2314.80],
    "live_times": ["14:32:01", "14:32:02", "14:32:03"]
  }
}
```

**Error response (if DynamoDB unreachable):**
```json
{ "error": "ResourceNotFoundException: ..." }
```

---

### `POST /api/terminal/command`
**Auth:** Verified user required
**Rate limit:** 30/min
**Content-Type:** `application/json`

Sends a command to the trading engine by writing to the `COMMAND_QUEUE` field in DynamoDB.

**Request body:**
```json
{ "command": "TOGGLE_TRADE" }
```

**Valid commands:**

| Command | Effect |
|---|---|
| `TOGGLE_TRADE` | Toggle auto-trading armed/disarmed |
| `CLOSE_ALL` | Close all open positions |
| `EMERGENCY_STOP` | Close positions + terminate engine |
| `BYPASS` | Skip post-trade cooldown |
| `TOGGLE_CB` | Toggle circuit breaker on/off |
| `REBOOT` | Reset drawdown reference after circuit breaker trip |

**Response (200 OK — success):**
```json
{ "ok": true }
```

**Response (400 — invalid command):**
```json
{ "ok": false, "error": "Invalid command" }
```

---

## Admin Routes

### `GET /admin`
**Auth:** Admin required

Query parameter `?status=pending|under_review|verified|rejected` filters the applications table.

---

### `POST /admin/applications/<application_id>/approve`
**Auth:** Admin required

Sets `applications.status = 'verified'` and `users.account_status = 'verified'` in a single transaction. Logs to `audit_log`.

**On success:** Flash "Application approved", redirect to `/admin`

---

### `POST /admin/applications/<application_id>/reject`
**Auth:** Admin required

**Form parameter:** `rejection_reason` (string)

Sets `applications.status = 'rejected'` and stores the reason. Logs to `audit_log`.

---

### `POST /admin/users/<user_id>/suspend`
**Auth:** Admin required

Sets `users.account_status = 'suspended'`. Logs to `audit_log` with action `USER_SUSPENDED`.

---

### `GET /api/admin/live_states`
**Auth:** Admin required

Returns DynamoDB live states for all verified users in a single JSON object keyed by `user_id`. Used for potential admin monitoring dashboard.

**Response:**
```json
{
  "user_abc123": { ...live state... },
  "user_def456": { ...live state... }
}
```

---

## Error Handlers

| Status | Condition | Response |
|---|---|---|
| 429 | Rate limit exceeded | `error.html` — "Too many requests" |
| 403 | Not admin / not verified | `error.html` — "Access denied" |
| 404 | Route not found | `error.html` — "Page not found" |
| 500 | Unhandled server error | `error.html` — "Internal server error" |
