"""
SENTINEL CLOUD TRADING — DATABASE CONNECTION TEST
==================================================
Run this before launching any Trading_Engine nodes to confirm
both PostgreSQL (RDS) and DynamoDB are reachable and configured.

    python db_test.py

All credentials are loaded from environment variables.
Never pass them as arguments or hardcode them.
"""

import os
import sys
import time
from datetime import datetime

# ── Load .env file if present ─────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[ENV] Loaded .env file")
except ImportError:
    print("[ENV] python-dotenv not installed — reading OS environment directly")


def check_env():
    """Verify all required environment variables are set."""
    required = [
        "RDS_HOST", "RDS_PORT", "RDS_NAME", "RDS_USER", "RDS_PASSWORD",
        "AWS_REGION", "DYNAMO_LIVE_STATE_TABLE"
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"\n[FAIL] Missing environment variables: {', '.join(missing)}")
        print("       Copy .env.template to .env and fill in all values.")
        sys.exit(1)
    print(f"[OK] All required environment variables are set")


def test_postgresql():
    """Test RDS PostgreSQL connection and verify schema."""
    print("\n── PostgreSQL (RDS) ─────────────────────────────────────")
    try:
        import psycopg2
    except ImportError:
        print("[FAIL] psycopg2 not installed. Run: pip install psycopg2-binary")
        return False

    config = {
        "host":     os.environ["RDS_HOST"],
        "port":     os.environ["RDS_PORT"],
        "database": os.environ["RDS_NAME"],
        "user":     os.environ["RDS_USER"],
        "password": os.environ["RDS_PASSWORD"],
        "connect_timeout": 10
    }

    try:
        t0 = time.time()
        conn = psycopg2.connect(**config)
        latency_ms = (time.time() - t0) * 1000
        print(f"[OK] Connected to {config['host']}  ({latency_ms:.0f} ms)")

        cur = conn.cursor()

        # Check PostgreSQL version
        cur.execute("SELECT version();")
        version = cur.fetchone()[0].split(",")[0]
        print(f"[OK] Server: {version}")

        # Check all expected tables exist
        expected_tables = [
            "users", "applications", "trade_history",
            "trading_nodes", "audit_log", "sessions"
        ]
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """)
        existing = {row[0] for row in cur.fetchall()}

        all_present = True
        for tbl in expected_tables:
            if tbl in existing:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                count = cur.fetchone()[0]
                print(f"  [OK] Table '{tbl}'  ({count} rows)")
            else:
                print(f"  [MISSING] Table '{tbl}' — run schema.sql first!")
                all_present = False

        # Check views
        cur.execute("""
            SELECT table_name FROM information_schema.views
            WHERE table_schema = 'public'
        """)
        views = {row[0] for row in cur.fetchall()}
        for view in ["v_user_trading_summary", "v_recent_trades"]:
            status = "[OK]" if view in views else "[MISSING]"
            print(f"  {status} View '{view}'")

        # Test write — insert and immediately delete a dummy row
        try:
            cur.execute("""
                INSERT INTO audit_log (actor_id, action, target_type, details)
                VALUES ('system', 'DB_CONNECTION_TEST', 'system', '{"test": true}')
                RETURNING log_id
            """)
            log_id = cur.fetchone()[0]
            cur.execute("DELETE FROM audit_log WHERE log_id = %s", (log_id,))
            conn.commit()
            print(f"  [OK] Write test passed (audit_log insert + delete)")
        except Exception as e:
            print(f"  [WARN] Write test failed: {e}")
            conn.rollback()

        cur.close()
        conn.close()
        return all_present

    except Exception as e:
        print(f"[FAIL] PostgreSQL connection error: {e}")
        return False


def test_dynamodb():
    """Test DynamoDB connectivity and verify tables."""
    print("\n── DynamoDB ─────────────────────────────────────────────")
    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError
    except ImportError:
        print("[FAIL] boto3 not installed. Run: pip install boto3")
        return False

    region = os.environ["AWS_REGION"]
    expected_tables = [
        os.environ.get("DYNAMO_LIVE_STATE_TABLE",    "Sentinel_Live_State"),
        os.environ.get("DYNAMO_COMMANDS_TABLE",       "Sentinel_Commands"),
        os.environ.get("DYNAMO_NOTIFICATIONS_TABLE",  "Sentinel_Notifications"),
    ]

    try:
        t0 = time.time()
        client = boto3.client("dynamodb", region_name=region)
        existing = client.list_tables()["TableNames"]
        latency_ms = (time.time() - t0) * 1000
        print(f"[OK] Connected to DynamoDB ({region})  ({latency_ms:.0f} ms)")

        all_present = True
        for tbl in expected_tables:
            if tbl in existing:
                info = client.describe_table(TableName=tbl)["Table"]
                item_count = info.get("ItemCount", "?")
                status = info["TableStatus"]
                print(f"  [OK] Table '{tbl}'  [status={status}, ~{item_count} items]")
            else:
                print(f"  [MISSING] Table '{tbl}' — run dynamodb_setup.py first!")
                all_present = False

        # Test read on Sentinel_Live_State for seeded users
        dynamo = boto3.resource("dynamodb", region_name=region)
        live_table = dynamo.Table(os.environ.get("DYNAMO_LIVE_STATE_TABLE", "Sentinel_Live_State"))

        for uid in ["user_01", "user_02"]:
            try:
                resp = live_table.get_item(Key={"user_id": uid})
                if "Item" in resp:
                    last_updated = resp["Item"].get("last_updated", 0)
                    print(f"  [OK] Live state found for '{uid}'  (last_updated={last_updated})")
                else:
                    print(f"  [WARN] No live state item for '{uid}' — seed with dynamodb_setup.py")
            except Exception as e:
                print(f"  [WARN] Could not read item for '{uid}': {e}")

        return all_present

    except Exception as e:
        print(f"[FAIL] DynamoDB error: {e}")
        return False


def print_report(pg_ok: bool, dynamo_ok: bool):
    print("\n" + "="*55)
    print("  SENTINEL DATABASE HEALTH REPORT")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*55)
    print(f"  PostgreSQL (RDS) : {'PASS ✓' if pg_ok     else 'FAIL ✗'}")
    print(f"  DynamoDB         : {'PASS ✓' if dynamo_ok else 'FAIL ✗'}")
    print("="*55)

    if pg_ok and dynamo_ok:
        print("  All systems GO. Safe to start Trading_Engine nodes.")
    else:
        print("  FIX issues above before starting Trading_Engine.")
    print()


if __name__ == "__main__":
    print("\nSENTINEL CLOUD TRADING — Database Connection Test")
    print(f"Time: {datetime.now()}\n")

    check_env()
    pg_ok     = test_postgresql()
    dynamo_ok = test_dynamodb()
    print_report(pg_ok, dynamo_ok)

    sys.exit(0 if (pg_ok and dynamo_ok) else 1)
