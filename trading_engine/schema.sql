-- ============================================================
--  SENTINEL CLOUD TRADING — POSTGRESQL MASTER SCHEMA
--  Database: sentinel
--  Run this once on your RDS instance to initialize all tables
--  Order matters — run top to bottom
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- TABLE 1: USERS
-- Web platform accounts (email + password login)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    user_id         VARCHAR(50)  PRIMARY KEY,          -- e.g. "user_01", "user_02" (must match Trading_Engine --user arg)
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   TEXT         NOT NULL,             -- bcrypt hash — NEVER store plaintext
    full_name       VARCHAR(255),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ,
    account_status  VARCHAR(20)  NOT NULL DEFAULT 'pending'   -- pending | verified | suspended | banned
        CHECK (account_status IN ('pending', 'verified', 'suspended', 'banned')),
    role            VARCHAR(20)  NOT NULL DEFAULT 'user'      -- user | admin
        CHECK (role IN ('user', 'admin')),
    email_verified  BOOLEAN      NOT NULL DEFAULT FALSE,
    verification_token TEXT,                           -- one-time email verification token
    reset_token        TEXT,                           -- password reset token
    reset_token_expiry TIMESTAMPTZ
);

COMMENT ON TABLE users IS 'Web platform user accounts for Sentinel Cloud Trading';

-- ============================================================
-- TABLE 2: APPLICATIONS
-- KYC / service request form submitted by users
-- ============================================================
CREATE TABLE IF NOT EXISTS applications (
    application_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          VARCHAR(50)  NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,

    -- Personal details
    full_name        VARCHAR(255) NOT NULL,
    phone_number     VARCHAR(30),
    address          TEXT,
    income_source    VARCHAR(255),
    reason_for_use   TEXT,

    -- MT5 terminal credentials (store encrypted in production)
    mt5_login        VARCHAR(100),
    mt5_password     TEXT,                             -- TODO: encrypt with AWS KMS before storing
    mt5_server       VARCHAR(255),

    -- Review workflow
    status           VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'under_review', 'verified', 'rejected')),
    submitted_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    reviewed_at      TIMESTAMPTZ,
    reviewed_by      VARCHAR(50)  REFERENCES users(user_id),  -- admin user_id
    rejection_reason TEXT,
    terms_accepted   BOOLEAN      NOT NULL DEFAULT FALSE,
    terms_accepted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_applications_user_id ON applications(user_id);
CREATE INDEX IF NOT EXISTS idx_applications_status  ON applications(status);

COMMENT ON TABLE applications IS 'KYC service applications submitted by users';
COMMENT ON COLUMN applications.mt5_password IS 'MUST be encrypted with KMS before storing in production';

