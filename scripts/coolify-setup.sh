#!/bin/bash
# Headless Coolify install + configuration (no browser needed until your first login). Run as root on the VM:
#   ADMIN_USERNAME=<ADMIN_USERNAME> ADMIN_EMAIL=<ALERT_EMAIL> ADMIN_PASSWORD='<ADMIN_PASSWORD>' \
#   VPS_DOMAIN=<BASE_DOMAIN> [COOLIFY_VERSION=4.5.2] bash coolify-setup.sh
#
# What it does (each step was verified on a live Always Free VM, see docs/07-coolify.md):
#   1. installs Coolify with a Docker address pool that does not overlap the VCN (ADR-0009)
#   2. creates the admin account (password needs upper+lower+digit+symbol or the seeder rejects it)
#   3. removes the ROOT_* credentials from Coolify's .env again (ADR-0017)
#   4. sets the dashboard domain, the wildcard domain for apps, disables registration and the API
#   5. mounts Coolify's audit log on the host for fail2ban (ADR-0012)
set -euo pipefail
: "${ADMIN_USERNAME:?}" "${ADMIN_EMAIL:?}" "${ADMIN_PASSWORD:?}" "${VPS_DOMAIN:?}"
SRC=/data/coolify/source

healthy() { for _ in $(seq 1 120); do [ "$(docker inspect -f '{{.State.Health.Status}}' coolify 2>/dev/null)" = healthy ] && return 0; sleep 5; done; return 1; }

tinker() {  # run PHP inside the Coolify app container (file must be world-readable for the app user)
  local f; f=$(mktemp); cat > "$f"; chmod 644 "$f"
  docker cp "$f" coolify:/tmp/setup.php; rm -f "$f"
  docker exec coolify sh -c 'php artisan tinker --execute="$(cat /tmp/setup.php)"'
  docker exec -u root coolify rm -f /tmp/setup.php
}

# 1-2. install (the installer seeds the admin from ROOT_* variables)
curl -fsSL https://cdn.coollabs.io/coolify/install.sh -o /root/coolify-install.sh
env ROOT_USERNAME="$ADMIN_USERNAME" ROOT_USER_EMAIL="$ADMIN_EMAIL" ROOT_USER_PASSWORD="$ADMIN_PASSWORD" \
    DOCKER_ADDRESS_POOL_BASE=172.20.0.0/14 DOCKER_ADDRESS_POOL_SIZE=24 \
    bash /root/coolify-install.sh ${COOLIFY_VERSION:-} < /dev/null
healthy

if [ "$(docker exec coolify-db psql -U coolify -d coolify -Atc 'select count(*) from users')" = 0 ]; then
  # seeder failed (usually the password policy); retry with the variables passed directly
  docker exec -e ROOT_USERNAME="$ADMIN_USERNAME" -e ROOT_USER_EMAIL="$ADMIN_EMAIL" -e ROOT_USER_PASSWORD="$ADMIN_PASSWORD" \
    coolify php artisan db:seed --class=RootUserSeeder --force
fi

# 3. no plaintext admin credentials left on disk
sed -i '/^ROOT_USERNAME=/d;/^ROOT_USER_EMAIL=/d;/^ROOT_USER_PASSWORD=/d' "$SRC/.env"

# 4. dashboard + wildcard domain, registration and API off, proxy config regenerated
tinker <<EOF
\$s = \App\Models\InstanceSettings::get();
\$s->fqdn = 'https://$VPS_DOMAIN';
\$s->is_registration_enabled = false;
\$s->is_api_enabled = false;
\$s->save();
\$srv = \App\Models\Server::find(0);
\$srv->settings->wildcard_domain = 'https://$VPS_DOMAIN';
\$srv->settings->save();
\$srv->setupDynamicProxyConfiguration();
echo "fqdn=" . \$s->fqdn . " users=" . \App\Models\User::count() . PHP_EOL;
EOF

# 5. audit log on the host (survives upgrades: upgrade.sh includes docker-compose.custom.yml)
mkdir -p /data/coolify/logs
docker cp coolify:/var/www/html/storage/logs/. /data/coolify/logs/ 2>/dev/null || true
chown -R 9999:root /data/coolify/logs
cat > "$SRC/docker-compose.custom.yml" <<'YML'
services:
  coolify:
    volumes:
      - /data/coolify/logs:/var/www/html/storage/logs
YML
(cd "$SRC" && docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml \
   -f docker-compose.custom.yml up -d coolify)
healthy

echo "Coolify ready: https://$VPS_DOMAIN  (log in, change the password, enable 2FA)"
echo "Back up $SRC/.env offline: its APP_KEY decrypts every secret Coolify stores."
