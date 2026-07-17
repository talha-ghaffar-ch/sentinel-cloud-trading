# 01 — System Overview

## Background

Algorithmic trading has historically been restricted to institutional investors and technically skilled individuals who can set up, maintain, and monitor automated trading systems locally. For retail traders, the barriers are significant — complex software configuration, constant monitoring, and the need to keep a personal machine running 24/7.

**Sentinel Cloud Trading** solves this by moving the entire trading infrastructure to the cloud. Clients interact only with a web browser. Everything else — the trading engine, market data processing, AI signal generation, and order execution — runs on managed AWS infrastructure around the clock.

---

## Project Goals

| Goal | Implementation |
|---|---|
| Provide non-technical users access to automated trading | Web dashboard with zero setup required |
| Ensure each user's trading is isolated and secure | Per-user DynamoDB state, per-process trading engine |
| Give administrators full oversight of clients | Admin panel with KYC review, MT5 credentials, trade stats |
| Make the system maintainable and updatable | CI/CD pipeline — one `git push` deploys changes |
| Keep credentials and secrets secure | IAM roles, environment variables, bcrypt, parameterized SQL |

---

## Scope

The project covers five distinct engineering areas:

**1. Web Application**
A Flask-based multi-page application with user authentication, an application/KYC workflow, a real-time trading terminal, trade history, and a full admin management panel.

**2. Trading Engine**
A headless Python process that connects to MetaTrader 5, trains an AI model on historical data, generates trading signals, executes orders, and manages risk — all running continuously in the background.

**3. Database Architecture**
A dual-database design: AWS RDS PostgreSQL for relational, permanent data (users, trade ledger, audit logs), and AWS DynamoDB for real-time, sub-second state synchronization between the trading engine and the web dashboard.

**4. AWS Cloud Infrastructure**
The platform is fully cloud-hosted across multiple AWS services in the `ap-south-1` region, with proper networking, security groups, IAM roles, and environment isolation.

**5. DevOps & CI/CD**
A GitHub Actions pipeline that automatically tests, packages, and deploys the web platform to AWS Elastic Beanstalk on every push to the `main` branch.

---

## User Roles

### Client (User)
1. Registers with email and password
2. Submits a KYC service application with personal details and MT5 terminal credentials
3. Waits for admin review and approval
4. Once verified, accesses the live trading terminal
5. Can arm/halt the system, monitor performance, view trade history, and send manual commands

### Administrator
1. Reviews incoming KYC applications
2. Views submitted MT5 login credentials to manually configure the trading terminal
3. Approves or rejects applications with optional rejection reasons
4. Monitors all users, trade counts, and profit/loss
5. Can suspend user accounts

### Trading Engine (System Process)
1. Runs as a background Windows Service on EC2
2. One process instance per verified user
3. Reads commands from DynamoDB, executes them on the MT5 terminal
4. Writes live state back to DynamoDB every second
5. Logs completed trades to PostgreSQL

---

## System Constraints and Design Decisions

**Why DynamoDB for real-time data?**
PostgreSQL is excellent for structured, relational data but is not designed for sub-second polling from a web dashboard. DynamoDB's key-value model allows the trading engine to `put_item` the entire state in one atomic write, and the web dashboard to `get_item` in one read — both completing in under 20ms regardless of concurrent users.

**Why a separate Windows EC2 for the trading engine?**
MetaTrader 5's Python API (`MetaTrader5` package) only runs on Windows. The trading engine process must co-exist with MT5 terminal executables on the same Windows machine. The web platform runs on Linux (Elastic Beanstalk) separately, communicating only through AWS-managed services.

**Why Flask over Django?**
The application's routing requirements are well-defined and relatively simple. Flask's lightweight nature and explicit configuration made it faster to build and easier to maintain for a project of this scope. Django's ORM was unnecessary given the direct psycopg2 approach used for full query control.

**Why Elastic Beanstalk over raw EC2 for the web platform?**
Elastic Beanstalk manages the Linux server, Nginx reverse proxy, Gunicorn process management, health monitoring, and rolling deployments automatically. This allows the CI/CD pipeline to deploy with a single GitHub Actions step without managing server configuration manually.
