#!/bin/bash
# Make Coolify usable on a 1 GB / burstable VM (the AMD E2.1.Micro test box). Idempotent; run as root.
#   sudo bash tune-small-vm.sh          apply
#   sudo bash tune-small-vm.sh revert   restore Coolify's defaults (keeps the logs mount)
# Not for the A1 production VM: it has the RAM and CPU for Coolify's defaults.
# Measured effect and reasoning: docs/20-amd-micro-limits.md
set -euo pipefail
SRC=/data/coolify/source
CUSTOM=$SRC/docker-compose.custom.yml
SERVICES="ModemManager fwupd fwupd-refresh.timer udisks2 multipathd multipathd.socket rpcbind rpcbind.socket"

if [ "${1:-}" = revert ]; then
  printf 'services:\n  coolify:\n    volumes:\n      - /data/coolify/logs:/var/www/html/storage/logs\n' > "$CUSTOM"
else
  [ -f "$CUSTOM" ] && cp -a "$CUSTOM" "/root/docker-compose.custom.yml.bak-$(date +%Y%m%d%H%M)"
  cat > "$CUSTOM" <<'YML'
# Local tuning for a small (1 GB / burstable) VM. Kept by Coolify upgrades.
# Health checks every 30 s instead of 5 s (fast 2 s probes only while starting),
# fewer idle PHP-FPM workers, at most 2 queue workers.
services:
  coolify:
    volumes:
      - /data/coolify/logs:/var/www/html/storage/logs
    environment:
      PHP_FPM_PM_MAX_CHILDREN: "8"
      PHP_FPM_PM_MAX_SPARE_SERVERS: "2"
      HORIZON_MAX_PROCESSES: "2"
    healthcheck:
      interval: 30s
      start_interval: 2s
  postgres:
    healthcheck:
      interval: 30s
      start_period: 1m
      start_interval: 2s
  redis:
    healthcheck:
      interval: 30s
      start_period: 1m
      start_interval: 2s
YML
  # host services a headless single-disk cloud VM never uses (modem, firmware updates, desktop
  # disk manager, SAN multipath, NFS portmapper)
  # shellcheck disable=SC2086
  systemctl disable --now $SERVICES >/dev/null 2>&1 || true
fi

cd "$SRC"
FILES="-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.custom.yml"
# shellcheck disable=SC2086
docker compose --env-file .env $FILES config -q
# same image tag as the running container, so this never upgrades or downgrades Coolify
# shellcheck disable=SC2086
LATEST_IMAGE=$(docker inspect -f '{{.Config.Image}}' coolify | sed 's/.*://') \
  docker compose --env-file .env $FILES up -d --remove-orphans
echo "applied; Coolify restarts (1 to 4 minutes on a Micro). Check: docker ps"
