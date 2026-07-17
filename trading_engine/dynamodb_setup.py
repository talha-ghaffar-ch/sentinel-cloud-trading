"""
SENTINEL CLOUD TRADING — DYNAMODB SETUP SCRIPT
================================================
Creates all required DynamoDB tables with correct
key schemas, billing mode, and TTL settings.

Run once from your EC2 instance or local machine
with AWS credentials configured:

    python dynamodb_setup.py

Requirements:
    pip install boto3
"""

import boto3
import json
import os
import sys
from botocore.exceptions import ClientError

# ── Load from environment (never hardcode) ────────────────────
AWS_REGION  = os.environ.get("AWS_REGION",  "ap-south-1")  # Consolidated to match RDS region
AWS_PROFILE = os.environ.get("AWS_PROFILE", None)           # Optional: named profile


def get_client():
    session = boto3.Session(profile_name=AWS_PROFILE) if AWS_PROFILE else boto3.Session()
    return session.client("dynamodb", region_name=AWS_REGION)


def table_exists(client, table_name: str) -> bool:
    try:
        client.describe_table(TableName=table_name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def create_sentinel_live_state(client):
    """
    Sentinel_Live_State
    ───────────────────
    Primary purpose : Real-time state sync between Trading_Engine nodes and the web dashboard.
    Partition key   : user_id  (String)
    Billing mode    : PAY_PER_REQUEST (no capacity planning needed at this scale)

    Item structure written by trading_engine.py → CloudManager.sync():
    {
        "user_id":           "user_01",
        "last_updated":      1234567890.12345,  (epoch float)
        "COMMAND_QUEUE":     "NONE",            (EMERGENCY_STOP | CLOSE_ALL | TOGGLE_TRADE | etc.)
        "system_status":     { ... },
        "performance_metrics": { ... },
        "algo_scanner":      { ... },
        "ui_arrays":         { logs: [...], trade_history: [...] }
    }
    """
    TABLE_NAME = "Sentinel_Live_State"

    if table_exists(client, TABLE_NAME):
        print(f"  [SKIP] {TABLE_NAME} already exists.")
        return

    print(f"  [CREATE] {TABLE_NAME}...")
    client.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"}
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"}
        ],
        BillingMode="PAY_PER_REQUEST",
        SSESpecification={
            "Enabled": True,                        # Encryption at rest
            "SSEType": "AES256"
        },
        Tags=[
            {"Key": "Project",     "Value": "SentinelCloudTrading"},
            {"Key": "Environment", "Value": "production"},
            {"Key": "ManagedBy",   "Value": "setup_script"}
        ]
    )

    # Wait for table to become ACTIVE
    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=TABLE_NAME)
    print(f"  [OK] {TABLE_NAME} is ACTIVE.")


def create_sentinel_commands(client):
    """
    Sentinel_Commands  (audit trail of all commands sent from the web)
    ─────────────────
    Partition key : user_id     (String)
    Sort key      : timestamp   (Number — epoch ms)
    TTL field     : ttl         (auto-delete records older than 30 days)

    Kept separate from Live_State so you have a full history of every
    command issued (useful for debugging and compliance).
    """
    TABLE_NAME = "Sentinel_Commands"

    if table_exists(client, TABLE_NAME):
        print(f"  [SKIP] {TABLE_NAME} already exists.")
        return

    print(f"  [CREATE] {TABLE_NAME}...")
    client.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "user_id",   "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"}
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id",   "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "N"}
        ],
        BillingMode="PAY_PER_REQUEST",
        SSESpecification={"Enabled": True, "SSEType": "AES256"},
        Tags=[
            {"Key": "Project",     "Value": "SentinelCloudTrading"},
            {"Key": "Environment", "Value": "production"}
        ]
    )

    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=TABLE_NAME)

    # Enable TTL on the 'ttl' attribute (set to epoch+30days when writing items)
    client.update_time_to_live(
        TableName=TABLE_NAME,
        TimeToLiveSpecification={
            "Enabled":       True,
            "AttributeName": "ttl"
        }
    )
    print(f"  [OK] {TABLE_NAME} is ACTIVE with TTL enabled.")


