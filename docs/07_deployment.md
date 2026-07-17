# 07 — Deployment

## Infrastructure Overview

| Component | Service | Region | Spec |
|---|---|---|---|
| Web Platform | AWS Elastic Beanstalk | ap-south-1 | Python 3.11, Amazon Linux 2023, t3.small |
| Relational DB | AWS RDS PostgreSQL 15 | ap-south-1 | db.t3.micro, 20GB gp3 |
| Real-time DB | AWS DynamoDB | ap-south-1 | PAY_PER_REQUEST |
| Trading Server | AWS EC2 Windows Server 2019 | ap-south-1 | t3.medium |
| Static IP | AWS Elastic IP | ap-south-1 | Attached to Windows EC2 |
| Version Control | GitHub (private) | — | Source code |
| Documentation | GitHub (public) | — | This repository |

---

## CI/CD Pipeline

The deployment pipeline uses **GitHub Actions** to automatically deploy the web platform to Elastic Beanstalk on every push to `main`.

### Workflow: `.github/workflows/deploy.yml`

```yaml
name: Deploy to AWS Elastic Beanstalk
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Create deployment zip
        run: |
          zip -r deploy.zip . \
            --exclude "*.git*" \
            --exclude "*.env" \
            --exclude "__pycache__/*" \
            --exclude "*.pyc" \
            --exclude "*.bat" \
            --exclude "logs/*"

      - name: Deploy to Elastic Beanstalk
        uses: einaregilsson/beanstalk-deploy@v22
        with:
          aws_access_key:    ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws_secret_key:    ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          application_name:  sentinel-web
          environment_name:  sentinel-web-prod
          version_label:     ${{ github.sha }}
          region:            ap-south-1
          deployment_package: deploy.zip
```

### Pipeline Stages

```
git push main
    │
    ▼
GitHub Actions runner (ubuntu-latest)
    ├── Checkout source code
    ├── Create deployment ZIP (excludes secrets, cache, Windows files)
    ├── Upload ZIP to S3 (via EB action)
    └── Trigger EB environment update
            │
            ▼
    Elastic Beanstalk
            ├── Download new version from S3
            ├── Install Python dependencies (pip install -r requirements.txt)
            ├── Configure Nginx reverse proxy
            ├── Start Gunicorn workers (3 workers, 60s timeout)
            ├── Health check passes
            └── Environment switches to new version
                    │
                    ▼
            Live — ~3 minutes total
```

---

## Elastic Beanstalk Configuration

### `Procfile`
```
web: gunicorn --bind 0.0.0.0:8000 --workers 3 --timeout 60 wsgi:app
```

### `.ebextensions/options.config`
```yaml
option_settings:
  aws:elasticbeanstalk:container:python:
    WSGIPath: wsgi:app
  aws:elasticbeanstalk:environment:proxy:staticfiles:
    /static: static
```

Static files are served directly by Nginx without hitting Gunicorn.

### Environment Properties (set via EB Console)
All sensitive configuration is injected as environment properties — never stored in code:
```
AWS_REGION, RDS_HOST, RDS_PORT, RDS_NAME, RDS_USER, RDS_PASSWORD,
DYNAMO_LIVE_STATE_TABLE, DYNAMO_COMMANDS_TABLE, DYNAMO_NOTIFICATIONS_TABLE,
SECRET_KEY, FLASK_ENV
```

---

## Windows EC2 — Trading Server

The trading engine runs as a **persistent Windows Service** managed by NSSM (Non-Sucking Service Manager):

```
Service Name:  SentinelWeb  (web platform)
               SentinelNode1 (trading engine — user 1)
               SentinelNode2 (trading engine — user 2)

Start type:    Automatic
Restart:       On failure (immediate)
Stdout log:    C:\...\logs\web_stdout.log
Stderr log:    C:\...\logs\web_stderr.log
```

**NSSM install command:**
```cmd
nssm install SentinelNode1 "C:\Python311\python.exe"
nssm set SentinelNode1 AppParameters "trading_engine.py --user user_abc123 --path C:\...\terminal64.exe"
nssm set SentinelNode1 AppDirectory "C:\Sentinel\trading_engine"
nssm set SentinelNode1 Start SERVICE_AUTO_START
nssm start SentinelNode1
```

---

## Database Initialization

Run once before first deployment:

```cmd
python web_platform/setup_db.py
```

This script:
1. Creates all 6 PostgreSQL tables, indexes, and views from `trading_engine/schema.sql` (idempotent — skips objects that already exist)
2. Seeds the admin account defined in `schema.sql` — replace the placeholder bcrypt hash first
3. Creates the 3 DynamoDB tables (skips any that already exist)

Blank live-state items for node users can be pre-seeded with `python trading_engine/dynamodb_setup.py`.

---

## Networking

```
Internet → Elastic IP → Windows EC2 :80 → Waitress → Flask
Internet → EB Load Balancer → EB EC2 :80 → Nginx → Gunicorn → Flask

EB EC2 → RDS :5432 (same VPC, security group allows)
EB EC2 → DynamoDB (via AWS endpoint, IAM role)
Windows EC2 → RDS :5432 (same VPC, security group allows private IP)
Windows EC2 → DynamoDB (via AWS endpoint, IAM role)
```

---

## Monitoring

- **EB Health Dashboard:** Enhanced health reporting enabled — CPU, request count, latency percentiles
- **CloudWatch Logs:** Application stdout/stderr streamed automatically (7-day retention)
- **DynamoDB Metrics:** Read/write capacity, throttled requests visible in CloudWatch
- **RDS Metrics:** CPU, connections, free storage in CloudWatch

---

## Update Procedure

**Web platform update:**
```cmd
git add .
git commit -m "description of change"
git push
# Automatically deploys in ~3 minutes
```

**Trading engine update:**
```cmd
# Copy new trading_engine.py to EC2
nssm restart SentinelNode1
nssm restart SentinelNode2
```

**Database schema change:**
```cmd
# Connect to RDS and run ALTER TABLE statements manually
# Never drop tables on production (destroys all data) — apply migrations instead
```
