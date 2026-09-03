#!/usr/bin/env bash
# Install status-pi as a systemd service on the Pi.
#
#     sudo ./setup/install.sh
#
# Assumes the panel is already up (see install-display.sh).  Safe to re-run:
# it upgrades the venv and the unit without touching your config.
set -euo pipefail

APP_DIR=/opt/status-pi
CONFIG_DIR=/etc/status-pi
STATE_DIR=/var/lib/status-pi
SERVICE_USER=status-pi
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

echo "==> packages"
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev libopenjp2-7 fbset rsync curl >/dev/null

echo "==> service user"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
usermod -aG video "$SERVICE_USER"

echo "==> application -> $APP_DIR"
mkdir -p "$APP_DIR" "$CONFIG_DIR" "$STATE_DIR"
rsync -a --delete --exclude .git --exclude var "$SRC_DIR/src/" "$APP_DIR/"
cp "$SRC_DIR/requirements.txt" "$APP_DIR/requirements.txt"

echo "==> virtualenv"
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    echo "==> first-run config -> $CONFIG_DIR/config.yaml"
    install -m 0640 "$SRC_DIR/setup/config.example.yaml" "$CONFIG_DIR/config.yaml"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR" "$STATE_DIR" "$APP_DIR"
chmod 0640 "$CONFIG_DIR/config.yaml"   # it holds the HA token and iCal URL

echo "==> status-pi command"
# So the diagnostics can be run from anywhere, not just /opt/status-pi.
install -m 0755 "$SRC_DIR/setup/status-pi-cli" /usr/local/bin/status-pi

echo "==> systemd unit"
install -m 0644 "$SRC_DIR/setup/status-pi.service" /etc/systemd/system/status-pi.service
systemctl daemon-reload
systemctl enable status-pi.service
systemctl restart status-pi.service

sleep 2
systemctl --no-pager --lines=10 status status-pi.service || true
cat <<EOF

Installed.  Next:
  1. Open http://$(hostname).local:8080 and fill in Settings
     (Home Assistant URL + token + entity, and the calendar source).
  2. Watch it work:   journalctl -u status-pi -f

Diagnostics, runnable from anywhere:
  status-pi --check-ha         does the Home Assistant link work
  status-pi --check-calendar   what the calendar returns
  status-pi --test-pattern     a ruler, to measure what the bezel hides
  status-pi --probe            what the framebuffer looks like
EOF
