#!/bin/bash
# Jarvis Lite v0.3 — Ubuntu/VDS installation script
set -euo pipefail

INSTALL_DIR="/opt/jarvis-lite"
SERVICE_USER="jarvis"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── Guards ─────────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (sudo)." >&2
    exit 1
fi

echo "=== Jarvis Lite v0.3 installer ==="
echo "Install dir : $INSTALL_DIR"
echo "Service user: $SERVICE_USER"
echo ""

# ── System packages ────────────────────────────────────────────────────────────

echo "[1/6] Installing system packages..."
apt-get update -qq
apt-get install -y python3 python3-venv docker.io

systemctl enable --now docker

# ── Create service user ────────────────────────────────────────────────────────

echo "[2/6] Setting up service user '$SERVICE_USER'..."
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system \
            --home-dir "$INSTALL_DIR" \
            --shell /usr/sbin/nologin \
            --create-home \
            "$SERVICE_USER"
    echo "  User '$SERVICE_USER' created."
else
    echo "  User '$SERVICE_USER' already exists."
fi

# Add jarvis to docker group so it can run docker commands
usermod -aG docker "$SERVICE_USER"

# ── Prepare install directory ──────────────────────────────────────────────────

echo "[3/6] Preparing $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$INSTALL_DIR/app"

# Copy project files — use /. to copy contents, not the directory itself,
# ensuring the result is $INSTALL_DIR/app/bot.py and NOT app/app/bot.py
cp -r "$REPO_DIR/app/." "$INSTALL_DIR/app/"
cp    "$REPO_DIR/requirements.txt" "$INSTALL_DIR/"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR/logs"

# ── Python virtual environment ─────────────────────────────────────────────────

echo "[4/6] Creating Python virtual environment..."
if [[ ! -d "$INSTALL_DIR/.venv" ]]; then
    sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/.venv"
fi
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
echo "  Dependencies installed."

# ── .env setup ────────────────────────────────────────────────────────────────

echo "[5/6] Checking .env configuration..."
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$REPO_DIR/.env.example" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
    echo ""
    echo "  .env created from .env.example."
    echo "  *** IMPORTANT: Edit $INSTALL_DIR/.env and set:"
    echo "      TELEGRAM_BOT_TOKEN=<your token>"
    echo "      ALLOWED_USER_IDS=<your Telegram user ID>"
    echo ""
else
    echo "  .env already exists, skipping."
fi

# ── Systemd service ────────────────────────────────────────────────────────────

echo "[6/6] Installing systemd service..."
cp "$REPO_DIR/systemd/jarvis-lite.service" /etc/systemd/system/jarvis-lite.service
systemctl daemon-reload
systemctl enable jarvis-lite

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit $INSTALL_DIR/.env with your bot token and allowed user IDs"
echo "  2. Start the service:  systemctl start jarvis-lite"
echo "  3. Check status:       systemctl status jarvis-lite"
echo "  4. Follow logs:        journalctl -u jarvis-lite -f"
echo ""
echo "To update Jarvis Lite, re-run this script after pulling new code."
