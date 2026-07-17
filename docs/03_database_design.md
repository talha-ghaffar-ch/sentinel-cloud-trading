# 03 — Database Design

## Overview

Sentinel uses a **dual-database architecture** chosen specifically for each database's strengths:

| Database | Type | Purpose | Why |
|---|---|---|---|
| AWS RDS PostgreSQL | Relational | Permanent, structured data | ACID, complex queries, foreign keys |
| AWS DynamoDB | NoSQL Key-Value | Real-time state sync | Sub-millisecond reads, no schema, single-item atomic writes |

---

## PostgreSQL Schema

### Entity Relationship Summary

```
users ──────────────────────┐
  │                         │
  ├──► applications         │ (reviewed_by FK → users)
  ├──► trade_history        │
  ├──► trading_nodes        │
  ├──► sessions             │
  └──► audit_log (actor_id) ┘
```

---

### Table: `users`

The central table. `user_id` is a short hash derived from the user's email at registration time and must match the `--user` argument passed to the corresponding `Trading_Engine` process.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `user_id` | VARCHAR(50) | PRIMARY KEY | e.g. `user_a3f8c2` |
| `email` | VARCHAR(255) | NOT NULL, UNIQUE | Login credential |
| `password_hash` | TEXT | NOT NULL | bcrypt hash (cost 12) |
| `full_name` | VARCHAR(255) | — | Display name |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Registration timestamp |
| `last_login_at` | TIMESTAMPTZ | — | Updated on each login |
| `account_status` | VARCHAR(20) | DEFAULT 'pending' | `pending` · `verified` · `suspended` · `banned` |
| `role` | VARCHAR(20) | DEFAULT 'user' | `user` · `admin` |
| `email_verified` | BOOLEAN | DEFAULT FALSE | Reserved for email verification flow |

**Indexes:** Primary key on `user_id`, unique index on `email`

---

### Table: `applications`

Stores KYC service applications submitted by users. Each application goes through a status workflow managed by admin review.

| Column | Type | Description |
|---|---|---|
| `application_id` | UUID (PK) | Auto-generated via `gen_random_uuid()` |
| `user_id` | FK → users | Applicant |
| `full_name` | VARCHAR(255) | As provided in form |
| `phone_number` | VARCHAR(30) | Contact number |
| `address` | TEXT | Residential address |
| `income_source` | VARCHAR(255) | Source of trading funds |
| `reason_for_use` | TEXT | Justification for service |
| `mt5_login` | VARCHAR(100) | MetaTrader 5 account number |
| `mt5_password` | TEXT | MT5 password (admin-only access) |
| `mt5_server` | VARCHAR(255) | Broker server name |
| `status` | VARCHAR(20) | `pending` → `under_review` → `verified` / `rejected` |
| `submitted_at` | TIMESTAMPTZ | Form submission time |
| `reviewed_at` | TIMESTAMPTZ | Admin review completion time |
| `reviewed_by` | FK → users | Admin who reviewed |
| `rejection_reason` | TEXT | Populated on rejection |
| `terms_accepted` | BOOLEAN | Must be TRUE to submit |
| `terms_accepted_at` | TIMESTAMPTZ | Timestamp of acceptance |

**Indexes:** `idx_app_user` on `user_id`, `idx_app_status` on `status`

**Status Workflow:**
```
[submitted] → pending → under_review → verified
                                    ↘ rejected
```

---

### Table: `trade_history`

Immutable ledger of every trade executed by the trading engine. Written by `CloudManager.log_trade_to_rds()`. The `ON CONFLICT (deal_ticket) DO NOTHING` clause ensures no duplicate entries even if the engine restarts mid-session.

| Column | Type | Description |
|---|---|---|
| `deal_ticket` | BIGINT (PK) | MT5 deal ticket — globally unique per deal |
| `user_id` | FK → users | Which user's engine executed this trade |
| `symbol` | VARCHAR(20) | e.g. `XAUUSDm` |
| `trade_type` | VARCHAR(10) | `LONG` or `SHORT` |
| `lot_size` | NUMERIC(10,2) | Volume traded |
| `open_time` | TIMESTAMPTZ | Position entry time |
| `close_time` | TIMESTAMPTZ | Position exit time |
| `duration_interval` | INTERVAL | `close_time - open_time` |
| `open_price` | NUMERIC(18,5) | Entry price |
| `close_price` | NUMERIC(18,5) | Exit price |
| `profit_usd` | NUMERIC(12,2) | Net profit/loss in USD |
| `drawdown_at_execution` | NUMERIC(6,2) | Account drawdown % when trade opened |
| `ai_confidence_score` | NUMERIC(5,4) | Model confidence at signal generation (0–1) |
| `created_at` | TIMESTAMPTZ | When this row was inserted |

**Indexes:** `idx_th_user` on `user_id`, `idx_th_time` on `close_time DESC`

---

### Table: `trading_nodes`

Maps verified users to their dedicated MT5 terminal and EC2 node. Used by administrators to track which physical terminal corresponds to which user account.

