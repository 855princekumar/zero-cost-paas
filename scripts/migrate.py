"""Move the Coolify VPS from the AMD Micro (free-amd) to an Ampere A1 VM (free-arm).

Runs as the `oci-migrator` systemd service on free-amd (--source-local), so the laptop can be off.
Can also run from a workstation (--source-host) for a dry run.

Stages (every stage is safe to re-run; the service restarts on failure):
  1. launch the target, retrying until Oracle has A1 capacity
  2. wait for first boot (Docker + harden.sh baked into cloud-init)
  3. back up Coolify on the source (DB dump, /data/coolify, app volumes) and stream it to the target
  4. install the same Coolify version on the target and restore into it
  5. health check: HTTPS login page through Traefik with the migrated certificate, Coolify -> host SSH
  6. cutover: DuckDNS -> target, source stops updating DNS, apps/services/databases redeployed (arm64)
  7. hand off finalize.py to the target: delete the source VM, grow disk to 200 GB, resize to 2/12
--dry-run stops after stage 5: DNS and the source VM are never touched.
"""
import argparse
import os
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import provision as P  # noqa: E402

DOMAIN = os.environ["VPS_DOMAIN"]                 # <BASE_DOMAIN>, e.g. myapps.duckdns.org
SSH_PORT = int(os.environ.get("SSH_PORT", "22022"))  # <SSH_PORT>
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
MIGRATOR_DIR = "/etc/oci-migrator"   # on the target, for finalize.py

ap = argparse.ArgumentParser()
ap.add_argument("--source-local", action="store_true", help="running on the source VM itself")
ap.add_argument("--source-host", help="source public IP/host when running remotely (SSH port 22022)")
ap.add_argument("--key", required=True, help="SSH private key authorized on source and target")
ap.add_argument("--user-pub", required=True, help="owner's SSH public key, authorized on the target")
ap.add_argument("--target", default=P.ARM_NAME)
ap.add_argument("--shape", default=P.ARM_SHAPE)
ap.add_argument("--oci-dir", default="/opt/oci-migrator", help="dir with config, api_key.pem, duckdns.env")
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--keep-source", action="store_true",
                help="keep the AMD VM as a test box after cutover (ARM keeps 150 GB instead of 200 GB)")
args = ap.parse_args()
log = P.log

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=10"]


def argv(cmd, host):
    """argv running `cmd` as root, locally (host None) or over SSH as ubuntu + sudo."""
    if host is None:
        return ["sudo", "bash", "-c", cmd] if os.geteuid() else ["bash", "-c", cmd]
    return ["ssh", "-i", args.key, "-p", str(host[1]), *SSH_OPTS, f"ubuntu@{host[0]}", "sudo bash -c " + sh_quote(cmd)]


