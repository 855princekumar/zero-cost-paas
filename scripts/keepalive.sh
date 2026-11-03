#!/bin/bash
# Install the Always Free idle-reclaim guard (keepalive.py). Idempotent; run as root, next to keepalive.py.
#   bash keepalive.sh            install and start (target 25 % CPU)
# Afterwards:  sudo keepalive on | off | status | target <percent>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

install -m 755 "$HERE/keepalive.py" /usr/local/sbin/oci-keepalive.py
[ -f /etc/default/oci-keepalive ] || echo "KEEPALIVE_TARGET=25" > /etc/default/oci-keepalive
NCPU=$(nproc)

cat > /etc/systemd/system/oci-keepalive.service <<EOF
[Unit]
Description=Keep this Always Free VM above Oracle's idle-reclaim threshold (spare CPU only)
After=network-online.target

[Service]
EnvironmentFile=-/etc/default/oci-keepalive
ExecStart=/usr/bin/python3 /usr/local/sbin/oci-keepalive.py
CPUSchedulingPolicy=idle
# idle cgroup: SCHED_IDLE alone only ranks threads inside the service's cgroup; this makes the
# whole group yield to every other cgroup (user sessions, Docker, other services)
CPUWeight=idle
Nice=19
# hard ceiling: never more than half of the machine, whatever happens
CPUQuota=$((NCPU * 50))%
MemoryMax=64M
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateNetwork=yes
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/sbin/keepalive <<'EOF'
#!/bin/bash
# sudo keepalive on | off | status | target <percent>
OFF=/etc/oci-keepalive.off
case "${1:-status}" in
  on)  systemctl enable --now oci-keepalive >/dev/null && rm -f "$OFF" && echo "keepalive ON" ;;
  off) systemctl disable --now oci-keepalive >/dev/null && date -Is > "$OFF"
       echo "keepalive OFF: all spare CPU is yours. Turn it back on within ~5 days if the VM goes quiet:"
       echo "  sudo keepalive on" ;;
  target)
       [[ "${2:-}" =~ ^[0-9]+$ ]] && [ "$2" -ge 21 ] && [ "$2" -le 60 ] || { echo "usage: keepalive target 21..60"; exit 1; }
       sed -i "s/^KEEPALIVE_TARGET=.*/KEEPALIVE_TARGET=$2/" /etc/default/oci-keepalive
       systemctl is-active -q oci-keepalive && systemctl restart oci-keepalive; echo "target set to $2 %" ;;
  status)
       echo "service : $(systemctl is-active oci-keepalive) ($(systemctl is-enabled oci-keepalive 2>/dev/null))"
       echo "target  : $(sed -n 's/^KEEPALIVE_TARGET=//p' /etc/default/oci-keepalive) % CPU (Oracle idle threshold: 20 %, 95th percentile over 7 days)"
       read -r _ a b c d e f g h _ < /proc/stat; sleep 5; read -r _ A B C D E F G H _ < /proc/stat
       busy=$(( (A+B+C+F+G+H) - (a+b+c+f+g+h) )); total=$(( busy + (D+E) - (d+e) ))
       echo "CPU now : $(( total > 0 ? 100 * busy / total : 0 )) % of the whole machine (5 s sample, includes keepalive)"
       [ -f "$OFF" ] && echo "off since: $(cat "$OFF")" ; true ;;
  *) echo "usage: keepalive on|off|status|target <percent>"; exit 1 ;;
esac
EOF
chmod 755 /usr/local/sbin/keepalive

# daily reminder if it was switched off and forgotten (Oracle reclaims after 7 idle days)
cat > /etc/cron.daily/oci-keepalive-reminder <<'EOF'
#!/bin/sh
OFF=/etc/oci-keepalive.off
[ -f "$OFF" ] || exit 0
days=$(( ( $(date +%s) - $(date -r "$OFF" +%s) ) / 86400 ))
[ "$days" -ge 4 ] || exit 0
[ -x /usr/local/sbin/notify ] && printf 'Keepalive has been off for %s days on %s.\nIf this VM is not busy, Oracle may reclaim (stop) it after 7 idle days.\nTurn it back on:  sudo keepalive on\n' \
  "$days" "$(hostname)" | /usr/local/sbin/notify -k keepalive-off -c 86400 "Keepalive is OFF ($days days)"
EOF
chmod 755 /etc/cron.daily/oci-keepalive-reminder

systemctl daemon-reload
if [ -f /etc/oci-keepalive.off ]; then
  echo "keepalive installed (left OFF as requested earlier)"
else
  systemctl enable --now oci-keepalive >/dev/null
  systemctl restart oci-keepalive
  echo "keepalive installed and ON"
fi
