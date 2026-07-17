#!/bin/bash
# ============================================================
#  SENTINEL WEB PLATFORM — EC2 DEPLOYMENT SCRIPT
#  Run once on your EC2 instance (Ubuntu 22.04 / Amazon Linux)
#  Usage: chmod +x deploy.sh && sudo ./deploy.sh
# ============================================================

set -e
APP_DIR="/opt/sentinel"
SERVICE_NAME="sentinel-web"

echo ""
echo "======================================"
echo "  SENTINEL WEB PLATFORM — DEPLOY"
echo "======================================"

# 1. Install system dependencies
echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nginx supervisor

# 2. Create app directory
echo "[2/6] Setting up app directory..."
mkdir -p $APP_DIR
cp -r . $APP_DIR/
chown -R www-data:www-data $APP_DIR

# 3. Python virtual environment
echo "[3/6] Creating Python environment..."
python3 -m venv $APP_DIR/venv
$APP_DIR/venv/bin/pip install --quiet --upgrade pip
$APP_DIR/venv/bin/pip install --quiet -r $APP_DIR/requirements.txt

# 4. Supervisor config (keeps gunicorn running)
echo "[4/6] Configuring supervisor..."
cat > /etc/supervisor/conf.d/sentinel.conf << EOF
[program:sentinel-web]
command=$APP_DIR/venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
directory=$APP_DIR
user=www-data
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/sentinel-web.log
environment=PATH="$APP_DIR/venv/bin"
EOF
supervisorctl reread
supervisorctl update
supervisorctl restart sentinel-web 2>/dev/null || supervisorctl start sentinel-web

# 5. Nginx config (reverse proxy + SSL ready)
echo "[5/6] Configuring Nginx..."
cat > /etc/nginx/sites-available/sentinel << 'NGINX'
server {
    listen 80;
    server_name _;    # Replace _ with your domain

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-Content-Type-Options "nosniff";
    add_header X-XSS-Protection "1; mode=block";

    location /static/ {
        alias /opt/sentinel/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 60;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/sentinel /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "======================================"
echo "  DEPLOYMENT COMPLETE"
echo "  Platform: http://$(curl -s ifconfig.me)"
echo "  Logs:     tail -f /var/log/sentinel-web.log"
echo "======================================"
echo ""
echo "NEXT STEPS:"
echo "  1. Copy your .env file to $APP_DIR/.env"
echo "  2. Run: supervisorctl restart sentinel-web"
echo "  3. (Optional) Point your domain and add SSL with certbot"
echo "     sudo certbot --nginx -d yourdomain.com"
echo ""
