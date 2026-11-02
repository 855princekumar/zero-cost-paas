#!/bin/bash
# Email health monitoring for the Coolify VPS. Idempotent; run as root.
#   ALERT_EMAIL=<ALERT_EMAIL> VPS_DOMAIN=<BASE_DOMAIN> GMAIL_APP_PASSWORD=<GMAIL_APP_PASSWORD> bash monitoring.sh
#   (GMAIL_APP_PASSWORD only the first time; later runs reuse /etc/msmtprc. REPORT_TZ defaults to UTC)
# Installs:
#   notify            send an email:  echo body | notify [-k key -c cooldown_s] "subject"
#   health-check      every 5 min: RAM/swap, disk, Coolify containers, HTTPS, cert expiry,
#                     reboot-required, dashboard brute-force bans -> email (6h cooldown per issue)
#   health-report     daily 09:00 (REPORT_TZ) summary email
set -euo pipefail
MAIL=${ALERT_EMAIL:?set ALERT_EMAIL=<ALERT_EMAIL>}
DOMAIN=${VPS_DOMAIN:?set VPS_DOMAIN=<BASE_DOMAIN>}
REPORT_TZ=${REPORT_TZ:-UTC}

DEBIAN_FRONTEND=noninteractive apt-get install -y -qq msmtp ca-certificates >/dev/null

if [ -n "${GMAIL_APP_PASSWORD:-}" ]; then
  cat > /etc/msmtprc <<EOF
defaults
auth on
tls on
tls_trust_file /etc/ssl/certs/ca-certificates.crt
logfile /var/log/msmtp.log
account gmail
host smtp.gmail.com
port 587
from $MAIL
user $MAIL
password $GMAIL_APP_PASSWORD
account default : gmail
EOF
  chmod 600 /etc/msmtprc
fi
[ -f /etc/msmtprc ] || { echo "no /etc/msmtprc; pass GMAIL_APP_PASSWORD"; exit 1; }

cat > /usr/local/sbin/notify <<EOF
#!/bin/bash
# echo body | notify [-k key -c cooldown_seconds] "subject"
key=""; cooldown=0
while getopts "k:c:" o; do case \$o in k) key=\$OPTARG;; c) cooldown=\$OPTARG;; esac; done
shift \$((OPTIND-1))
if [ -n "\$key" ]; then
  mkdir -p /var/lib/health-alerts; stamp=/var/lib/health-alerts/\$key
  [ -f "\$stamp" ] && [ \$(( \$(date +%s) - \$(stat -c %Y "\$stamp") )) -lt "\$cooldown" ] && exit 0
  touch "\$stamp"
fi
body=\$(cat)
printf 'To: $MAIL\nFrom: VPS \$(hostname) <$MAIL>\nSubject: [\$(hostname)] %s\nContent-Type: text/plain; charset=UTF-8\n\n%s\n\n-- \n\$(hostname) | https://$DOMAIN | %s\n' \
  "\$1" "\$body" "\$(date '+%F %T %Z')" | msmtp -a default $MAIL
EOF
chmod 755 /usr/local/sbin/notify

cat > /usr/local/sbin/health-check <<EOF
#!/bin/bash
# Runs every 5 min. Each problem emails at most once per 6h; recovery clears the cooldown.
CD=21600
alert() { echo "\$3" | /usr/local/sbin/notify -k "\$1" -c \$CD "\$2"; }
ok() { rm -f "/var/lib/health-alerts/\$1"; }

mem_avail=\$(awk '/MemAvailable/{a=\$2}/MemTotal/{t=\$2}END{print int(a*100/t)}' /proc/meminfo)
swap_used=\$(awk '/SwapTotal/{t=\$2}/SwapFree/{f=\$2}END{print (t>0)?int((t-f)*100/t):0}' /proc/meminfo)
if [ "\$mem_avail" -lt 10 ] && [ "\$swap_used" -gt 75 ]; then
  alert memory "LOW MEMORY: \${mem_avail}% RAM free, swap \${swap_used}% used" "\$(free -h)

Top memory users:
\$(ps -eo pid,comm,%mem,rss --sort=-rss | head -8)"
else ok memory; fi

disk=\$(df --output=pcent / | tail -1 | tr -dc 0-9)
if [ "\$disk" -ge 85 ]; then
  alert disk "DISK \${disk}% FULL" "\$(df -h /)

Docker usage:
\$(docker system df 2>&1)

Free space with: sudo docker system prune -af  (or Coolify > Server > Docker cleanup)"
else ok disk; fi

# Coolify, dashboard and certificate checks only where Coolify runs (a kept AMD test box after the
# ARM move has /etc/vps-no-coolify). Disk, memory, reboot and intrusion checks always run.
if [ ! -f /etc/vps-no-coolify ]; then
bad=\$(docker ps -a --filter name=coolify --format '{{.Names}}|{{.Status}}' | awk -F'|' '\$2 !~ /^Up/ || \$2 ~ /unhealthy/' || true)
if [ -n "\$bad" ]; then
  alert containers "COOLIFY CONTAINER DOWN" "Not running/healthy:
\$bad