def create_sentinel_notifications(client):
    """
    Sentinel_Notifications
    ──────────────────────
    For pushing alerts to the web dashboard (circuit breaker trips, errors, etc.)
    Partition key : user_id    (String)
    Sort key      : created_at (Number — epoch ms)
    TTL           : ttl        (auto-delete after 7 days)
    """
    TABLE_NAME = "Sentinel_Notifications"

    if table_exists(client, TABLE_NAME):
        print(f"  [SKIP] {TABLE_NAME} already exists.")
        return

    print(f"  [CREATE] {TABLE_NAME}...")
    client.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "user_id",    "KeyType": "HASH"},
            {"AttributeName": "created_at", "KeyType": "RANGE"}
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id",    "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "N"}
        ],
        BillingMode="PAY_PER_REQUEST",
        SSESpecification={"Enabled": True, "SSEType": "AES256"},
        Tags=[
            {"Key": "Project",     "Value": "SentinelCloudTrading"},
            {"Key": "Environment", "Value": "production"}
        ]
    )

    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=TABLE_NAME)

    client.update_time_to_live(
        TableName=TABLE_NAME,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"}
    )
    print(f"  [OK] {TABLE_NAME} is ACTIVE with TTL enabled.")


def seed_user_items(client, user_ids: list):
    """
    Pre-seeds blank state items in Sentinel_Live_State for each user.
    Trading_Engine will overwrite these — this just ensures the items exist
    so the web dashboard doesn't crash on a fresh query.
    """
    dynamo = boto3.Session(
        profile_name=AWS_PROFILE
    ).resource("dynamodb", region_name=AWS_REGION) if AWS_PROFILE else boto3.resource("dynamodb", region_name=AWS_REGION)

    table = dynamo.Table("Sentinel_Live_State")

    import time
    for uid in user_ids:
        table.put_item(
            Item={
                "user_id":        uid,
                "last_updated":   int(time.time()),
                "COMMAND_QUEUE":  "NONE",
                "system_status":  {
                    "status_text":            "OFFLINE — Node not started",
                    "trading_enabled":        False,
                    "circuit_breaker_enabled": True,
                    "circuit_breaker_tripped": False,
                    "mode":                   "NORMAL",
                    "cooldown_remaining_sec": 0
                },
                "performance_metrics": {
                    "start_balance":    0,
                    "live_balance":     0,
                    "equity":           0,
                    "peak_balance":     0,
                    "session_pnl":      0,
                    "open_pnl":         0,
                    "drawdown_pct":     0,
                    "active_trades_count": 0,
                    "total_trades":     0,
                    "win_rate":         0,
                    "wins":             0,
                    "losses":           0
                },
                "algo_scanner": {
                    "trend_vector":        "NEUTRAL",
                    "ema_delta":           0,
                    "macd":                0,
                    "momentum_rsi":        50,
                    "ai_signal":           "WAIT",
                    "ai_confidence":       0,
                    "current_position_type": "NONE",
                    "high_prob_signal":    "NONE"
                },
                "ui_arrays": {
                    "logs":          [],
                    "trade_history": []
                }
            },
            ConditionExpression="attribute_not_exists(user_id)"  # Don't overwrite live data
        )
        print(f"  [SEED] Blank state created for {uid}")


def print_summary(client):
    """Print a summary of all Sentinel tables."""
    sentinel_tables = [t for t in client.list_tables()["TableNames"] if "Sentinel" in t]
    print("\n" + "="*55)
    print("  DYNAMODB TABLES READY")
    print("="*55)
    for name in sentinel_tables:
        info = client.describe_table(TableName=name)["Table"]
        print(f"  ✓ {name:<35} [{info['TableStatus']}]")
    print("="*55)


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\nSENTINEL CLOUD TRADING — DynamoDB Setup")
    print(f"Region: {AWS_REGION}\n")

    client = get_client()

    print("Creating tables...")
    create_sentinel_live_state(client)
    create_sentinel_commands(client)
    create_sentinel_notifications(client)

    # Seed blank state for existing users
    # Add any user IDs that match your --user args in start_sessions.bat
    print("\nSeeding initial user state...")
    try:
        seed_user_items(client, user_ids=["user_01", "user_02"])
    except Exception as e:
        print(f"  [WARN] Seed skipped (items may already exist): {e}")

    print_summary(client)
    print("\nDone. DynamoDB is ready.\n")
