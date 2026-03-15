#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# deploy_indiex.sh — Deploy Indiex on Ubuntu EC2
# Run this CMD to deploy:  chmod +x deploy_indiex.sh && ./deploy_indiex.sh
# Add Custom TCP Rule in EC2 Security Group: add inbound rules to allow Port 80 (HTTP) from 0.0.0.0/0 (anywhere)
# ═══════════════════════════════════════════════════════════════════════

set -e

APP_NAME="indiex"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/venv"
SERVICE_FILE="/etc/systemd/system/$APP_NAME.service"

echo "═══════════════════════════════════════"
echo "  Deploying Indiex"
echo "  Project directory: $APP_DIR"
echo "═══════════════════════════════════════"

# ── System packages ─────────────────────────────────────────────────
echo "Updating system..."
sudo apt update

echo "Installing python3-venv and nginx..."
sudo apt install python3-venv nginx -y

cd "$APP_DIR"

# ── Data directory (user JSON files live here) ──────────────────────
echo "Ensuring data directory..."
mkdir -p "$APP_DIR/data/users"

# ── Virtual environment ─────────────────────────────────────────────
echo "Checking virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
else
    echo "Virtual environment already exists"
fi

echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo "Installing requirements..."
pip install --upgrade pip
pip install -r requirements.txt

# gunicorn is the process manager; uvicorn[standard] provides the
# async worker + websocket support (websockets library).
echo "Installing Gunicorn..."
pip install gunicorn

# ── Systemd service ─────────────────────────────────────────────────
# IMPORTANT: -w 1 (single worker) because the app keeps game rooms
# and WebSocket connections in memory. Multiple workers would split
# players across processes and break the game.
echo "Creating systemd service..."

sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=Indiex FastAPI Service
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/gunicorn main:app \
    -k uvicorn.workers.UvicornWorker \
    -w 1 \
    -b 127.0.0.1:8100 \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile -

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOL

echo "Reloading systemd..."
sudo systemctl daemon-reload

echo "Enabling service..."
sudo systemctl enable "$APP_NAME"

echo "Restarting service..."
sudo systemctl restart "$APP_NAME"

# ── Nginx (reverse proxy + WebSocket support) ──────────────────────
echo "Configuring Nginx..."

sudo bash -c 'cat > /etc/nginx/sites-available/indiex' <<'EOL'
server {
    listen 80;
    server_name _;

    # ── Regular HTTP requests ───────────────────────────────
    location / {
        proxy_pass http://127.0.0.1:8100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── WebSocket connections ───────────────────────────────
    location /games/teen-patti/ws/ {
        proxy_pass http://127.0.0.1:8100;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
EOL

# Remove default site if it exists (avoids conflicts)
sudo rm -f /etc/nginx/sites-enabled/default

sudo ln -sf /etc/nginx/sites-available/indiex /etc/nginx/sites-enabled/

echo "Testing Nginx config..."
sudo nginx -t

echo "Restarting Nginx..."
sudo systemctl restart nginx

# ── Done ────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  ✅ Deployment complete!"
echo ""
echo "  App status:   sudo systemctl status $APP_NAME"
echo "  App logs:     sudo journalctl -u $APP_NAME -f"
echo "  Nginx logs:   sudo tail -f /var/log/nginx/access.log"
echo ""
echo "  Public IP:"
curl -s ifconfig.me
echo ""
echo "═══════════════════════════════════════"