-- ============================================================
-- TABLE 3: TRADE HISTORY
-- Permanent ledger — written by trading_engine.py (log_trade_to_rds)
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_history (
    deal_ticket             BIGINT       PRIMARY KEY,       -- MT5 deal ticket (unique per deal)
    user_id                 VARCHAR(50)  NOT NULL REFERENCES users(user_id),
    symbol                  VARCHAR(20)  NOT NULL,
    trade_type              VARCHAR(10)  NOT NULL CHECK (trade_type IN ('LONG', 'SHORT')),
    lot_size                NUMERIC(10,2) NOT NULL,
    open_time               TIMESTAMPTZ  NOT NULL,
    close_time              TIMESTAMPTZ  NOT NULL,
    duration_interval       INTERVAL,                      -- e.g. '00:03:42'
    open_price              NUMERIC(18,5) NOT NULL,
    close_price             NUMERIC(18,5) NOT NULL,
    profit_usd              NUMERIC(12,2) NOT NULL,
    drawdown_at_execution   NUMERIC(6,2),                  -- % drawdown when trade was opened
    ai_confidence_score     NUMERIC(5,4),                  -- 0.0000 – 1.0000
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trade_history_user_id    ON trade_history(user_id);
CREATE INDEX IF NOT EXISTS idx_trade_history_open_time  ON trade_history(open_time DESC);
CREATE INDEX IF NOT EXISTS idx_trade_history_symbol     ON trade_history(symbol);
CREATE INDEX IF NOT EXISTS idx_trade_history_close_time ON trade_history(close_time DESC);

COMMENT ON TABLE trade_history IS 'Immutable trade ledger written by Trading_Engine nodes';

-- ============================================================
-- TABLE 4: TRADING NODES
-- Tracks which EC2 node / MT5 terminal is assigned to each user
-- ============================================================
CREATE TABLE IF NOT EXISTS trading_nodes (
    node_id         SERIAL       PRIMARY KEY,
    user_id         VARCHAR(50)  UNIQUE REFERENCES users(user_id) ON DELETE SET NULL,
    node_label      VARCHAR(50)  NOT NULL UNIQUE,           -- e.g. "Node 1", "Node 2"
    mt5_exe_path    TEXT,                                   -- path to terminal64.exe on EC2
    ec2_instance_id VARCHAR(50),                            -- AWS instance ID
    node_status     VARCHAR(20)  NOT NULL DEFAULT 'offline'
        CHECK (node_status IN ('online', 'offline', 'error', 'maintenance')),
    assigned_at     TIMESTAMPTZ,
    last_heartbeat  TIMESTAMPTZ                             -- updated by Trading_Engine on each sync
);

COMMENT ON TABLE trading_nodes IS 'Maps users to their dedicated MT5 terminal nodes on EC2';

-- ============================================================
-- TABLE 5: AUDIT LOG
-- Tracks admin actions and important system events
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    log_id      BIGSERIAL   PRIMARY KEY,
    actor_id    VARCHAR(50),                               -- user_id of person who performed action (NULL = system)
    action      VARCHAR(100) NOT NULL,                     -- e.g. 'APPLICATION_APPROVED', 'USER_SUSPENDED'
    target_type VARCHAR(50),                               -- e.g. 'application', 'user', 'trade'
    target_id   TEXT,                                      -- ID of the affected record
    details     JSONB,                                     -- additional context
    ip_address  INET,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_actor     ON audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_action    ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_log_created   ON audit_log(created_at DESC);

COMMENT ON TABLE audit_log IS 'Immutable log of all admin actions and system events';

-- ============================================================
-- TABLE 6: SESSIONS
-- Web session tokens for authenticated users
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    session_token   TEXT        PRIMARY KEY,               -- cryptographically random token
    user_id         VARCHAR(50) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    ip_address      INET,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    is_valid        BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

COMMENT ON TABLE sessions IS 'Web authentication sessions — clean up expired rows periodically';

-- ============================================================
-- UTILITY VIEWS
-- Pre-built queries for the web dashboard
-- ============================================================

-- Per-user trading summary (used on user dashboard)
CREATE OR REPLACE VIEW v_user_trading_summary AS
SELECT
    u.user_id,
    u.email,
    u.full_name,
    u.account_status,
    COUNT(t.deal_ticket)                                AS total_trades,
    COALESCE(SUM(t.profit_usd), 0)                     AS total_profit_usd,
    COALESCE(SUM(CASE WHEN t.profit_usd >= 0 THEN 1 ELSE 0 END), 0) AS wins,
    COALESCE(SUM(CASE WHEN t.profit_usd <  0 THEN 1 ELSE 0 END), 0) AS losses,
    COALESCE(AVG(t.ai_confidence_score), 0)            AS avg_ai_confidence,
    MAX(t.close_time)                                  AS last_trade_at
FROM users u
LEFT JOIN trade_history t ON u.user_id = t.user_id
GROUP BY u.user_id, u.email, u.full_name, u.account_status;

COMMENT ON VIEW v_user_trading_summary IS 'Aggregated per-user stats for the admin panel and dashboards';

-- Recent trades view (used on terminal page)
CREATE OR REPLACE VIEW v_recent_trades AS
SELECT
    t.*,
    u.email,
    u.full_name
FROM trade_history t
JOIN users u ON t.user_id = u.user_id
ORDER BY t.close_time DESC;

COMMENT ON VIEW v_recent_trades IS 'Latest trades with user info — limit on query side';

-- ============================================================
-- SEED: DEFAULT ADMIN ACCOUNT
-- ------------------------------------------------------------
-- No usable credential is shipped in this public schema. Before running,
-- generate a bcrypt hash for your chosen admin password and paste it into
-- password_hash below, and set a real admin email:
--   python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt(12)).decode())"
-- Rotate the password after first login.
-- ============================================================
INSERT INTO users (user_id, email, password_hash, full_name, account_status, role, email_verified)
VALUES (
    'admin',
    'admin@example.com',
    '__REPLACE_WITH_BCRYPT_HASH__',
    'System Administrator',
    'verified',
    'admin',
    TRUE
)
ON CONFLICT (user_id) DO NOTHING;

-- ============================================================
-- CLEANUP FUNCTION: Remove expired sessions (run via cron/Lambda)
-- ============================================================
CREATE OR REPLACE FUNCTION cleanup_expired_sessions()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM sessions
    WHERE expires_at < NOW() OR is_valid = FALSE;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cleanup_expired_sessions IS 'Call daily via pg_cron or AWS Lambda to purge stale sessions';

-- ============================================================
-- END OF SCHEMA
-- ============================================================
