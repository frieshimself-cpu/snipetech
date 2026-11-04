#!/usr/bin/env bash
# Bring up snipetech on a Linux server (Ubuntu/Debian): venv, data dir, env file, systemd services.
#   sudo bash deploy/setup.sh            # from a checkout of the repo
# Then put your keys in /etc/snipetech/env and: sudo systemctl enable --now snipetech-tape snipetech-daily.timer
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SNIPETECH_USER:-snipetech}"
DATA=/var/lib/snipetech

id "$USER_NAME" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$USER_NAME"
apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null
install -d -o "$USER_NAME" -g "$USER_NAME" "$DATA" "$DATA/reports"
python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q -e "$REPO"
chown -R "$USER_NAME":"$USER_NAME" "$REPO"

install -d -m 750 /etc/snipetech
if [ ! -f /etc/snipetech/env ]; then
  cat > /etc/snipetech/env <<'ENV'
# Read by every snipetech service. Keep this file private (mode 600).
HELIUS_API_KEY=
OKS_KEY=
SNIPETECH_CONFIG=/etc/snipetech/snipetech.toml
ENV
  chmod 600 /etc/snipetech/env
fi
if [ ! -f /etc/snipetech/snipetech.toml ]; then
  "$REPO/.venv/bin/snipetech" --config /etc/snipetech/snipetech.toml init >/dev/null || true
fi
chown -R root:"$USER_NAME" /etc/snipetech && chmod 640 /etc/snipetech/snipetech.toml 2>/dev/null || true

for unit in "$REPO"/deploy/systemd/*; do
  sed -e "s#@REPO@#$REPO#g" -e "s#@USER@#$USER_NAME#g" -e "s#@DATA@#$DATA#g" "$unit" > "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload
echo "Installed. Fill /etc/snipetech/env, then: systemctl enable --now snipetech-tape snipetech-daily.timer"
