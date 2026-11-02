# 14 · Troubleshooting: Every Real Issue Hit

[← Operations runbook](13-operations-runbook.md) · [Index](README.md) · [Next: Production readiness →](15-production-readiness.md)

> **TL;DR**: These are not hypothetical. Each row happened while building this platform; the fix is
> already in the scripts.

## Cloud & provisioning

| Symptom | Cause | Fix |
|---|---|---|
| `pip install oci-cli` fails building **PyYAML** ("Microsoft Visual C++ 14.0 required") | oci-cli pins an old PyYAML with no wheels for Python 3.14 | Use the **OCI Python SDK** (`pip install oci`) in a venv |
| A1 limit shows **2 OCPU / 12 GB**, not 4 / 24 | Tenancy-specific Always Free limit | Design for 2/12; `ARM_MAX` is configurable |
| `500 InternalError: Out of host capacity` | No free A1 hosts in the home region | Retry loop across fault domains and smaller shapes; AMD stand-in; self-migration |
| `429 Too many requests for the user` | Retrying too fast | 30 s between attempts; treat 429 as transient |
| Launch fails `LimitExceeded` while testing | Two Micros + ARM > 200 GB storage | Compute boot size from remaining allowance; delete test VMs |
| Need serial console without the GUI | VM unreachable during boot | `capture_console_history` → `get_console_history_content` (decode **bytes**) |
| `create_user` fails `400 IdcsConversionError: The primary email must be specified` | Tenancy uses **OCI Identity Domains**, which require an email on every user | `create-automation-user.py` sets one (a `+vps-automation` plus-address of `ALERT_EMAIL`, or `AUTOMATION_USER_EMAIL`) |

## Host networking

| Symptom | Cause | Fix |
|---|---|---|
| Ports 80/443 opened with `iptables -I INPUT 6` still unreachable | REJECT was **rule 5** on this image; ACCEPTs landed after it | `open-ports` finds the REJECT line number dynamically |
| A Docker nginx answers on 80 although `INPUT` has no rule | Published ports are DNAT'd → `FORWARD`, never `INPUT` | Guard containers in `DOCKER-USER` (`--ctorigdstport`) and at the security list |
| Routing weirdness / Docker networks in `10.0.x.0/24` | Coolify default pool `10.0.0.0/8` overlaps VCN `10.0.0.0/16` | `DOCKER_ADDRESS_POOL_BASE=172.20.0.0/14` |
| Connection *timeout* instead of *refused* for a closed port from Windows | ICMP host-prohibited not surfaced by the Windows client | Treat timeouts as "closed or filtered"; test from the VM side too |

## SSH

| Symptom | Cause | Fix |
|---|---|---|
| `Host key verification failed` after moving to `<SSH_PORT>` | `[host]:port` is a new known_hosts entry | `-o StrictHostKeyChecking=accept-new` once |
| Fresh VM never opens `<SSH_PORT>` | `sshd -t` → `Missing privilege separation directory: /run/sshd` (socket activation, no login yet) → `set -e` aborted harden | `mkdir -p /run/sshd` before `sshd -t` |
| Port change not applied | Ubuntu 24.04 socket activation | `systemctl daemon-reload && systemctl restart ssh.socket ssh` |
| SSH to a 1/8-OCPU VM drops mid-command (exit 255) | VM starved during image pulls; keepalives time out | Run long steps with `systemd-run` and poll; `ServerAliveCountMax=10` |

## Coolify

| Symptom | Cause | Fix |
|---|---|---|
| No admin user; log says `Invalid Root User Environment Variables` | Password policy: needs mixed case, digit **and symbol**, not breached | Re-run `RootUserSeeder` with a compliant password; remove `ROOT_*` from `.env` |
| `cd: /data/coolify/source: Permission denied` | Directory is `700` root/9999 | Run inside `sudo bash -c '…'` |
| `tinker --execute` prints only `>` | Script file copied with mode 0600 (`mktemp`) → PHP (non-root) reads nothing → interactive prompt | `chmod 644` before `docker cp` |
| After migration Coolify can't SSH to host | Installer generated a **new** localhost key on the fresh server | Restore migrated keys, `ssh-keygen -y` → root `authorized_keys` |
| Restore script exits 255 with no error | `ssh-keygen -y` on `ssh_key@….lock` fails; with `pipefail` the loop's pipeline fails → `set -e` | Skip `*.lock`/`*.pub`; `\|\| true` inside the loop |
| Restore unit log empty | `StandardOutput=file:` and `StandardError=file:` on the same path truncate each other | Use `append:` for both |
| New app shows certificate error for ~1-2 min | ACME issuance in progress; Traefik serves its default cert | Wait; check `docker logs coolify-proxy \| grep -i acme` |
| Healthy install but proxy missing after restore | Proxy starts asynchronously | `docker restart coolify-proxy`; Coolify also reconciles it |

## Automation & scripting

| Symptom | Cause | Fix |
|---|---|---|
| Polling loop never sees the installer finish | `pgrep -f coolify-install.sh` matched **its own** SSH command line | Poll a marker file or a systemd unit state instead |
| `while/else` resized twice | Python `else` on `while` runs after normal loop exit | Explicit `rebooted` flag |
| Mixed quoting in `python -c` with PHP `\App\…` | `\U` parsed as a unicode escape | Put the script in a file; use raw strings |
| fail2ban never matches dashboard failures | Laravel logs in **UTC** | `logtimezone = UTC` in the jail |
| New daily audit file not watched | Glob evaluated at jail start | Cron reload after UTC midnight |

## Email

| Symptom | Cause | Fix |
|---|---|---|
| Gmail rejects login | Normal password used, or 2-Step Verification off | Create an **App Password** (needs 2SV) |
| No mail from apt | No `sendmail` binary | `msmtp-mta` |
| Too many repeated alerts | Persistent condition | `notify -k <key> -c <seconds>` cooldowns |

[Next: Production readiness →](15-production-readiness.md)