def sh_quote(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


def run(cmd, host, check=True, input=None, timeout=3600):
    r = subprocess.run(argv(cmd, host), input=input, capture_output=True, timeout=timeout)
    out = r.stdout.decode(errors="replace").strip()
    if check and r.returncode:
        raise RuntimeError(f"failed ({r.returncode}) on {host or 'local'}: {cmd[:120]}\n{out[-2000:]}\n"
                           f"{r.stderr.decode(errors='replace')[-2000:]}")
    return out


def pipe(src_cmd, src, dst_cmd, dst):
    """Stream stdout of src_cmd on src into stdin of dst_cmd on dst."""
    a = subprocess.Popen(argv(src_cmd, src), stdout=subprocess.PIPE)
    b = subprocess.run(argv(dst_cmd, dst), stdin=a.stdout, capture_output=True)
    a.stdout.close()
    if a.wait() or b.returncode:
        raise RuntimeError(f"transfer failed: {src_cmd[:80]} -> {dst_cmd[:80]}\n{b.stderr.decode(errors='replace')[-1000:]}")


SRC = None if args.source_local else (args.source_host, SSH_PORT)


def notify(subject, body="", key=None, cooldown=0):
    """Email via the source VM's notify helper (monitoring.sh); no-op elsewhere."""
    if args.source_local and os.path.exists("/usr/local/sbin/notify"):
        extra = ["-k", key, "-c", str(cooldown)] if key else []
        subprocess.run(["/usr/local/sbin/notify", *extra, f"[MIGRATION] {subject}"], input=body.encode())


# ---------------------------------------------------------------- 1. launch target
# Polite capacity polling, following OCI API guidance: a modest steady rate, random jitter so
# requests never align on a fixed beat, and exponential backoff when the API returns 429.
BASE_INTERVAL = 120                      # s between launch attempts (~720/day; each shape+FD every ~12 min)
JITTER = 0.25                            # +/- 25 %
BACKOFF_START, BACKOFF_MAX = 300, 3600   # on 429: 5 min, doubling, capped at 1 h


def pause(seconds):
    time.sleep(seconds * random.uniform(1 - JITTER, 1 + JITTER))


def launch_target():
    inst = P.instance(args.target)
    if not inst:
        sub, img = P.subnet(), P.ubuntu_image(args.shape)
        used = sum(P.boot_volume(i).size_in_gbs for i in P.comp.list_instances(P.T).data
                   if i.lifecycle_state not in ("TERMINATED", "TERMINATING"))
        is_arm = args.shape == P.ARM_SHAPE
        boot = P.TOTAL_FREE_GB - used if is_arm else P.MICRO_BOOT_GB
        keys = "\n".join(sorted({P.read_file(args.user_pub).strip(), P.read_file(args.key + ".pub").strip()}))
        tries = [(s, fd) for s in P.ARM_TRY_SIZES for fd in P.FAULT_DOMAINS] if is_arm else [(None, None)]
        log(f"launching {args.target} ({args.shape}, {boot} GB boot), polling every ~{BASE_INTERVAL // 60} min")
        n, throttled = 0, 0
        while not inst:
            for size, fd in tries:
                n += 1
                try:
                    inst = P.comp.launch_instance(P.launch_details(
                        args.target, args.shape, sub, img, boot, size, fd, user_data=P.cloud_init(), ssh_keys=keys)).data
                    log("LAUNCHED", inst.id, size, fd)
                    notify(f"Got an Ampere A1 VM ({size[0]} OCPU / {size[1]} GB)" if size else "Target VM launched",
                           "Migration of Coolify has started. You'll get another email when the switch is done.")
                    break
                except P.oci.exceptions.ServiceError as e:
                    if e.status == 429:  # throttled: back off exponentially, never keep hammering
                        throttled += 1
                        wait = min(BACKOFF_START * 2 ** (throttled - 1), BACKOFF_MAX)
                        log(f"attempt {n}: rate limited by OCI (429), backing off ~{wait // 60} min")
                        pause(wait)
                        continue
                    if not P.is_capacity_error(e):
                        raise
                    throttled = 0
                    if n % 30 == 1:  # about once an hour
                        log(f"attempt {n}: {e.message} ({size}, {fd})")
                except Exception as e:  # transient network errors: wait longer than usual
                    log(f"attempt {n}: {type(e).__name__}: {e}")
                    pause(BASE_INTERVAL * 2)
                    continue
                pause(BASE_INTERVAL)
    return P.wait(P.comp, P.comp.get_instance(inst.id), "RUNNING")


def target_host(inst):
    v = P.vnic(inst)
    return (v.private_ip, 22) if args.source_local else (v.public_ip, SSH_PORT)


# ---------------------------------------------------------------- 2. first boot
def wait_first_boot(dst):
    for _ in range(120):
        try:
            if run("test -f /var/log/setup-done && echo yes || echo no", dst, timeout=60) == "yes":
                log("target first boot done")
                return
        except Exception:
            # target not reachable yet (still booting / SSH not up): retry below
            pass
        time.sleep(30)
    raise RuntimeError("target first boot did not finish within 60 min")


# ---------------------------------------------------------------- 3. backup + transfer
BACKUP = r"""
set -euo pipefail
B=/root/migrate; rm -rf $B; mkdir -p $B
docker inspect coolify --format '{{.Config.Image}}' | sed 's/.*://' > $B/version
docker volume ls -q | grep -vE '^coolify-(db|redis)$' | grep -vE '^[0-9a-f]{64}$' > $B/volumes || true
if [ "${STOP_APPS:-0}" = 1 ]; then
  docker ps -q --filter label=coolify.managed=true > $B/stopped || true
  [ -s $B/stopped ] && docker stop $(cat $B/stopped) >/dev/null
fi
docker exec coolify-db pg_dump -U coolify -d coolify -Fc > $B/coolify.dump
echo "backup: coolify $(cat $B/version), $(wc -l < $B/volumes) app volume(s), dump $(du -h $B/coolify.dump | cut -f1)"
"""
RESTART_APPS = "[ -s /root/migrate/stopped ] && docker start $(cat /root/migrate/stopped) >/dev/null; true"


def backup_and_transfer(dst):
    if run("test -f /root/migrate.restored && echo yes || echo no", dst) == "yes":
        log("target already restored; skipping transfer")
        return
    log(run(("STOP_APPS=1 " if not args.dry_run else "") + BACKUP, SRC, timeout=1800))
    try:
        run("rm -rf /root/migrate-in && mkdir -p /root/migrate-in/volumes", dst)
        pipe("tar czf - -C / data/coolify root/migrate", SRC, "tar xzf - -C /root/migrate-in", dst)
        for vol in run("cat /root/migrate/volumes", SRC).split():
            pipe(f"tar czf - -C /var/lib/docker/volumes/{vol}/_data .", SRC,
                 f"mkdir -p /root/migrate-in/volumes/{vol} && tar xzf - -C /root/migrate-in/volumes/{vol}", dst)
    finally:
        run(RESTART_APPS, SRC, check=False)
    log("transfer done")


# ---------------------------------------------------------------- 4. install + restore
RESTORE = r"""
set -euo pipefail
IN=/root/migrate-in
VER=$(cat $IN/root/migrate/version)
healthy() { for i in $(seq 1 120); do [ "$(docker inspect -f '{{.State.Health.Status}}' coolify 2>/dev/null)" = healthy ] && return 0; sleep 5; done; return 1; }
if [ ! -f /root/migrate.restored ]; then
  # start clean if a previous attempt died half-way
  docker rm -f coolify coolify-db coolify-redis coolify-realtime coolify-proxy coolify-sentinel >/dev/null 2>&1 || true
  docker volume rm coolify-db coolify-redis >/dev/null 2>&1 || true
  rm -rf /data/coolify && mkdir -p /data && cp -a $IN/data/coolify /data/coolify
  # the source's small-VM tuning (slow health checks, fewer workers) is not wanted on the ARM
  printf 'services:\n  coolify:\n    volumes:\n      - /data/coolify/logs:/var/www/html/storage/logs\n' > /data/coolify/source/docker-compose.custom.yml
  curl -fsSL https://cdn.coollabs.io/coolify/install.sh -o /root/coolify-install.sh
  DOCKER_ADDRESS_POOL_BASE=172.20.0.0/14 DOCKER_ADDRESS_POOL_SIZE=24 \
    bash /root/coolify-install.sh "$VER" < /dev/null > /root/coolify-install.log 2>&1 || true
  healthy
  # the installer generates a fresh localhost key on a new server; restore the migrated ones
  rm -rf /data/coolify/ssh/keys && cp -a $IN/data/coolify/ssh/keys /data/coolify/ssh/keys
  sed -i '/ coolify$/d' /root/.ssh/authorized_keys
  for f in /data/coolify/ssh/keys/*; do
    case "$f" in *.lock|*.pub) continue ;; esac
    ssh-keygen -y -f "$f" 2>/dev/null || true
  done | awk '{print $1" "$2" coolify"}' | sort -u >> /root/.ssh/authorized_keys
  chown -R 9999:root /data/coolify/{source,ssh,images} && chmod -R 700 /data/coolify/{source,ssh,images}
  docker stop coolify >/dev/null
  docker exec -i coolify-db pg_restore -U coolify -d coolify --clean --if-exists --no-owner < $IN/root/migrate/coolify.dump || true
  for v in $(cat $IN/root/migrate/volumes); do
    docker volume create "$v" >/dev/null
    cp -a $IN/volumes/$v/. /var/lib/docker/volumes/$v/_data/
  done
  docker start coolify >/dev/null
  healthy
  docker exec coolify php artisan cache:clear >/dev/null || true
  docker restart coolify-proxy >/dev/null 2>&1 || true
  touch /root/migrate.restored
fi
healthy && echo "restore: coolify $VER healthy"
"""


def install_and_restore(dst):
    # Runs detached on the target: image pulls can stall a small VM long enough to drop SSH.
    if run("test -f /root/migrate.restored && echo yes || echo no", dst) == "no":
        run("cat > /root/migrate-restore.sh", dst, input=RESTORE.encode())
        run("systemctl is-active -q coolify-restore || { systemctl reset-failed coolify-restore 2>/dev/null; "
            "systemd-run --unit=coolify-restore --property=StandardOutput=append:/root/migrate-restore.log "
            "--property=StandardError=append:/root/migrate-restore.log bash -x /root/migrate-restore.sh; }", dst)
        for _ in range(120):
            time.sleep(30)
            try:
                state = run("systemctl is-active coolify-restore; test -f /root/migrate.restored && echo restored",
                            dst, check=False, timeout=120)
            except Exception:
                continue
            if "restored" in state:
                break
            if "failed" in state or ("inactive" in state and "restored" not in state):
                raise RuntimeError("restore failed:\n" + run("tail -30 /root/migrate-restore.log", dst, check=False))
        else:
            raise RuntimeError("restore did not finish within 60 min")
    log("restore: coolify", run("cat /root/migrate-in/root/migrate/version", dst), "restored on target")


# ---------------------------------------------------------------- 5. health
TINKER = r"""
f=$(mktemp); cat > $f; chmod 644 $f; docker cp $f coolify:/tmp/m.php; rm -f $f
docker exec coolify sh -c 'php artisan tinker --execute="$(cat /tmp/m.php)"'
docker exec -u root coolify rm -f /tmp/m.php
"""


def tinker(php, dst):
    return subprocess.run(argv(TINKER, dst), input=php.encode(), capture_output=True, timeout=600).stdout.decode().strip()


def health(dst):
    for _ in range(30):
        code = run(f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 20 --resolve {DOMAIN}:443:127.0.0.1 "
                   f"https://{DOMAIN}/login", dst, check=False)
        if code == "200":
            break
        time.sleep(20)
    else:
        raise RuntimeError(f"target HTTPS health check failed (last code {code})")
    ssh = tinker('try { echo trim(instant_remote_process(["whoami"], \\App\\Models\\Server::find(0))); }'
                 ' catch (\\Throwable $e) { echo "ERR ".$e->getMessage(); }', dst)
    if not ssh.endswith("root"):
        raise RuntimeError(f"Coolify cannot manage the target host over SSH: {ssh[-300:]}")
    log("health OK: HTTPS login 200 with migrated cert, Coolify->host SSH OK")


# ---------------------------------------------------------------- 6. cutover + redeploy
REDEPLOY_PHP = r"""
$s = \App\Models\InstanceSettings::get(); $was = (bool) $s->is_api_enabled; $s->is_api_enabled = true; $s->save();
$t = \App\Models\User::find(0)->createToken("migrate-redeploy", ["root"], now()->addMinutes(30), 0)->plainTextToken;
echo "WAS=" . ($was ? 1 : 0) . "\nTOKEN=" . $t . "\n";
"""
REDEPLOY = r"""
set -uo pipefail
A="https://%(d)s/api/v1"; R="--resolve %(d)s:443:127.0.0.1"; H="Authorization: Bearer $TOKEN"
for u in $(curl -s $R -H "$H" $A/applications | jq -r '.[].uuid'); do curl -s $R -H "$H" "$A/deploy?uuid=$u&force=true" >/dev/null && echo "redeploy app $u"; done
for u in $(curl -s $R -H "$H" $A/services | jq -r '.[].uuid'); do curl -s $R -H "$H" "$A/services/$u/restart" >/dev/null && echo "restart service $u"; done
for u in $(curl -s $R -H "$H" $A/databases | jq -r '.[].uuid'); do curl -s $R -H "$H" "$A/databases/$u/restart" >/dev/null && echo "restart db $u"; done
echo redeploy-done
"""


def cutover(dst, inst):
    if run("test -f /root/migrate.cutover && echo yes || echo no", dst) == "yes":
        return
    env = P.read_file(os.path.join(args.oci_dir, "duckdns.env"), "rb")
    run("install -d -m 700 /etc/duckdns && cat > /etc/duckdns/env && chmod 600 /etc/duckdns/env", dst, input=env)
    # harden.sh installs the DuckDNS timer once /etc/duckdns/env exists
    run("/usr/local/sbin/harden >/dev/null && systemctl start duckdns.service", dst)
    run("systemctl disable --now duckdns.timer || true", SRC, check=False)
    log("DuckDNS now ->", P.public_ip(inst))

    # same email alerts + security automation as the source
    run("cat > /etc/msmtprc && chmod 600 /etc/msmtprc", dst, input=run("cat /etc/msmtprc", SRC).encode())
    run("install -d -m 700 /root/vps-setup", dst)
    for name in ["monitoring.sh", "security.sh", "security-watch.py", "keepalive.py", "keepalive.sh"]:
        run(f"cat > /root/vps-setup/{name}", dst, input=P.read_file(os.path.join(HERE, name), "rb"))
    env = f"ALERT_EMAIL={ALERT_EMAIL} VPS_DOMAIN={DOMAIN}"
    run(f"{env} bash /root/vps-setup/monitoring.sh && {env} bash /root/vps-setup/security.sh"
        " && bash /root/vps-setup/keepalive.sh", dst, timeout=900)

    out = tinker(REDEPLOY_PHP, dst)
    token = next(line[6:] for line in out.splitlines() if line.startswith("TOKEN="))
    was = next(line[4:] for line in out.splitlines() if line.startswith("WAS=")) == "1"
    try:
        log(run(f"TOKEN='{token}' bash -c " + sh_quote(REDEPLOY % {"d": DOMAIN}), dst, timeout=600))
    finally:
        tinker('\\App\\Models\\User::find(0)->tokens()->where("name","migrate-redeploy")->delete();'
               f'$s=\\App\\Models\\InstanceSettings::get(); $s->is_api_enabled={"true" if was else "false"}; $s->save();',
               dst)
    run("touch /root/migrate.cutover", dst)

    if args.keep_source:
        # retire Coolify on the AMD so it does not compete with the ARM copy; Docker stays for tests
        run("touch /etc/vps-no-coolify && cd /data/coolify/source && docker compose --env-file .env "
            "-f docker-compose.yml -f docker-compose.prod.yml down --remove-orphans; "
            "docker rm -f coolify-proxy coolify-sentinel coolify-proxy-error-pages coolify-proxy-logrotate 2>/dev/null; true",
            SRC, check=False, timeout=600)
        log("source kept as a test box: Coolify stopped on it")


# ---------------------------------------------------------------- 7. finalize hand-off
def hand_off_finalize(dst):
    run(f"install -d -m 700 {MIGRATOR_DIR}", dst)
    for name, mode in [("api_key.pem", "600"), ("config", "600")]:
        run(f"cat > {MIGRATOR_DIR}/{name} && chmod {mode} {MIGRATOR_DIR}/{name}", dst,
            input=P.read_file(os.path.join(args.oci_dir, name), "rb"))
    for name in ["provision.py", "finalize.py", "cloud-init.yaml"]:
        run(f"cat > {MIGRATOR_DIR}/{name}", dst, input=P.read_file(os.path.join(HERE, name), "rb"))
    if args.keep_source:
        run(f"touch {MIGRATOR_DIR}/keep_source", dst)
    run(f"""set -e
sed -i 's|^key_file=.*|key_file={MIGRATOR_DIR}/api_key.pem|' {MIGRATOR_DIR}/config
[ -x {MIGRATOR_DIR}/venv/bin/python ] || (python3 -m venv {MIGRATOR_DIR}/venv && {MIGRATOR_DIR}/venv/bin/pip install -q oci)
cat > /etc/systemd/system/oci-finalize.service <<EOF
[Unit]
Description=Finish AMD->ARM move: delete old VM, grow disk, resize to free-tier max
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
Environment=OCI_CONFIG_FILE={MIGRATOR_DIR}/config
ExecStart={MIGRATOR_DIR}/venv/bin/python -u {MIGRATOR_DIR}/finalize.py
Restart=on-failure
RestartSec=300

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable oci-finalize.service && systemctl start --no-block oci-finalize.service""", dst,
        timeout=900)
    log("finalize handed off to target; this VM will be deleted by it")


def main():
    inst = launch_target()
    dst = target_host(inst)
    log("target", args.target, "running at", dst)
    wait_first_boot(dst)
    backup_and_transfer(dst)
    install_and_restore(dst)
    health(dst)
    if args.dry_run:
        log("DRY RUN complete: target healthy; DNS and source untouched")
        return
    cutover(dst, inst)
    notify("Coolify now runs on the ARM VM",
           f"https://{DOMAIN} now points to {P.public_ip(inst)}. Apps are redeploying for arm64.\n"
           + ("This AMD VM is kept as a test box (Coolify stopped here). The ARM VM keeps 150 GB and resizes to 2 OCPU / 12 GB.\n"
              if args.keep_source else
              "The ARM VM will now delete this AMD VM, grow its disk to 200 GB and resize to 2 OCPU / 12 GB.\n")
           + f"SSH: ssh -i keys/oci_vm -p {SSH_PORT} ubuntu@{DOMAIN}")
    hand_off_finalize(dst)
    if args.source_local:
        # job done: remove the API key and DuckDNS token from this VM and stop the service for good
        # (otherwise systemd would restart it every 5 min; matters when the source is kept)
        subprocess.run(f"shred -u {args.oci_dir}/api_key.pem {args.oci_dir}/duckdns.env; rm -f {args.oci_dir}/config",
                       shell=True)
        subprocess.Popen(["systemctl", "disable", "--now", "oci-migrator.service"])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        notify("Migration step failed (will retry in 5 min)",
               f"The current server keeps serving everything; DNS only switches after health checks pass.\n\n"
               f"{traceback.format_exc()[-3000:]}", key="migrate-fail", cooldown=21600)
        raise
