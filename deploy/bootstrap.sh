#!/usr/bin/env bash
# One command puts snipetech on a server and lets your Claude session operate it:
#
#   curl -fsSL https://raw.githubusercontent.com/frieshimself-cpu/snipetech/refs/heads/claude/stoic-babbage-5e6pck/deploy/bootstrap.sh | sudo bash
#
# * installs snipetech under /opt/snipetech and its data under /var/lib/snipetech
# * asks for your Helius and OKTech keys (typing is hidden; they are stored only in /etc/snipetech/env, mode 600)
# * starts the market recorder (every trade of every token), the daily analysis, and snipetech-ops:
#   an HTTPS endpoint at https://snipetech.<your-ip>.sslip.io that only accepts requests signed by the keys
#   in deploy/ops_authorized_keys (public halves only; nothing secret is exchanged)
# * leaves anything else on the server alone: an existing Caddy config gets one site block appended
#   (backed up first, rolled back if it doesn't validate)
#
# Safe to run again: it updates the code and keeps keys and data. SNIPETECH_REKEY=1 asks for the keys again.
# Turn remote operations off:  sudo systemctl disable --now snipetech-ops
set -euo pipefail
REPO_URL="${SNIPETECH_REPO:-https://github.com/frieshimself-cpu/snipetech.git}"
BRANCH="${SNIPETECH_BRANCH:-claude/stoic-babbage-5e6pck}"
APP=/opt/snipetech
ENVF=/etc/snipetech/env
main() {
say() { printf '\n\033[1m>> %s\033[0m\n' "$*"; }

[ "$(id -u)" = 0 ] || { echo "run it with sudo"; exit 1; }
command -v apt-get >/dev/null || { echo "this script needs Ubuntu or Debian"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

say "packages"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates sudo iproute2 gnupg >/dev/null

say "code: $BRANCH"
if [ -d "$APP/.git" ]; then
  git -c safe.directory="$APP" -C "$APP" fetch -q origin "$BRANCH"
  git -c safe.directory="$APP" -C "$APP" checkout -q -B "$BRANCH" "origin/$BRANCH"
  git -c safe.directory="$APP" -C "$APP" reset -q --hard "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO_URL" "$APP"
fi
git -c safe.directory="$APP" -C "$APP" log -1 --format='%h %s'

say "install"
bash "$APP/deploy/setup.sh"

say "keys"
set_key() {  # NAME "prompt"
  local name=$1 prompt=$2 cur val
  cur=$(grep -E "^$name=" "$ENVF" | head -1 | cut -d= -f2- || true)
  if [ -n "$cur" ] && [ "${SNIPETECH_REKEY:-}" != 1 ]; then echo "$name: already set"; return 0; fi
  if [ ! -r /dev/tty ]; then echo "$name: no terminal to ask on; put it in $ENVF yourself"; return 0; fi
  read -r -s -p "$prompt (typing is hidden, Enter to skip): " val </dev/tty; echo
  [ -n "$val" ] || { echo "$name: skipped"; return 0; }
  KEY_NAME="$name" KEY_VAL="$val" python3 - "$ENVF" <<'PY'
import os, sys
path, name, val = sys.argv[1], os.environ["KEY_NAME"], os.environ["KEY_VAL"].strip()
lines, done = [], False
for line in open(path).read().splitlines():
    if line.startswith(name + "="):
        line, done = f"{name}={val}", True
    lines.append(line)
if not done:
    lines.append(f"{name}={val}")
open(path, "w").write("\n".join(lines) + "\n")
PY
  echo "$name: saved"
}
set_key HELIUS_API_KEY "Helius API key"
set_key OKS_KEY "OKTech Sniper API key (make a NEW one in OKTech, with 'Allow starting' on)"
chmod 600 "$ENVF"
if ! grep -qE '^SNIPETECH_RETAIN_DAYS=' "$ENVF"; then
  # the market tape grows ~7 GB/day: keep as many days as fit in ~60% of the disk (3..30)
  total_gb=$(df -BG --output=size /var/lib/snipetech | tail -1 | tr -dc 0-9)
  days=$(( ${total_gb:-0} * 6 / 10 / 7 ))
  [ "$days" -lt 3 ] && days=3
  [ "$days" -gt 30 ] && days=30
  echo "SNIPETECH_RETAIN_DAYS=$days" >> "$ENVF"
  echo "market data kept: last $days days (disk ${total_gb} GB; change SNIPETECH_RETAIN_DAYS in $ENVF)"
fi

check_keys() {
  local h o
  h=$(grep -E '^HELIUS_API_KEY=' "$ENVF" | cut -d= -f2- || true)
  o=$(grep -E '^OKS_KEY=' "$ENVF" | cut -d= -f2- || true)
  if [ -n "$h" ]; then  # keys go to curl on stdin, never on a command line
    if printf 'url = "https://mainnet.helius-rpc.com/?api-key=%s"\n' "$h" \
       | curl -sS -m 15 -K - -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"getSlot"}' \
       | grep -q '"result"'; then echo "Helius key: works"; else echo "Helius key: REJECTED (check it, then re-run with SNIPETECH_REKEY=1)"; fi
  else echo "Helius key: missing (the market recorder needs it)"; fi
  if [ -n "$o" ]; then
    code=$(printf 'header = "Authorization: Bearer %s"\n' "$o" \
       | curl -s -o /dev/null -w '%{http_code}' -m 15 -K - https://oktech.me/api/sniper-api/v1/status || true)
    if [ "$code" = 200 ]; then echo "OKTech key: works"; else echo "OKTech key: HTTP $code (check it, then re-run with SNIPETECH_REKEY=1)"; fi
  else echo "OKTech key: missing (needed to create snipers)"; fi
}
check_keys

say "HTTPS endpoint for remote operations"
IP=$(curl -4 -fsS -m 10 https://api.ipify.org 2>/dev/null || curl -4 -fsS -m 10 https://ifconfig.me 2>/dev/null || true)
HOST=""
[ -n "$IP" ] && HOST="snipetech.$(echo "$IP" | tr . -).sslip.io"
holder443=$(ss -ltnpH 'sport = :443' 2>/dev/null | grep -o 'users:(("[^"]*' | head -1 | cut -d'"' -f2 || true)
holder80=$(ss -ltnpH 'sport = :80' 2>/dev/null | grep -o 'users:(("[^"]*' | head -1 | cut -d'"' -f2 || true)
if [ -z "$HOST" ]; then
  echo "could not find this server's public IPv4 address; skipping the endpoint"
elif [ -n "$holder443" ] && [ "$holder443" != caddy ]; then
  echo "port 443 is already used by '$holder443'; skipping the endpoint (tell Claude what runs there)"
  HOST=""
else
  fresh=0
  if ! command -v caddy >/dev/null; then
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https >/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy >/dev/null
    fresh=1
  fi
  CF=/etc/caddy/Caddyfile
  site=$(printf '%s {\n\treverse_proxy 127.0.0.1:8788\n}\n' "$HOST")
  backup=""; dropin=""
  if [ "$fresh" = 1 ]; then
    { [ -n "$holder80" ] && [ "$holder80" != caddy ] && printf '{\n\tauto_https disable_redirects\n}\n\n'; printf '%s\n' "$site"; } > "$CF"
  elif grep -qE '^\s*import\s+/etc/caddy/conf\.d/' "$CF" 2>/dev/null; then
    # The Caddyfile imports a drop-in directory (survives tools that regenerate the Caddyfile): use it.
    install -d /etc/caddy/conf.d
    dropin=/etc/caddy/conf.d/snipetech.caddy
    printf '# snipetech-ops (written by snipetech bootstrap)\n%s\n' "$site" > "$dropin"
  elif ! grep -q "^$HOST " "$CF" 2>/dev/null; then
    backup="$CF.bak.$(date +%s)"; cp "$CF" "$backup" 2>/dev/null || true
    printf '\n# snipetech-ops (added by snipetech bootstrap)\n%s\n' "$site" >> "$CF"
    echo "added a site block to $CF. If another deploy script rewrites that file, add this line to its template so"
    echo "the block survives:  import /etc/caddy/conf.d/*.caddy   (then re-run this command)"
  fi
  if caddy validate --config "$CF" --adapter caddyfile >/dev/null 2>&1; then
    systemctl enable -q caddy
    systemctl reload caddy 2>/dev/null || systemctl restart caddy
  else
    echo "Caddy rejected the config; restoring and skipping the endpoint"
    [ -n "$backup" ] && cp "$backup" "$CF"
    [ -n "$dropin" ] && rm -f "$dropin"
    HOST=""
  fi
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow 443/tcp >/dev/null; ufw allow 80/tcp >/dev/null
  fi
fi

say "services"
systemctl enable -q --now snipetech-tape snipetech-ops snipetech-daily.timer
systemctl restart snipetech-tape snipetech-ops  # pick up new code on re-runs
sleep 3
for u in snipetech-tape snipetech-ops snipetech-daily.timer; do printf '%-24s %s\n' "$u" "$(systemctl is-active "$u")"; done

ok=0
if [ -n "$HOST" ]; then
  printf 'waiting for the HTTPS certificate'
  for _ in $(seq 1 40); do
    if curl -fsS -m 5 "https://$HOST/health" >/dev/null 2>&1; then ok=1; break; fi
    printf '.'; sleep 3
  done
  echo
fi
echo
echo "=================================================================="
if [ "$ok" = 1 ]; then
  echo " Done. Paste this line to Claude:"
  echo
  echo "   SNIPETECH_OPS_URL=https://$HOST"
elif [ -n "$HOST" ]; then
  echo " Installed, but https://$HOST is not answering yet."
  echo " Usually a firewall in your hosting panel: open TCP 80 and 443 to this server, then re-run this command."
  echo " Paste this line to Claude either way:  SNIPETECH_OPS_URL=https://$HOST"
else
  echo " Installed and recording, but without the remote endpoint (see the messages above)."
fi
echo "=================================================================="
}

# Run only once the whole script has arrived (it is piped through curl); nothing below reads the pipe.
main "$@" </dev/null
