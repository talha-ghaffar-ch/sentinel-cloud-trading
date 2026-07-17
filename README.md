# Sentinel Cloud Trading Platform

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?style=for-the-badge&logo=flask&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-Cloud-FF9900?style=for-the-badge&logo=amazon-aws&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?style=for-the-badge&logo=postgresql&logoColor=white)
![DynamoDB](https://img.shields.io/badge/DynamoDB-Real--time-4053D6?style=for-the-badge&logo=amazon-dynamodb&logoColor=white)
![License](https://img.shields.io/badge/License-Proprietary-red?style=for-the-badge)

**A cloud-hosted, AI-powered automated trading platform with multi-user management, real-time dashboards, and full AWS infrastructure.**

[Overview](#system-overview) · [Architecture](#architecture) · [Repository Layout](#repository-layout) · [Getting Started](#getting-started) · [Tech Stack](#tech-stack) · [Documentation](#documentation) · [Author](#author)

</div>

---

> ### 🔒 A note on what's in this repository
> This is a full source-available portfolio repository — web platform, trading
> engine, database schema, infrastructure scripts, and complete documentation.
> The only things deliberately **withheld** are the production-tuned strategy
> parameters (signal-confidence gates, recovery position-sizing, and stop/target
> multipliers). Those live behind environment variables; the in-code defaults are
> neutral placeholders. No credentials, keys, or secrets are stored anywhere in
> this repository — all runtime configuration is injected via environment
> variables. See the [LICENSE](LICENSE) for usage terms.

---

## System Overview

**Sentinel Cloud Trading** is a full-stack, cloud-deployed platform that enables clients to run automated forex/gold trading through MetaTrader 5 terminals managed entirely on AWS infrastructure. The platform provides a secure web interface where users register, submit KYC applications, and — once verified — access a real-time trading terminal that displays live market data, AI-generated signals, and full trade execution control.

The system was designed and built end-to-end as a **final-year project**, covering backend web development, cloud infrastructure, database architecture, real-time data pipelines, AI/ML integration, and automated deployment.

### Core Problem Solved
Traditional algorithmic trading requires technical expertise to set up and run locally. Sentinel abstracts this entirely — users simply log in to a web dashboard, and their dedicated trading engine runs 24/7 on cloud infrastructure, executing trades automatically based on AI signals while they monitor everything in real time from any device.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                            │
│          Login · Dashboard · Apply · Terminal · History         │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP / REST API
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              AWS ELASTIC BEANSTALK (Linux · Python 3.11)        │
│                                                                 │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐   │
│   │  app.py  │  │  auth.py │  │   db.py  │  │  Templates  │   │
│   │ 15 routes│  │  bcrypt  │  │ PG + DDB │  │  Jinja2 HTML│   │
│   └──────────┘  └──────────┘  └──────────┘  └─────────────┘   │
│                    Flask + Gunicorn + Nginx                     │
└──────────────────┬──────────────────┬───────────────────────────┘
                   │                  │
          SQL queries           DynamoDB R/W
                   │                  │
    ┌──────────────▼──┐    ┌──────────▼──────────────┐
    │  AWS RDS         │    │  AWS DynamoDB            │
    │  PostgreSQL 15   │    │  Sentinel_Live_State     │
    │                  │    │  Sentinel_Commands       │
    │  users           │    │  Sentinel_Notifications  │
    │  applications    │    └──────────────────────────┘
    │  trade_history   │              ▲
    │  trading_nodes   │              │ sync every 1 sec
    │  audit_log       │    ┌─────────┴──────────────────────────┐
    │  sessions        │    │  AWS EC2 Windows Server            │
    └──────────────────┘    │                                    │
              ▲             │  trading_engine.py (per user)      │
              │ log trades  │  ├── AIEngine (scikit-learn GBT)   │
              └─────────────│  ├── RiskManager                   │
                            │  ├── TradingBot (MT5 API)          │
                            │  └── CloudManager (AWS sync)       │
                            │                                    │
                            │  MT5 Node User_01 (terminal64.exe) │
                            │  MT5 Node User_02 (terminal64.exe) │
                            └────────────────────────────────────┘
```

### Data Flow Summary
1. **Trading engine** runs on a Windows EC2 host, connects to MetaTrader 5 terminals, generates AI signals, executes trades, and syncs live state to DynamoDB every second.
2. **Web dashboard** polls `/api/terminal/state` every second, reads DynamoDB, and renders live data to the user's browser.
3. **User commands** (`ARM`/`HALT`, `EMERGENCY_STOP`, `CLOSE_ALL`, …) are written to DynamoDB's `COMMAND_QUEUE` field and picked up by the engine on its next sync cycle.
4. **Completed trades** are permanently logged to RDS PostgreSQL via the engine's `log_trade_to_rds()` function.
5. **GitHub Actions** provides a CI check on every push and an illustrative deploy workflow to Elastic Beanstalk.

---

## Repository Layout

```
sentinel-cloud-trading/
│
├── web_platform/               Flask web application
│   ├── app.py                  All routes and request handling
│   ├── auth.py                 Authentication decorators and helpers
│   ├── db.py                   Database abstraction layer (PostgreSQL + DynamoDB)
│   ├── serve.py                Waitress server entry point (Windows)
│   ├── wsgi.py                 Gunicorn entry point (Linux / Elastic Beanstalk)
│   ├── gunicorn.conf.py        Gunicorn production config
│   ├── setup_db.py             One-shot database initializer
│   ├── deploy.sh               Self-managed EC2 deploy (Nginx + Supervisor)
│   ├── Procfile                Elastic Beanstalk process definition
│   ├── .ebextensions/          Elastic Beanstalk configuration
│   ├── requirements.txt
│   ├── .env.template
│   ├── install.bat / install_service.bat / start_web.bat   Windows helpers
│   ├── static/                 CSS + vanilla-JS terminal
│   └── templates/              9 Jinja2 HTML templates
│
├── trading_engine/             AI trading engine + database
│   ├── trading_engine.py       Headless engine (AIEngine · RiskManager · TradingBot · CloudManager)
│   ├── schema.sql              PostgreSQL master schema (6 tables + views)
│   ├── dynamodb_setup.py       Creates the 3 DynamoDB tables
│   ├── db_test.py              Connectivity / health-check script
│   ├── start_sessions.bat      Multi-node launcher
│   ├── requirements.txt
│   └── .env.template
│
├── docs/                       Full technical documentation (01–08)
├── .github/workflows/          CI + deploy pipelines
├── LICENSE
└── README.md
```

---

## Getting Started

> The platform is designed for AWS (RDS + DynamoDB + EC2). You can also run the
> web platform locally against your own PostgreSQL and DynamoDB endpoints.

**1. Configure environment**
```bash
cd web_platform
cp .env.template .env      # then fill in RDS / DynamoDB / SECRET_KEY
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Initialize databases** (creates tables + DynamoDB tables)
```bash
# First, replace the placeholder admin hash in ../trading_engine/schema.sql
python setup_db.py
```

**4. Run the web platform**
```bash
python serve.py            # Waitress (Windows)   → http://localhost
# or, on Linux:
gunicorn -c gunicorn.conf.py wsgi:app
```

**5. Run a trading node** (Windows host with MetaTrader 5 installed)
```bash
cd trading_engine
cp .env.template .env      # set engine + strategy parameters
pip install -r requirements.txt
python trading_engine.py --user user_01 --path "C:\path\to\terminal64.exe"
```

---

## Features

### User-Facing
| Feature | Description |
|---|---|
| **Secure Authentication** | Email/password registration with bcrypt hashing, session management, rate limiting |
| **KYC Application** | Multi-step service application form collecting personal details, MT5 credentials, and terms acceptance |
| **Live Trading Terminal** | Real-time dashboard updating every second — live prices, AI signals, equity, P&L, drawdown, trade history |
| **Tick Chart** | Live price trajectory chart rendered with Chart.js, dynamically scaled |
| **Command Center** | ARM / HALT, CLOSE ALL, EMERGENCY STOP, BYPASS COOLDOWN, circuit-breaker toggle, REBOOT |
| **Trade History** | Full permanent trade ledger with profit, lot size, duration, AI confidence, drawdown at execution |

### Admin-Facing
| Feature | Description |
|---|---|
| **Application Review** | Approve or reject KYC applications with reason; status workflow: pending → under review → verified / rejected |
| **User Management** | View all users, trade counts, total P&L, suspend accounts |
| **Audit Trail** | Every admin action logged with actor, action, target, and timestamp |

### System
| Feature | Description |
|---|---|
| **Multi-User Isolation** | Each user has a dedicated trading node and isolated DynamoDB state |
| **Circuit Breaker** | Auto-halts trading and closes positions when drawdown exceeds a configurable limit |
| **Recovery Mode** | Configurable lot scaling on consecutive losses, capped (production values proprietary) |
| **CI/CD Pipeline** | GitHub Actions CI on every push; illustrative Elastic Beanstalk deploy workflow |
| **IAM Role Auth** | EC2 instances authenticate to DynamoDB via IAM roles — no hardcoded AWS credentials |

---

## Tech Stack

**Backend:** Python 3.11 · Flask 3.0 · Gunicorn (Linux) / Waitress (Windows) · bcrypt · flask-limiter · psycopg2 · boto3 · python-dotenv

**AI / Trading:** MetaTrader5 API · scikit-learn (`HistGradientBoostingClassifier`) · pandas · numpy (EMA, MACD, RSI, ATR)

**Frontend:** Jinja2 · Chart.js · vanilla JavaScript · Google Fonts

**AWS:** EC2 (Windows) · Elastic Beanstalk · RDS PostgreSQL · DynamoDB · IAM · Elastic IP

**DevOps:** GitHub Actions · NSSM (Windows service manager) · Nginx + Supervisor (self-managed EC2 option)

---

## Database Design

### PostgreSQL (6 tables)
`users` · `applications` · `trade_history` · `trading_nodes` · `audit_log` · `sessions`, plus two dashboard views (`v_user_trading_summary`, `v_recent_trades`). Full schema with indexing rationale is in [docs/03_database_design.md](docs/03_database_design.md).

### DynamoDB (3 tables)
| Table | Key | Purpose |
|---|---|---|
| `Sentinel_Live_State` | `user_id` | Real-time trading state — balance, equity, signals, logs, chart data |
| `Sentinel_Commands` | `user_id` + `timestamp` | Command audit trail — 30-day TTL |
| `Sentinel_Notifications` | `user_id` + `created_at` | Dashboard alerts — 7-day TTL |

---

## Security Highlights

| Concern | Implementation |
|---|---|
| Password storage | bcrypt, cost factor 12 — never plaintext |
| SQL injection | psycopg2 parameterized queries throughout |
| Brute force | flask-limiter: 10 req/min on login, 5/hr on register |
| Session security | `HTTPONLY`, `SameSite=Lax`, secure cookies in production |
| AWS credentials | IAM instance profile on EC2 — no keys in code or config |
| Secrets | All credentials in environment variables — never committed |
| Admin actions | Every action logged to `audit_log` with actor and timestamp |

Full threat model in [docs/06_security.md](docs/06_security.md).

---

## Documentation

| Document | Description |
|---|---|
| [01 — System Overview](docs/01_system_overview.md) | Project background, goals, and scope |
| [02 — Architecture](docs/02_architecture.md) | System design decisions |
| [03 — Database Design](docs/03_database_design.md) | Full schema, relationships, indexing |
| [04 — Trading Engine](docs/04_trading_engine.md) | AI model, indicators, execution logic |
| [05 — API Reference](docs/05_api_reference.md) | All endpoints with request/response examples |
| [06 — Security](docs/06_security.md) | Security measures and threat model |
| [07 — Deployment](docs/07_deployment.md) | AWS infrastructure and CI/CD |
| [08 — User Guide](docs/08_user_guide.md) | End-to-end user journey |

---

## Author

**Talha Ghaffar**
GitHub: [@talha-ghaffar-ch](https://github.com/talha-ghaffar-ch)

*Final-year project — University of Management and Technology, Lahore.*

> ⚠️ **Disclaimer:** This project is for educational and portfolio purposes.
> It is not financial advice, and automated trading carries substantial risk.

---

<div align="center">
<sub>© 2026 Talha Ghaffar — Proprietary, source-available. See <a href="LICENSE">LICENSE</a>.</sub>
</div>
