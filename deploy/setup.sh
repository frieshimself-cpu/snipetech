#!/usr/bin/env bash
# Bring up snipetech on a Linux server (Ubuntu/Debian): venv, data dir, env file, systemd services.
#   sudo bash deploy/setup.sh            # from a checkout of the repo (deploy/bootstrap.sh runs this for you)
# Then put your keys in /etc/snipetech/env and: sudo systemctl enable --now snipetech-tape snipetech-daily.timer snipetech-operator.timer snipetech-discover.timer snipetech-qualify.timer snipetech-measure.timer snipetech-book.timer snipetech-events snipetech-snapshot.timer snipetech-strategist.timer snipetech-commands.timer
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SNIPETECH_USER:-snipetech}"
DATA=/var/lib/snipetech

id "$USER_NAME" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$USER_NAME"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip git sudo >/dev/null
PY=python3
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  # Ubuntu 22.04 ships 3.10; snipetech needs 3.11+
  apt-get install -y -qq python3.11 python3.11-venv >/dev/null && PY=python3.11
fi
install -d -o "$USER_NAME" -g "$USER_NAME" "$DATA" "$DATA/reports" "$DATA/jobs" "$DATA/inputs"
[ -x "$REPO/.venv/bin/python" ] || "$PY" -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q --upgrade pip >/dev/null
"$REPO/.venv/bin/pip" install -q -e "$REPO"
chown -R "$USER_NAME":"$USER_NAME" "$REPO"

install -d -m 750 /etc/snipetech
if [ ! -f /etc/snipetech/env ]; then
  cat > /etc/snipetech/env <<'ENV'
# Read by every snipetech service. Keep this file private (mode 600).
HELIUS_API_KEY=
OKS_KEY=
# the operator (snipetech operate, every 15 min) needs the Anthropic key; Telegram is optional (decision notes)
ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=
# the live dashboard at https://<host>/dash: any user name, this as the password (8+ characters; empty = no page)
DASH_TOKEN=
SNIPETECH_CONFIG=/etc/snipetech/snipetech.toml
# Helius requests per second snipetech may use (default 40). Lower it when the same Helius plan also
# serves a trading bot on this machine, so snipetech never eats the bot's rate limit:
# SNIPETECH_RPC_RPS=15
ENV
  chmod 640 /etc/snipetech/env
fi
if [ ! -f /etc/snipetech/snipetech.toml ]; then
  "$REPO/.venv/bin/snipetech" --config /etc/snipetech/snipetech.toml init >/dev/null || true
fi
chown -R root:"$USER_NAME" /etc/snipetech && chmod 640 /etc/snipetech/snipetech.toml 2>/dev/null || true
chmod 640 /etc/snipetech/env  # root writes, the service user reads (jobs load new keys without a restart)

# snipetech-ops may restart snipetech's own services after an update, and nothing else.
cat > /etc/sudoers.d/snipetech <<EOF
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart snipetech-tape, /usr/bin/systemctl --no-block restart snipetech-ops, /usr/bin/systemctl --no-block start snipetech-daily
EOF
chmod 440 /etc/sudoers.d/snipetech
visudo -cf /etc/sudoers.d/snipetech >/dev/null || rm -f /etc/sudoers.d/snipetech
getent group systemd-journal >/dev/null && usermod -aG systemd-journal "$USER_NAME" || true  # `remote logs`

for unit in "$REPO"/deploy/systemd/*; do
  sed -e "s#@REPO@#$REPO#g" -e "s#@USER@#$USER_NAME#g" -e "s#@DATA@#$DATA#g" "$unit" > "/etc/systemd/system/$(basename "$unit")"
done

# Ubuntu's own update jobs run gently. 11 Nov 2026 07:12 UTC (and 7 Nov 07:10): the upgrade job held 15.6 GB, the
# machine stood still for 11 minutes and the tape recorder with it. They keep running: at the lowest priority for
# processor and disk, their page cache given back above 3 GB. No MemoryMax (an update killed half-way leaves packages
# half installed). To undo: rm /etc/systemd/system/apt-daily*.service.d/snipetech.conf && systemctl daemon-reload
for unit in apt-daily.service apt-daily-upgrade.service; do
  install -d "/etc/systemd/system/$unit.d"
  cat > "/etc/systemd/system/$unit.d/snipetech.conf" <<'EOF'
[Service]
Nice=19
CPUWeight=10
IOWeight=10
IOSchedulingClass=idle
MemoryHigh=3G
EOF
done
systemctl daemon-reload
echo "Installed. Fill /etc/snipetech/env, then: systemctl enable --now snipetech-tape snipetech-ops snipetech-daily.timer snipetech-operator.timer snipetech-discover.timer snipetech-qualify.timer snipetech-measure.timer snipetech-book.timer snipetech-events snipetech-snapshot.timer snipetech-strategist.timer snipetech-commands.timer"
