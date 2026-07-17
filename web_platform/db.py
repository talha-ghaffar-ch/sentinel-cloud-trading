"""
db.py — Database helpers for Sentinel Cloud Trading Web Platform
All connections use environment variables. Never hardcode credentials.
"""

import os
import time
import psycopg2
import psycopg2.extras
import boto3
from decimal import Decimal
from functools import wraps

# ── PostgreSQL ────────────────────────────────────────────────
def get_pg_conn():
    """Return a new PostgreSQL connection. Caller must close it."""
    return psycopg2.connect(
        host=os.environ["RDS_HOST"],
        port=os.environ.get("RDS_PORT", "5432"),
        database=os.environ.get("RDS_NAME", "sentinel"),
        user=os.environ["RDS_USER"],
        password=os.environ["RDS_PASSWORD"],
        connect_timeout=10,
        cursor_factory=psycopg2.extras.RealDictCursor,  # rows as dicts
    )

def pg_query(sql, params=None, fetch="all"):
    """Execute a query and return results. fetch: 'all' | 'one' | None"""
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if fetch == "all":
                    return cur.fetchall()
                elif fetch == "one":
                    return cur.fetchone()
                return None
    finally:
        conn.close()

def pg_execute(sql, params=None):
    """Execute a write query (INSERT/UPDATE/DELETE). Returns rowcount."""
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.rowcount
    finally:
        conn.close()

# ── DynamoDB ──────────────────────────────────────────────────
_dynamo_resource = None

def get_dynamo():
    global _dynamo_resource
    if _dynamo_resource is None:
        _dynamo_resource = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_REGION", "ap-south-1")
        )
    return _dynamo_resource

def get_live_state(user_id: str) -> dict:
    """Fetch the live trading state for a user from DynamoDB."""
    try:
        table = get_dynamo().Table(
            os.environ.get("DYNAMO_LIVE_STATE_TABLE", "Sentinel_Live_State")
        )
        resp = table.get_item(Key={"user_id": user_id})
        return _dec_to_float(resp.get("Item", {}))
    except Exception as e:
        return {"error": str(e)}

def send_command(user_id: str, command: str) -> bool:
    """Write a command to DynamoDB for the Trading_Engine to pick up."""
    try:
        table = get_dynamo().Table(
            os.environ.get("DYNAMO_LIVE_STATE_TABLE", "Sentinel_Live_State")
        )
        table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET COMMAND_QUEUE = :cmd",
            ExpressionAttributeValues={":cmd": command}
        )
        # Also log to Sentinel_Commands for audit trail
        cmd_table = get_dynamo().Table(
            os.environ.get("DYNAMO_COMMANDS_TABLE", "Sentinel_Commands")
        )
        cmd_table.put_item(Item={
            "user_id":   user_id,
            "timestamp": int(time.time() * 1000),
            "command":   command,
            "source":    "web_dashboard",
            "ttl":       int(time.time()) + (30 * 86400)
        })
        return True
    except Exception:
        return False

def _dec_to_float(obj):
    """Recursively convert Decimal → float for JSON serialisation."""
    if isinstance(obj, dict):
        return {k: _dec_to_float(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dec_to_float(i) for i in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    return obj

# ── User helpers ──────────────────────────────────────────────
def get_user_by_id(user_id: str):
    return pg_query(
        "SELECT * FROM users WHERE user_id = %s", (user_id,), fetch="one"
    )

def get_user_by_email(email: str):
    return pg_query(
        "SELECT * FROM users WHERE email = %s", (email,), fetch="one"
    )

def create_user(user_id, email, password_hash, full_name):
    pg_execute(
        """INSERT INTO users (user_id, email, password_hash, full_name)
           VALUES (%s, %s, %s, %s)""",
        (user_id, email, password_hash, full_name)
    )

def update_last_login(user_id):
    pg_execute(
        "UPDATE users SET last_login_at = NOW() WHERE user_id = %s", (user_id,)
    )

def get_user_application(user_id: str):
    return pg_query(
        "SELECT * FROM applications WHERE user_id = %s ORDER BY submitted_at DESC LIMIT 1",
        (user_id,), fetch="one"
    )

# ── Trade history helpers ─────────────────────────────────────
def get_user_trades(user_id: str, limit: int = 50):
    return pg_query(
        """SELECT * FROM trade_history WHERE user_id = %s
           ORDER BY close_time DESC LIMIT %s""",
        (user_id, limit)
    )

def get_user_trade_stats(user_id: str):
    return pg_query(
        """SELECT
               COUNT(*)                                         AS total_trades,
               COALESCE(SUM(profit_usd), 0)                   AS total_profit,
               COALESCE(SUM(CASE WHEN profit_usd>=0 THEN 1 ELSE 0 END),0) AS wins,
               COALESCE(SUM(CASE WHEN profit_usd< 0 THEN 1 ELSE 0 END),0) AS losses,
               COALESCE(AVG(ai_confidence_score)*100, 0)       AS avg_confidence
           FROM trade_history WHERE user_id = %s""",
        (user_id,), fetch="one"
    )

# ── Admin helpers ─────────────────────────────────────────────
def get_all_applications(status_filter=None):
    if status_filter:
        return pg_query(
            """SELECT a.*, u.email, u.full_name as user_full_name
               FROM applications a JOIN users u ON a.user_id = u.user_id
               WHERE a.status = %s ORDER BY a.submitted_at DESC""",
            (status_filter,)
        )
    return pg_query(
        """SELECT a.*, u.email, u.full_name as user_full_name
           FROM applications a JOIN users u ON a.user_id = u.user_id
           ORDER BY a.submitted_at DESC"""
    )

def approve_application(application_id: str, admin_id: str):
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # Get user_id from application
                cur.execute("SELECT user_id FROM applications WHERE application_id = %s", (application_id,))
                row = cur.fetchone()
                if not row:
                    return False
                user_id = row["user_id"]

                # Update application status
                cur.execute(
                    """UPDATE applications SET status='verified', reviewed_at=NOW(), reviewed_by=%s
                       WHERE application_id = %s""",
                    (admin_id, application_id)
                )
                # Update user account status
                cur.execute(
                    "UPDATE users SET account_status='verified' WHERE user_id = %s",
                    (user_id,)
                )
                # Log to audit trail
                cur.execute(
                    """INSERT INTO audit_log (actor_id, action, target_type, target_id, details)
                       VALUES (%s, 'APPLICATION_APPROVED', 'application', %s, '{}')""",
                    (admin_id, application_id)
                )
        return True
    finally:
        conn.close()

def reject_application(application_id: str, admin_id: str, reason: str):
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE applications
                       SET status='rejected', reviewed_at=NOW(), reviewed_by=%s, rejection_reason=%s
                       WHERE application_id = %s""",
                    (admin_id, reason, application_id)
                )
                cur.execute(
                    """INSERT INTO audit_log (actor_id, action, target_type, target_id,
                                             details)
                       VALUES (%s, 'APPLICATION_REJECTED', 'application', %s,
                               %s::jsonb)""",
                    (admin_id, application_id, f'{{"reason": "{reason}"}}')
                )
        return True
    finally:
        conn.close()

def get_all_users():
    return pg_query(
        """SELECT u.*, COUNT(t.deal_ticket) as trade_count,
                  COALESCE(SUM(t.profit_usd), 0) as total_profit
           FROM users u
           LEFT JOIN trade_history t ON u.user_id = t.user_id
           WHERE u.role != 'admin'
           GROUP BY u.user_id ORDER BY u.created_at DESC"""
    )