| Column | Type | Description |
|---|---|---|
| `node_id` | SERIAL (PK) | Auto-increment |
| `user_id` | FK → users, UNIQUE | One node per user |
| `node_label` | VARCHAR(50) | e.g. `Node 1`, `Node 2` |
| `mt5_exe_path` | TEXT | Full path to `terminal64.exe` on EC2 |
| `ec2_instance_id` | VARCHAR(50) | AWS instance ID |
| `node_status` | VARCHAR(20) | `online` · `offline` · `error` · `maintenance` |
| `last_heartbeat` | TIMESTAMPTZ | Updated by engine each sync cycle |

---

### Table: `audit_log`

Append-only log of every significant admin action. Never deleted or updated — provides a full accountability trail.

| Column | Type | Description |
|---|---|---|
| `log_id` | BIGSERIAL (PK) | Auto-increment |
| `actor_id` | VARCHAR(50) | Admin user_id who performed the action |
| `action` | VARCHAR(100) | e.g. `APPLICATION_APPROVED`, `USER_SUSPENDED` |
| `target_type` | VARCHAR(50) | e.g. `application`, `user`, `trade` |
| `target_id` | TEXT | ID of the affected record |
| `details` | JSONB | Additional context (flexible schema) |
| `ip_address` | INET | Admin's IP at time of action |
| `created_at` | TIMESTAMPTZ | Action timestamp |

**Index:** `idx_audit_created` on `created_at DESC`

---

### Table: `sessions`

Web authentication sessions. Each login creates a cryptographically random token stored here. Expired sessions are cleaned up periodically.

| Column | Type | Description |
|---|---|---|
| `session_token` | TEXT (PK) | `secrets.token_hex(32)` — 64 hex characters |
| `user_id` | FK → users | Session owner |
| `ip_address` | INET | Client IP at login |
| `user_agent` | TEXT | Browser user agent |
| `created_at` | TIMESTAMPTZ | Login time |
| `expires_at` | TIMESTAMPTZ | DEFAULT `NOW() + 7 days` |
| `is_valid` | BOOLEAN | Set to FALSE on logout |

---

### Views

**`v_user_trading_summary`**
Aggregates per-user trading statistics used by the admin panel and user dashboard:
```sql
SELECT user_id, email, full_name, account_status,
       COUNT(deal_ticket)             AS total_trades,
       SUM(profit_usd)                AS total_profit_usd,
       SUM(CASE WHEN profit_usd >= 0 THEN 1 ELSE 0 END) AS wins,
       SUM(CASE WHEN profit_usd <  0 THEN 1 ELSE 0 END) AS losses,
       MAX(close_time)                AS last_trade_at
FROM users LEFT JOIN trade_history USING (user_id)
GROUP BY user_id, email, full_name, account_status;
```

**`v_recent_trades`**
Joins trade_history with user info for the history page, ordered by close_time DESC.

---

## DynamoDB Design

### Table: `Sentinel_Live_State`

| Attribute | Type | Description |
|---|---|---|
| `user_id` (PK) | String | Partition key — matches PostgreSQL user_id |
| `last_updated` | Number | Unix epoch — used to detect staleness |
| `COMMAND_QUEUE` | String | Current pending command or `"NONE"` |
| `system_status` | Map | Status text, flags, mode, cooldown |
| `performance_metrics` | Map | Balance, equity, P&L, drawdown, W/L |
| `algo_scanner` | Map | Signal, confidence, trend, MACD, RSI, ATR |
| `ai_core` | Map | Ticks processed, inference latency |
| `ui_arrays` | Map | logs[], trade_history[], live_prices[], live_times[] |

**Billing:** PAY_PER_REQUEST — no capacity planning required
**Encryption:** Server-side encryption enabled by default

---

### Table: `Sentinel_Commands` (TTL: 30 days)

Audit trail of every command sent from the web dashboard to the trading engine.

| Attribute | Type |
|---|---|
| `user_id` (PK) | String |
| `timestamp` (SK) | Number (epoch ms) |
| `command` | String |
| `source` | String (`web_dashboard`) |
| `ttl` | Number (epoch + 30 days) |

---

### Table: `Sentinel_Notifications` (TTL: 7 days)

Push notifications from the trading engine to the web dashboard (e.g. circuit breaker alerts).

| Attribute | Type |
|---|---|
| `user_id` (PK) | String |
| `created_at` (SK) | Number (epoch ms) |
| `type` | String (`CIRCUIT_BREAKER`, `ERROR`, etc.) |
| `message` | String |
| `read` | Boolean |
| `ttl` | Number (epoch + 7 days) |

---

## Data Flow Between Databases

```
Trading Engine executes trade
        │
        ├──► DynamoDB: update ui_arrays.trade_history (immediate, in-memory display)
        │
        └──► PostgreSQL: INSERT INTO trade_history (permanent record)
                         ON CONFLICT (deal_ticket) DO NOTHING
```

The dual-write ensures the web terminal shows the trade immediately (via DynamoDB) while the permanent ledger (PostgreSQL) receives the full structured record with all metadata.
