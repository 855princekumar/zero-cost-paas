#!/bin/bash
# Host hardening for the Coolify VPS. Idempotent; run as root.
#   DUCKDNS_TOKEN=<DUCKDNS_TOKEN> DUCKDNS_DOMAIN=<DUCKDNS_SUBDOMAIN> [SSH_PORT=22022] bash harden.sh
set -euo pipefail

SSH_PORT=${SSH_PORT:-22022}
DUCKDNS_DOMAIN=${DUCKDNS_DOMAIN:-}

# --- SSH: key-only, no root from outside, public access only on $SSH_PORT ----------------
# Port 22 keeps listening for Coolify (root via host.docker.internal) and VCN-internal use;
# the OCI security list only exposes $SSH_PORT to the internet.
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<EOF
Port 22
Port $SSH_PORT
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PubkeyAuthentication yes
AuthenticationMethods publickey
MaxAuthTries 3
MaxSessions 10
LoginGraceTime 20
X11Forwarding no
AllowAgentForwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
AllowUsers ubuntu root
EOF
# Match blocks must be last in the main config. Coolify manages the host as root over SSH
# from its Docker network, so root keys are allowed only from local/docker/VCN addresses.
sed -i '/^# coolify-root-begin/,/^# coolify-root-end/d' /etc/ssh/sshd_config
cat >> /etc/ssh/sshd_config <<'EOF'
# coolify-root-begin
Match Address 127.0.0.1,::1,172.16.0.0/12,10.0.0.0/16
    PermitRootLogin prohibit-password
# coolify-root-end
EOF
mkdir -p /run/sshd  # absent on a fresh socket-activated boot; sshd -t needs it
sshd -t
systemctl daemon-reload
systemctl restart ssh.socket 2>/dev/null || true
systemctl restart ssh

# --- host firewall (Oracle image REJECTs everything not explicitly allowed) --------------
/usr/local/sbin/open-ports 80 443 "$SSH_PORT"

# Docker-published ports bypass INPUT; block Coolify's internal ports (8000 UI, 6001/6002
# realtime/terminal, 8080 Traefik dashboard) from the internet. They're served via 443 only.
cat > /usr/local/sbin/docker-port-guard <<'EOF'
#!/bin/sh
IF=$(ip route show default | awk '{print $5; exit}')
for p in 8000 6001 6002 8080; do
  iptables -C DOCKER-USER -i "$IF" -p tcp -m conntrack --ctorigdstport "$p" -j DROP 2>/dev/null ||
    iptables -I DOCKER-USER -i "$IF" -p tcp -m conntrack --ctorigdstport "$p" -j DROP
done
EOF
chmod 755 /usr/local/sbin/docker-port-guard
cat > /etc/systemd/system/docker-port-guard.service <<'EOF'
[Unit]
Description=Block Coolify internal ports from the internet
After=docker.service
Requires=docker.service
PartOf=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/docker-port-guard
RemainAfterExit=yes

[Install]
WantedBy=docker.service
EOF
systemctl daemon-reload
systemctl enable --now docker-port-guard.service
systemctl restart docker-port-guard.service

# --- fail2ban: 3 failures in 10 min -> 24h ban, repeat offenders escalate ----------------
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-systemd >/dev/null
cat > /etc/fail2ban/jail.d/sshd.local <<EOF
[DEFAULT]
backend = systemd
bantime = 24h
bantime.increment = true
findtime = 10m
maxretry = 3
ignoreip = 127.0.0.1/8 ::1 10.0.0.0/16 172.16.0.0/12

[sshd]
enabled = true
port = 22,$SSH_PORT
EOF

# Coolify dashboard logins: 5 failures in 10 min -> 24h ban. Coolify's audit log (mounted to the
# host by /data/coolify/source/docker-compose.custom.yml) has the real client IP. HTTPS traffic is
# DNAT'd to Traefik, so the ban goes in DOCKER-USER, not INPUT.
cat > /etc/fail2ban/filter.d/coolify-login.conf <<'EOF'
[Definition]
failregex = "event":"auth\.user\.login_failed","ip":"<HOST>"
datepattern = ^\[%%Y-%%m-%%d %%H:%%M:%%S\]
EOF
mkdir -p /data/coolify/logs
touch "/data/coolify/logs/audit-$(date -u +%F).log"
cat > /etc/fail2ban/jail.d/coolify-login.local <<'EOF'
[coolify-login]
enabled = true
filter = coolify-login
backend = polling
logpath = /data/coolify/logs/audit-*.log
logtimezone = UTC
maxretry = 5
findtime = 10m
bantime = 24h
action = iptables-multiport[name=coolify-login, port="http,https", chain="DOCKER-USER"]
EOF
# audit logs rotate daily at 00:00 UTC; reload shortly after so the new file is watched
echo 'CRON_TZ=UTC
1 0 * * * root touch /data/coolify/logs/audit-$(date -u +\%F).log; fail2ban-client reload coolify-login >/dev/null 2>&1' \
  > /etc/cron.d/coolify-login-f2b
systemctl restart fail2ban

# --- kernel/network hardening ------------------------------------------------------------
cat > /etc/sysctl.d/99-hardening.conf <<'EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.conf.all.log_martians = 1
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
EOF
sysctl -q --system

# --- automatic security updates ----------------------------------------------------------
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

# --- DuckDNS: keep the domain pointed at this VM's public IP (every 5 min) ---------------
if [ -n "${DUCKDNS_TOKEN:-}" ]; then
  : "${DUCKDNS_DOMAIN:?set DUCKDNS_DOMAIN=<DUCKDNS_SUBDOMAIN>}"
  install -d -m 700 /etc/duckdns
  printf 'DUCKDNS_DOMAIN=%s\nDUCKDNS_TOKEN=%s\n' "$DUCKDNS_DOMAIN" "$DUCKDNS_TOKEN" > /etc/duckdns/env
  chmod 600 /etc/duckdns/env
fi
if [ -f /etc/duckdns/env ]; then
  cat > /etc/systemd/system/duckdns.service <<'EOF'
[Unit]
Description=Update DuckDNS record
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/duckdns/env
ExecStart=/bin/sh -c 'curl -fsS --max-time 20 "https://www.duckdns.org/update?domains=$DUCKDNS_DOMAIN&token=$DUCKDNS_TOKEN&ip=" | grep -q OK'
EOF
  cat > /etc/systemd/system/duckdns.timer <<'EOF'
[Unit]
Description=Update DuckDNS every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now duckdns.timer
fi

echo "hardening done"
