#!/bin/bash
# Security automation for the Coolify VPS. Idempotent; run as root after monitoring.sh.
#  - security-watch.py: trusted vulnerability feeds (6h) + intrusion checks with auto-containment (5 min)
#  - unattended-upgrades: Ubuntu security + Docker Engine updates daily, email report,
#    automatic reboot at 04:00 (server time) when a kernel/libc update needs it
#  - egress guard: block outbound connections to common crypto-mining pool ports (host + containers)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
MAIL=${ALERT_EMAIL:?set ALERT_EMAIL=<ALERT_EMAIL>}

DEBIAN_FRONTEND=noninteractive apt-get install -y -qq msmtp-mta >/dev/null   # provides sendmail for apt mails
install -m 755 "$HERE/security-watch.py" /usr/local/sbin/security-watch.py

# --- OS + Docker patches ------------------------------------------------------------------
cat > /etc/apt/apt.conf.d/52vps-unattended <<EOF
Unattended-Upgrade::Origins-Pattern { "origin=Docker,label=Docker CE"; };
Unattended-Upgrade::Mail "$MAIL";
Unattended-Upgrade::MailReport "on-change";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
EOF
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
# --- egress guard: mining pools ---------------------------------------------------------
cat > /usr/local/sbin/egress-guard <<'EOF'
#!/bin/sh
PORTS=3333,5555,14433,14444,45560,45700
for ch in OUTPUT DOCKER-USER; do
  iptables -C $ch -p tcp -m multiport --dports $PORTS -j REJECT 2>/dev/null ||
    iptables -I $ch -p tcp -m multiport --dports $PORTS -j REJECT
done
EOF
chmod 755 /usr/local/sbin/egress-guard
cat > /etc/systemd/system/egress-guard.service <<'EOF'
[Unit]
Description=Block outbound crypto-mining pool ports
After=docker.service
Requires=docker.service
PartOf=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/egress-guard
RemainAfterExit=yes
[Install]
WantedBy=docker.service
EOF

# --- security-watch timers ----------------------------------------------------------------
for mode in feeds host; do
  cat > /etc/systemd/system/security-watch-$mode.service <<EOF
[Unit]
Description=Security watch ($mode)
After=docker.service network-online.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/sbin/security-watch.py $mode
EOF
done
cat > /etc/systemd/system/security-watch-host.timer <<'EOF'
[Unit]
Description=Intrusion checks every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/security-watch-feeds.timer <<'EOF'
[Unit]
Description=Vulnerability feeds every 6 hours
[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
RandomizedDelaySec=10min
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now egress-guard.service security-watch-host.timer security-watch-feeds.timer >/dev/null
systemctl restart egress-guard.service
echo "security automation installed"
