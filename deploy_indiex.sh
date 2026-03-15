#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# deploy_indiex.sh — Deploy Indiex on Ubuntu EC2
# Run this CMD to deploy:  chmod +x deploy_indiex.sh && ./deploy_indiex.sh

## enter domain name for HTTPS: indiex.duckdns.org 

# it auto renew ssl certs with certbot, so no need to worry about that.
# EC2 Security Group: add inbound rules for Port 80 (HTTP) AND Port 443 (HTTPS) from 0.0.0.0/0
# DNS: Point your domain (A record) to the EC2 public IP BEFORE running this script
# post installation commands (run as needed):
# Action	Command
# Stop	    sudo systemctl stop indiex
# Start	    sudo systemctl start indiex
# Restart	sudo systemctl restart indiex
# Status	sudo systemctl status indiex
# Logs	    sudo journalctl -u indiex -f
# Disable auto-start	sudo systemctl disable indiex
# ═══════════════════════════════════════════════════════════════════════

set -e

APP_NAME="indiex"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/venv"
SERVICE_FILE="/etc/systemd/system/$APP_NAME.service"

# ── Domain for HTTPS (optional — leave blank for HTTP-only) ─────────
read -rp "Enter your domain name for HTTPS (leave blank to skip SSL): " DOMAIN
ENABLE_SSL=false
if [ -n "$DOMAIN" ]; then
    read -rp "Enter your email for Let's Encrypt notifications: " LE_EMAIL
    if [ -z "$LE_EMAIL" ]; then
        echo "❌ Email is required for Let's Encrypt. Exiting."
        exit 1
    fi
    ENABLE_SSL=true
fi

echo "═══════════════════════════════════════"
echo "  Deploying Indiex"
echo "  Project directory: $APP_DIR"
if [ "$ENABLE_SSL" = true ]; then
    echo "  Domain: $DOMAIN (HTTPS)"
else
    echo "  Mode: HTTP-only (no SSL)"
fi
echo "═══════════════════════════════════════"

# ── System packages ─────────────────────────────────────────────────
echo "Updating system..."
sudo apt update

echo "Installing python3-venv and nginx..."
sudo apt install python3-venv nginx -y

if [ "$ENABLE_SSL" = true ]; then
    echo "Installing certbot..."
    sudo apt install certbot python3-certbot-nginx -y
fi

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

# ── Nginx (reverse proxy + WebSocket + optional HTTPS) ─────────────
echo "Configuring Nginx..."

if [ "$ENABLE_SSL" = true ]; then
    SERVER_NAME="$DOMAIN"
else
    SERVER_NAME="_"
fi

# Step 1: Write HTTP config (also used by Certbot for domain verification)
sudo bash -c "cat > /etc/nginx/sites-available/indiex" <<EOL
server {
    listen 80;
    server_name $SERVER_NAME;

    # ── Regular HTTP requests ───────────────────────────────
    location / {
        proxy_pass http://127.0.0.1:8100;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 75s;
        proxy_read_timeout 300s;
    }

    # ── WebSocket connections ───────────────────────────────
    location /games/teen-patti/ws/ {
        proxy_pass http://127.0.0.1:8100;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
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

# ── Let's Encrypt SSL Certificate (only if domain provided) ────────
if [ "$ENABLE_SSL" = true ]; then
    echo "Obtaining SSL certificate from Let's Encrypt..."
    sudo certbot --nginx \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --email "$LE_EMAIL" \
        --redirect

    # Certbot auto-modifies the Nginx config to add SSL directives and
    # a redirect from HTTP → HTTPS. Now patch the HTTPS server block
    # to ensure WebSocket proxy settings are also in the SSL block.

    # Step 2: Write the final Nginx config with full HTTPS + WSS support
    sudo bash -c "cat > /etc/nginx/sites-available/indiex" <<EOL
# ── HTTP → HTTPS redirect (managed by Certbot) ─────────────
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}

# ── HTTPS server ────────────────────────────────────────────
server {
    listen 443 ssl;
    server_name $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # ── Regular HTTP requests ───────────────────────────────
    location / {
        proxy_pass http://127.0.0.1:8100;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 75s;
        proxy_read_timeout 300s;
    }

    # ── WebSocket connections (WSS) ─────────────────────────
    location /games/teen-patti/ws/ {
        proxy_pass http://127.0.0.1:8100;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
EOL

    echo "Testing final Nginx config..."
    sudo nginx -t

    echo "Reloading Nginx with HTTPS..."
    sudo systemctl reload nginx

    # ── Certbot auto-renewal (runs twice daily via systemd timer) ──
    echo "Verifying Certbot auto-renewal timer..."
    sudo systemctl enable certbot.timer
    sudo systemctl start certbot.timer
    echo "Auto-renewal status:"
    sudo systemctl list-timers certbot.timer --no-pager
fi

# ── Done ────────────────────────────────────────────────────────────
PUBLIC_IP=$(curl -s ifconfig.me)
echo ""
echo "═══════════════════════════════════════"
if [ "$ENABLE_SSL" = true ]; then
    echo "  ✅ Deployment complete with HTTPS!"
    echo ""
    echo "  🌐  https://$DOMAIN"
    echo "  📡  Public IP: $PUBLIC_IP"
    echo ""
    echo "  App status:     sudo systemctl status $APP_NAME"
    echo "  App logs:       sudo journalctl -u $APP_NAME -f"
    echo "  Nginx logs:     sudo tail -f /var/log/nginx/access.log"
    echo "  SSL expiry:     sudo certbot certificates"
    echo "  Renew manually: sudo certbot renew --dry-run"
    echo ""
    echo "  ⚠️  Make sure your DNS A record points $DOMAIN → $PUBLIC_IP"
else
    echo "  ✅ Deployment complete (HTTP)!"
    echo ""
    echo "  🌐  http://$PUBLIC_IP"
    echo ""
    echo "  App status:   sudo systemctl status $APP_NAME"
    echo "  App logs:     sudo journalctl -u $APP_NAME -f"
    echo "  Nginx logs:   sudo tail -f /var/log/nginx/access.log"
    echo ""
    echo "  💡 To add HTTPS later, get a domain and re-run this script"
fi
echo "═══════════════════════════════════════"
