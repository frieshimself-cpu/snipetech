#!/bin/sh
# snipetech network watch: the server repairs its own network setup. Root, every minute
# (snipetech-netwatch.timer; deploy/setup.sh installs this as /usr/local/sbin/snipetech-netwatch).
#
# 12 Nov 2026: the server had no internet for twelve hours while it kept running. On 11 Nov at 07:14 UTC, during an
# 11-minute freeze, systemd-networkd could not set a route in time ("Could not set route: Connection timed out") and
# gave up on the network card ("enp1s0: Failed"). Nothing renewed the address after that; it ran out at 00:31 the next
# night. `networkctl reconfigure` re-applies the setup; nothing on the machine ran it.
#
# A link whose setup has failed, or no IPv4 default route: reconfigure, and if that did not help restart the network
# manager. One repair in ten minutes at most. Says what it did in one or two lines (`remote logs snipetech-netwatch`).

command -v networkctl >/dev/null 2>&1 || exit 0
STAMP="${SNIPETECH_NETWATCH_STAMP:-/run/snipetech-netwatch.last}"

failed_links() { networkctl list --no-legend --no-pager 2>/dev/null | awk '$NF == "failed" {print $2}'; }
default_route() { ip -4 route show default 2>/dev/null; }

failed=$(failed_links)
route=$(default_route)
[ -z "$failed" ] && [ -n "$route" ] && exit 0

now=$(date +%s)
last=$(cat "$STAMP" 2>/dev/null || echo 0)
[ $((now - last)) -lt 600 ] && exit 0
echo "$now" > "$STAMP"

links="$failed"
[ -n "$links" ] || links=$(networkctl list --no-legend --no-pager 2>/dev/null | awk '$3 == "ether" && $NF != "unmanaged" {print $2}')
what="no IPv4 default route"
[ -n "$failed" ] && what="the network manager gave up on $(echo $failed)"
echo "network watch: $what: reconfiguring $(echo $links)"
for link in $links; do
  networkctl reconfigure "$link" || true
done
sleep 20
if [ -n "$(failed_links)" ] || [ -z "$(default_route)" ]; then
  echo "network watch: still broken after the reconfigure: restarting systemd-networkd"
  systemctl restart systemd-networkd || true
else
  echo "network watch: repaired"
fi
exit 0