All containers:
\$(docker ps -a --format '{{.Names}}  {{.Status}}')"
else ok containers; fi

code=\$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 --resolve $DOMAIN:443:127.0.0.1 https://$DOMAIN/login)
if [ "\$code" != 200 ]; then
  alert https "DASHBOARD DOWN (HTTP \$code)" "https://$DOMAIN/login returned \$code from the server itself.
\$(docker ps --format '{{.Names}}  {{.Status}}')"
else ok https; fi

end=\$(echo | timeout 15 openssl s_client -connect 127.0.0.1:443 -servername $DOMAIN 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
if [ -n "\$end" ]; then
  days=\$(( (\$(date -d "\$end" +%s) - \$(date +%s)) / 86400 ))
  if [ "\$days" -lt 14 ]; then
    alert cert "TLS CERTIFICATE EXPIRES IN \$days DAYS" "Certificate for $DOMAIN expires \$end.
Traefik should renew it automatically ~30 days before expiry; check: docker logs coolify-proxy | grep -i acme"
  else ok cert; fi
fi
fi

if [ -f /var/run/reboot-required ]; then
  alert reboot "REBOOT REQUIRED after security updates" "Packages: \$(cat /var/run/reboot-required.pkgs 2>/dev/null | tr '\n' ' ')
Reboot when convenient: sudo reboot  (Coolify and apps restart automatically)"
fi

banned=\$(fail2ban-client status coolify-login 2>/dev/null | awk -F'\t' '/Banned IP list/{print \$2}')
if [ -n "\$banned" ]; then
  alert "ban-\$(echo "\$banned" | md5sum | cut -c1-8)" "DASHBOARD LOGIN BRUTE-FORCE BLOCKED" "These IPs failed the Coolify login 5+ times in 10 min and are banned for 24h:
\$banned

If one is yours: sudo fail2ban-client set coolify-login unbanip <ip>"
fi
EOF
chmod 755 /usr/local/sbin/health-check

cat > /usr/local/sbin/health-report <<EOF
#!/bin/bash
{
echo "== System"; uptime; echo
echo "== Memory"; free -h; echo
echo "== Disk"; df -h / | tail -1; echo
echo "== Docker"; docker system df 2>/dev/null; echo
echo "== Containers"; docker ps -a --format 'table {{.Names}}\t{{.Status}}'; echo
echo "== TLS certificate"
echo | timeout 15 openssl s_client -connect 127.0.0.1:443 -servername $DOMAIN 2>/dev/null | openssl x509 -noout -subject -enddate 2>/dev/null; echo
echo "== Security (last 24h)"
echo "SSH bans now: \$(fail2ban-client status sshd 2>/dev/null | awk -F'\t' '/Currently banned/{print \$2}')   total since restart: \$(fail2ban-client status sshd 2>/dev/null | awk -F'\t' '/Total banned/{print \$2}')"
echo "Dashboard login bans now: \$(fail2ban-client status coolify-login 2>/dev/null | awk -F'\t' '/Currently banned/{print \$2}')"
echo "Failed dashboard logins: \$(cat /data/coolify/logs/audit-\$(date -u +%F).log /data/coolify/logs/audit-\$(date -u -d yesterday +%F).log 2>/dev/null | grep -c login_failed)"
echo "Failed SSH auths: \$(journalctl -u ssh --since -24h 2>/dev/null | grep -cE 'Invalid user|authentication failure|Connection closed by authenticating')"
echo
echo "== Updates"
echo "Pending upgrades: \$(apt list --upgradable 2>/dev/null | grep -c upgradable)"
[ -f /var/run/reboot-required ] && echo "REBOOT REQUIRED" || echo "No reboot required"
echo
if systemctl list-unit-files oci-migrator.service >/dev/null 2>&1; then
  echo "== AMD -> ARM migrator: \$(systemctl is-active oci-migrator)"
  journalctl -u oci-migrator --since -24h --no-pager -o cat 2>/dev/null | tail -3
fi
} | /usr/local/sbin/notify "Daily health report"
EOF
chmod 755 /usr/local/sbin/health-report

for unit in health-check health-report; do
  cat > /etc/systemd/system/$unit.service <<EOF
[Unit]
Description=VPS $unit
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/$unit
EOF
done
cat > /etc/systemd/system/health-check.timer <<'EOF'
[Unit]
Description=VPS health check every 5 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/health-report.timer <<EOF
[Unit]
Description=Daily VPS health report
[Timer]
OnCalendar=*-*-* 09:00:00 $REPORT_TZ
Persistent=true
[Install]
WantedBy=timers.target
EOF
# boot notice: tells you about unexpected reboots/outages
cat > /etc/systemd/system/boot-notify.service <<'EOF'
[Unit]
Description=Email on boot
Wants=network-online.target
After=network-online.target docker.service
[Service]
Type=oneshot
ExecStartPre=/bin/sleep 60
ExecStart=/bin/sh -c 'uptime | /usr/local/sbin/notify "Server booted"'
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now health-check.timer health-report.timer >/dev/null
systemctl enable boot-notify.service >/dev/null
echo "monitoring installed"
