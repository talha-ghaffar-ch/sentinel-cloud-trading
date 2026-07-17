"""
setup_db.py — Run this on your Windows EC2 to set up all databases
Usage: python setup_db.py
"""
import os, sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[ERROR] Run: pip install python-dotenv")
    sys.exit(1)

print("\n  SENTINEL — Database Setup")
print("  =" * 28)

# ── Step 1: PostgreSQL ────────────────────────────────────────
print("\n[1/2] Setting up PostgreSQL schema...")
try:
    import psycopg2
    conn = psycopg2.connect(
        host=os.environ["RDS_HOST"],
        port=os.environ.get("RDS_PORT", "5432"),
        database=os.environ.get("RDS_NAME", "sentinel"),
        user=os.environ["RDS_USER"],
        password=os.environ["RDS_PASSWORD"],
        connect_timeout=10
    )
    conn.autocommit = True
    cur = conn.cursor()

    schema_path = os.path.join(os.path.dirname(__file__), "..", "trading_engine", "schema.sql")
    if not os.path.exists(schema_path):
        schema_path = "schema.sql"

    with open(schema_path, "r") as f:
        sql = f.read()

    # Run each statement
    import re
    statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
    ok = 0
    for stmt in statements:
        try:
            cur.execute(stmt)
            ok += 1
        except psycopg2.errors.DuplicateTable:
            pass  # Table already exists, skip
        except psycopg2.errors.DuplicateObject:
            pass  # Index already exists, skip
        except Exception as e:
            if "already exists" in str(e).lower():
                pass
            else:
                print(f"  [WARN] {str(e)[:80]}")

    cur.close()
    conn.close()
    print(f"  [OK] PostgreSQL schema ready ({ok} statements executed)")

except Exception as e:
    print(f"  [FAIL] PostgreSQL error: {e}")
    sys.exit(1)

# ── Step 2: DynamoDB ──────────────────────────────────────────
print("\n[2/2] Setting up DynamoDB tables...")
try:
    import boto3
    from botocore.exceptions import ClientError

    region = os.environ.get("AWS_REGION", "ap-south-1")
    client = boto3.client("dynamodb", region_name=region)
    existing = client.list_tables()["TableNames"]

    tables = [
        {
            "name": os.environ.get("DYNAMO_LIVE_STATE_TABLE", "Sentinel_Live_State"),
            "key": [{"AttributeName": "user_id", "KeyType": "HASH"}],
            "attrs": [{"AttributeName": "user_id", "AttributeType": "S"}],
        },
        {
            "name": os.environ.get("DYNAMO_COMMANDS_TABLE", "Sentinel_Commands"),
            "key": [
                {"AttributeName": "user_id",   "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"}
            ],
            "attrs": [
                {"AttributeName": "user_id",   "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "N"}
            ],
            "ttl": "ttl"
        },
        {
            "name": os.environ.get("DYNAMO_NOTIFICATIONS_TABLE", "Sentinel_Notifications"),
            "key": [
                {"AttributeName": "user_id",    "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"}
            ],
            "attrs": [
                {"AttributeName": "user_id",    "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "N"}
            ],
            "ttl": "ttl"
        },
    ]

    for t in tables:
        if t["name"] in existing:
            print(f"  [SKIP] {t['name']} already exists")
            continue
        client.create_table(
            TableName=t["name"],
            KeySchema=t["key"],
            AttributeDefinitions=t["attrs"],
            BillingMode="PAY_PER_REQUEST",
            SSESpecification={"Enabled": True, "SSEType": "AES256"}
        )
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=t["name"])
        if "ttl" in t:
            client.update_time_to_live(
                TableName=t["name"],
                TimeToLiveSpecification={"Enabled": True, "AttributeName": t["ttl"]}
            )
        print(f"  [OK] {t['name']} created")

except Exception as e:
    print(f"  [FAIL] DynamoDB error: {e}")
    sys.exit(1)

print("\n  All databases ready. Run start_web.bat to launch the platform.\n")
