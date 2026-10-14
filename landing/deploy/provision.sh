#!/usr/bin/env bash
set -euo pipefail

# stardust landing page — provisioner for Amazon Linux 2023.
# Usage:  sudo bash provision.sh
#
# Idempotent: safe to re-run to update the app (git reset --hard + restart).
#
# This is designed to run ALONGSIDE the existing ojblabs.com site on the same
# instance. It only touches its own resources:
#   * app user + /opt/stardust (sparse checkout of landing/ only)
#   * systemd unit stardust-landing (+ a daily DB backup timer)
#   * /etc/nginx/conf.d/stardust.conf  (leaves nginx.conf and ojblabs.conf alone)
#   * a dedicated Let's Encrypt cert for $DOMAIN (the ojblabs.com cert does not
#     cover subdomains)
# It never installs or touches Node, and never rewrites the global nginx config.

DOMAIN="${DOMAIN:-stardust.ojblabs.com}"
ADMIN_EMAIL="${ADMIN_EMAIL:-anil.ojblabs@gmail.com}"
REPO_URL="${REPO_URL:-https://github.com/anilpank/stardust.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
APP_USER="${APP_USER:-stardust}"
APP_DIR="${APP_DIR:-/opt/stardust}"
PORT="${PORT:-8000}"
PY=python3.12

LANDING_DIR="$APP_DIR/landing"
VENV_DIR="$APP_DIR/.venv"
ENV_FILE="$LANDING_DIR/.admin.env"
SERVICE=stardust-landing

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash provision.sh" >&2
  exit 1
fi
if ! command -v dnf >/dev/null 2>&1; then
  echo "This provisioner targets Amazon Linux 2023 (dnf not found)." >&2
  exit 1
fi

echo "==> Installing base packages"
dnf install -y git nginx python3.12 python3.12-pip

echo "==> Creating app user ($APP_USER)"
id -u "$APP_USER" &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

echo "==> Fetching landing code (sparse checkout: landing/ only)"
git config --global --add safe.directory "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$REPO_BRANCH"
  git -C "$APP_DIR" sparse-checkout set landing
  git -C "$APP_DIR" reset --hard "origin/$REPO_BRANCH"
else
  mkdir -p "$APP_DIR"
  chown "$APP_USER:$APP_USER" "$APP_DIR"
  sudo -u "$APP_USER" git clone --filter=blob:none --no-checkout --depth 1 \
    -b "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
  sudo -u "$APP_USER" git -C "$APP_DIR" sparse-checkout init --cone
  sudo -u "$APP_USER" git -C "$APP_DIR" sparse-checkout set landing
  sudo -u "$APP_USER" git -C "$APP_DIR" checkout "$REPO_BRANCH"
fi
mkdir -p "$LANDING_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Creating virtualenv ($("$PY" --version))"
sudo -u "$APP_USER" "$PY" -m venv "$VENV_DIR"
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --quiet -r "$LANDING_DIR/requirements.txt"

echo "==> Configuring admin credentials"
ADMIN_PASS=""
if [ ! -s "$ENV_FILE" ] || [ -n "${ADMIN_PASSWORD:-}" ]; then
  ADMIN_PASS="${ADMIN_PASSWORD:-$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 || true)}"
  sudo -u "$APP_USER" env PASSWORD="$ADMIN_PASS" \
    "$PY" "$LANDING_DIR/gen_admin_password.py" > "$ENV_FILE"
  chown "$APP_USER:$APP_USER" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
else
  echo "    keeping existing $ENV_FILE"
fi

echo "==> Installing systemd service"
cat >/etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=stardust landing page (Flask/gunicorn)
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$LANDING_DIR
Environment=LANDING_DATA_DIR=$LANDING_DIR/data
Environment=HOST=127.0.0.1
Environment=PORT=$PORT
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/gunicorn --bind 127.0.0.1:$PORT --workers 2 --access-logfile - server:app
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

echo "==> Waiting for the app to come up"
for i in $(seq 1 15); do
  if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null; then
    echo "    app is up"
    break
  fi
  [ "$i" -eq 15 ] && { echo "    app failed to start; check: journalctl -u $SERVICE" >&2; exit 1; }
  sleep 1
done

echo "==> Configuring nginx (adds stardust.conf; ojblabs.com untouched)"
cat >/etc/nginx/conf.d/stardust.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN;

    gzip on;
    gzip_types text/plain text/css application/json image/svg+xml;
    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
nginx -t
systemctl reload nginx

echo "==> Configuring TLS (certbot)"
command -v certbot >/dev/null 2>&1 || dnf install -y certbot python3-certbot-nginx
if [ ! -e "/etc/letsencrypt/live/$DOMAIN" ]; then
  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$ADMIN_EMAIL" --redirect; then
    echo "    SSL installed for $DOMAIN"
    nginx -t && systemctl reload nginx
  else
    echo "    WARN: certbot could not issue a certificate (DNS not propagated yet?)."
    echo "    Point $DOMAIN at this server, then run: sudo certbot --nginx -d $DOMAIN --redirect"
  fi
else
  echo "    certificate already present for $DOMAIN; skipping issuance"
fi
systemctl enable certbot.timer 2>/dev/null || true

echo "==> Installing daily database backup timer"
sudo -u "$APP_USER" mkdir -p "$LANDING_DIR/backups"
cat >/etc/systemd/system/$SERVICE-backup.service <<EOF
[Unit]
Description=stardust landing database snapshot
After=$SERVICE.service

[Service]
Type=oneshot
User=$APP_USER
Group=$APP_USER
Environment=LANDING_DATA_DIR=$LANDING_DIR/data
Environment=BACKUP_DIR=$LANDING_DIR/backups
Environment=BACKUP_KEEP=14
ExecStart=$PY $LANDING_DIR/deploy/backup_db.py
EOF
cat >/etc/systemd/system/$SERVICE-backup.timer <<EOF
[Unit]
Description=Daily stardust landing database backup

[Timer]
OnCalendar=*-*-* 03:45:00
Persistent=true
RandomizedDelaySec=1800

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now "$SERVICE-backup.timer"
systemctl start "$SERVICE-backup.service"

PUBLIC_IP="$(curl -sf --max-time 5 https://checkip.amazonaws.com || echo 'your-EC2-public-IP')"
if [ -n "$ADMIN_PASS" ]; then
  ADMIN_LINE="  Admin:       https://$DOMAIN/admin   user: admin   password: $ADMIN_PASS
               (stored hashed in $ENV_FILE)"
else
  ADMIN_LINE="  Admin:       https://$DOMAIN/admin   (credentials already in $ENV_FILE)"
fi
cat <<DONE

stardust landing provisioned.
  App server:  http://127.0.0.1:$PORT   (systemd: $SERVICE)
  Public URL:  https://$DOMAIN          (needs the GoDaddy A record -> $PUBLIC_IP)
  Public IP:   $PUBLIC_IP
$ADMIN_LINE
  Update app:  sudo bash $LANDING_DIR/deploy/provision.sh   (idempotent)
  Logs:        journalctl -u $SERVICE -f
  Backups:     14 daily DB snapshots in $LANDING_DIR/backups ($SERVICE-backup.timer)
  Download DB: https://$DOMAIN/admin -> Download DB

DONE
