#!/bin/bash
# Install the oci-migrator service on the AMD stand-in VM so the AMD->ARM move runs without the laptop.
# Run from the repo root:
#   VPS_DOMAIN=<BASE_DOMAIN> DUCKDNS_DOMAIN=<DUCKDNS_SUBDOMAIN> ALERT_EMAIL=<ALERT_EMAIL> bash scripts/deploy-migrator.sh
# Needs keys/vps-automation.config + keys/vps-automation_api_key.pem (least-privilege OCI user)
# and keys/duckdns-token.txt (keys/ is git-ignored).
set -euo pipefail
: "${VPS_DOMAIN:?}" "${DUCKDNS_DOMAIN:?}" "${ALERT_EMAIL:?}"
SSH_PORT=${SSH_PORT:-22022}
cd "$(dirname "$0")/.."
HOST=${HOST:-$VPS_DOMAIN}
SSH="ssh -i keys/oci_vm -p $SSH_PORT -o BatchMode=yes ubuntu@$HOST"
D=/opt/oci-migrator

for f in keys/vps-automation.config keys/vps-automation_api_key.pem keys/duckdns-token.txt; do
  [ -s "$f" ] || { echo "missing $f"; exit 1; }
done

$SSH "sudo install -d -m 700 $D && sudo apt-get install -y -qq python3-venv >/dev/null"
for f in provision.py migrate.py finalize.py harden.sh cloud-init.yaml monitoring.sh security.sh security-watch.py; do
  $SSH "sudo tee $D/$f >/dev/null" < "scripts/$f"
done
$SSH "sudo tee $D/user_key.pub >/dev/null" < keys/oci_vm.pub
$SSH "sudo tee $D/api_key.pem >/dev/null" < keys/vps-automation_api_key.pem
sed "s|^key_file=.*|key_file=$D/api_key.pem|" keys/vps-automation.config | $SSH "sudo tee $D/config >/dev/null"
printf 'DUCKDNS_DOMAIN=%s\nDUCKDNS_TOKEN=%s\n' "$DUCKDNS_DOMAIN" "$(cat keys/duckdns-token.txt)" | $SSH "sudo tee $D/duckdns.env >/dev/null"

$SSH "sudo bash -s" <<EOF
set -e
chmod 600 $D/api_key.pem $D/config $D/duckdns.env
[ -f $D/id_migrate ] || ssh-keygen -q -t ed25519 -N '' -C oci-migrator -f $D/id_migrate
[ -x $D/venv/bin/python ] || (python3 -m venv $D/venv && $D/venv/bin/pip install -q oci)
cat > /etc/systemd/system/oci-migrator.service <<UNIT
[Unit]
Description=Wait for Oracle A1 capacity, then move Coolify from this AMD VM to it
Wants=network-online.target
After=network-online.target docker.service

[Service]
Environment=OCI_CONFIG_FILE=$D/config
Environment=VPS_DOMAIN=$VPS_DOMAIN
Environment=ALERT_EMAIL=$ALERT_EMAIL
Environment=SSH_PORT=$SSH_PORT
ExecStart=$D/venv/bin/python -u $D/migrate.py --source-local --key $D/id_migrate --user-pub $D/user_key.pub --oci-dir $D
Restart=always
RestartSec=300
Nice=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now oci-migrator.service
sleep 20
systemctl --no-pager status oci-migrator.service | head -5
journalctl -u oci-migrator --no-pager -n 5
EOF
