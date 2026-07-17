# 02 — Architecture

## High-Level Design

Sentinel follows a **three-tier architecture** with an additional real-time data layer:

```
Tier 1 — Presentation    Browser (HTML/CSS/JS)
Tier 2 — Application     Flask on Elastic Beanstalk
Tier 3 — Data            PostgreSQL (RDS) + DynamoDB
              +
Real-time Layer          Trading Engine → DynamoDB → Browser (1s poll)
```

---

## Component Breakdown

### Web Platform (Flask)

The web platform is a server-rendered Flask application deployed on AWS Elastic Beanstalk. It handles:

- **Authentication** — login, registration, session management via `auth.py`
- **Business logic** — KYC workflow, admin approvals, account management via `app.py`
- **Database abstraction** — all SQL and DynamoDB operations encapsulated in `db.py`
- **REST API** — two API endpoints consumed by the terminal JavaScript: `/api/terminal/state` and `/api/terminal/command`
- **Templating** — 9 Jinja2 HTML templates with dual CSS themes

The application runs behind **Nginx** (reverse proxy) and **Gunicorn** (WSGI server, 3 workers) on Elastic Beanstalk's managed Linux environment.

---

### Trading Engine

The trading engine is a standalone Python script (`trading_engine.py`) that runs as a **persistent Windows Service** on the EC2 instance using NSSM (Non-Sucking Service Manager). One process instance runs per user.

```
python trading_engine.py --user user_abc123 --path "C:\...\terminal64.exe"
```

The engine is composed of four internal modules:

| Module | Responsibility |
|---|---|
| `AIEngine` | Data download, indicator computation, model training, signal prediction |
| `RiskManager` | Mode switching (NORMAL/RECOVERY), lot sizing, win/loss tracking |
| `TradingBot` | MT5 connection, order placement, position monitoring, trade detection |
| `CloudManager` | DynamoDB sync (read commands, write state), RDS trade logging |

---

### Real-Time Data Pipeline

The real-time pipeline is the architectural centrepiece of the system:

```
Trading Engine                DynamoDB                   Browser
      │                          │                          │
      │── put_item (state) ──►  │                          │
      │                          │ ◄── get_item (poll) ────│
      │                          │──── return Item ────────►│
      │                          │                          │ (renders UI)
      │ ◄── get_item (cmd) ─────│                          │
      │                          │ ◄── put_item (cmd) ─────│
      │ (executes command)       │                          │
```

- **Write frequency:** Trading Engine pushes state every ~1 second
- **Read frequency:** Browser JavaScript polls `/api/terminal/state` every 1 second
- **Latency:** End-to-end (engine → DynamoDB → Flask → browser) typically 200–400ms
- **Data size:** Each DynamoDB item contains ~50 fields including price arrays (last 150 ticks)

---

### CI/CD Pipeline

```
Local Machine                 GitHub                  AWS
     │                           │                     │
     │── git push main ─────────►│                     │
     │                           │ GitHub Actions fires │
     │                           │── zip source ───────►│
     │                           │── upload to S3 ─────►│
     │                           │── EB deploy ────────►│
     │                           │                     │ EB updates
     │                           │◄── success ─────────│ environment
     │◄── Actions green ─────────│                     │
                                                        │ Live in ~3 min
```

---

## Network Architecture

All services are deployed in the **ap-south-1 (Mumbai) region** within the same VPC:

```
VPC: sentinel-vpc
│
├── Public Subnet
│   ├── EC2 Windows (Elastic IP: xx.xx.xx.xx)
│   │   ├── port 80  — Flask web platform (Waitress)
│   │   └── port 3389 — RDP admin access (restricted to admin IP)
│   │
│   └── Elastic Beanstalk EC2 (auto-managed)
│       ├── port 80  — Nginx → Gunicorn → Flask
│       └── port 443 — HTTPS (when domain added)
│
└── Private Subnet
    └── RDS PostgreSQL
        └── port 5432 — accessible only from VPC (security group)
```

**Security Group Rules:**

| Resource | Inbound | Source |
|---|---|---|
| Windows EC2 | TCP 80 | 0.0.0.0/0 (public web) |
| Windows EC2 | TCP 3389 | Admin IP only |
| EB Instances | TCP 80 | 0.0.0.0/0 |
| RDS | TCP 5432 | EB security group + Windows EC2 private IP |
| DynamoDB | — | Via IAM role (no network rules needed) |

---

## State Management

### Session State (Flask)
User authentication state is stored in server-side signed cookies using Flask's `SECRET_KEY`. Sessions are configured with `HTTPONLY`, `SAMESITE=Lax`, and a 7-day lifetime.

### Application State (DynamoDB)
The full trading state for each user is stored as a single DynamoDB item under their `user_id` key. Structure:

```json
{
  "user_id": "user_abc123",
  "last_updated": 1234567890.123,
  "COMMAND_QUEUE": "NONE",
  "system_status": { "status_text": "...", "trading_enabled": true, ... },
  "performance_metrics": { "live_balance": 1250.50, "drawdown_pct": 2.3, ... },
  "algo_scanner": { "ai_signal": "BUY", "ai_confidence": 0.78, ... },
  "ai_core": { "ticks_processed": 14832, "inference_ms": 2.4 },
  "ui_arrays": { "logs": [...], "trade_history": [...], "live_prices": [...] }
}
```

### Permanent State (PostgreSQL)
All user accounts, applications, trades, and audit records are stored in PostgreSQL with full ACID guarantees. Trade history is immutable — `ON CONFLICT (deal_ticket) DO NOTHING` prevents any duplicate logging.

---

## Scalability Considerations

| Aspect | Current | Scalable To |
|---|---|---|
| Web platform | Single EB instance (t3.small) | EB auto-scaling group |
| Database | RDS db.t3.micro | RDS read replicas + connection pooling |
| DynamoDB | PAY_PER_REQUEST (unlimited) | Already infinitely scalable |
| Trading nodes | Manual per-user EC2 config | Automated via `trading_nodes` table |
| Users | Unlimited web users | Limited by EC2 MT5 terminal capacity |
