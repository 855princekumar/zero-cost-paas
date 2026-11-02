#!/usr/bin/env python3
"""Security watch for the Coolify VPS. Runs as root from systemd timers; emails via /usr/local/sbin/notify.

  security-watch.py feeds   (every 6h) trusted sources:
      - Coolify releases + Coolify GitHub security advisories (github.com/coollabsio/coolify)
      - GitHub advisories for Laravel / Livewire (PHP) and Traefik
      - CISA Known Exploited Vulnerabilities catalog, filtered to this stack
    Automated: security release or high/critical Coolify advisory -> upgrade Coolify now;
    high/critical advisory with no fix released -> stop the Coolify dashboard (apps keep running).

  security-watch.py host    (every 5 min) intrusion indicators:
      - UID-0 / new login accounts -> locked;  /etc/ld.so.preload (rootkit) -> lockdown
      - crypto-miner processes or binaries running from /dev/shm -> killed + quarantined -> lockdown
      - unknown SSH keys for ubuntu -> removed;  root/sudoers/cron/systemd/sshd file changes -> email
      - root SSH login from a public IP -> lockdown
    Lockdown = stop the Coolify control plane (dashboard, realtime) so an attacker using a Coolify
    RCE loses access; apps, Traefik and your SSH keep working. Undo: sudo security-watch.py unlock

  security-watch.py unlock  restart the Coolify control plane after you have investigated
  security-watch.py status
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

STATE_DIR = "/var/lib/security-watch"
STATE = f"{STATE_DIR}/state.json"
QUARANTINE = f"{STATE_DIR}/quarantine"
CONTROL_PLANE = ["coolify", "coolify-realtime"]
INSTALLER_ENV = {"DOCKER_ADDRESS_POOL_BASE": "172.20.0.0/14", "DOCKER_ADDRESS_POOL_SIZE": "24"}

KEV_KEYWORDS = ["coolify", "laravel", "livewire", "php", "traefik", "docker", "containerd", "runc", "openssh",
                "linux kernel", "ubuntu", "canonical", "postgresql", "redis", "sudo", "glibc", "polkit", "openssl"]
PKG_ADVISORIES = [("composer", "laravel/framework"), ("composer", "livewire/livewire"),
                  ("go", "github.com/traefik/traefik/v3")]
SECURITY_WORDS = re.compile(r"\b(security|vulnerab\w*|cve-\d{4}-\d+|ghsa-|rce|remote code|injection|xss|"
                            r"privilege escalation|auth(entication)? bypass)\b", re.I)
MINER_NAMES = re.compile(r"(xmrig|kinsing|kdevtmpfsi|minerd|cpuminer|xmr-stak|nbminer|t-rex|lolminer|"
                         r"phoenixminer|kthreaddk|sysupdate|networkservice|dbused)", re.I)
WATCHED_FILES = ["/etc/passwd", "/etc/shadow", "/etc/group", "/etc/sudoers", "/etc/sudoers.d/*",
                 "/etc/ssh/sshd_config", "/etc/ssh/sshd_config.d/*", "/root/.ssh/authorized_keys",
                 "/etc/crontab", "/etc/cron.d/*", "/var/spool/cron/crontabs/*",
                 "/etc/systemd/system/*.service", "/etc/systemd/system/*.timer", "/etc/rc.local"]
UBUNTU_KEYS = "/home/ubuntu/.ssh/authorized_keys"


def read_file(path, mode="r", encoding=None):
    with open(path, mode, encoding=encoding) as f:
        return f.read()


def write_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


# ------------------------------------------------------------------ helpers
def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def notify(subject, body, key=None, cooldown=0):
    args = ["/usr/local/sbin/notify"] + (["-k", key, "-c", str(cooldown)] if key else []) + [f"[SECURITY] {subject}"]
    subprocess.run(args, input=body, text=True)


def load():
    try:
        return json.loads(read_file(STATE))
    except Exception:
        return {}


def save(s):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    write_file(tmp, json.dumps(s, indent=1))
    os.replace(tmp, STATE)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "vps-security-watch", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def vkey(v):
    """Version sort key; pre-releases (4.0.0-beta.420) sort before their release."""
    v = v.strip().lstrip("v")
    base, _, pre = v.partition("-")
    nums = lambda s: [int(x) for x in re.findall(r"\d+", s)]
    return (nums(base), 0 if pre else 1, nums(pre))


def in_range(version, rng):
    """GitHub vulnerable_version_range like '>= 4.0.0, < 4.5.1' or '<= 4.0.0-beta.434'."""
    if not rng:
        return True
    for cond in rng.split(","):
        m = re.match(r"\s*(<=|>=|<|>|=)?\s*(\S+)", cond)
        if not m:
            continue
        op, ref = m.group(1) or "=", vkey(m.group(2))
        v = vkey(version)
        if not {"<": v < ref, "<=": v <= ref, ">": v > ref, ">=": v >= ref, "=": v == ref}[op]:
            return False
    return True


def coolify_version():
    return sh("docker inspect coolify --format '{{.Config.Image}}'").rsplit(":", 1)[-1]


def upgrade_coolify(version, reason):
    log = f"{STATE_DIR}/upgrade-{version}.log"
    r = subprocess.run(f"curl -fsSL https://cdn.coollabs.io/coolify/install.sh -o /root/coolify-install.sh && "
                       f"bash /root/coolify-install.sh {version} < /dev/null > {log} 2>&1",
                       shell=True, env={**os.environ, **INSTALLER_ENV})
    now = coolify_version()
    ok = r.returncode == 0 or vkey(now) >= vkey(version)
    notify(f"Coolify auto-upgraded to {now}" if ok else f"Coolify auto-upgrade to {version} FAILED",
           f"Reason: {reason}\nRunning version now: {now}\nLog: {log}\n\n" + sh(f"tail -15 {log}"))
    return ok


def lockdown(reason, s):
    sh("docker stop " + " ".join(CONTROL_PLANE))
    if not s.get("lockdown"):
        s["lockdown"] = {"since": time.strftime("%F %T"), "reason": reason}
        save(s)
    notify("LOCKDOWN: Coolify dashboard stopped",
           f"Automated containment triggered:\n  {reason}\n\n"
           "The Coolify control plane (dashboard, realtime) is stopped so an attacker cannot use it.\n"
           "Your apps, Traefik/HTTPS and your SSH access keep working.\n\n"
           "Investigate (ssh -p 22022), then re-enable with:\n  sudo /usr/local/sbin/security-watch.py unlock\n\n"
           f"Containers now:\n{sh('docker ps --format \"{{.Names}}  {{.Status}}\"')}",
           key="lockdown", cooldown=3600)


# ------------------------------------------------------------------ feeds (every 6h)
def feeds():
    s = load()
    first = "seen" not in s
    seen = set(s.get("seen", []))
    out, running = [], coolify_version()

    # Coolify releases
    rel = [r for r in get("https://api.github.com/repos/coollabsio/coolify/releases?per_page=10")
           if not r["draft"] and not r["prerelease"]]
    latest = rel[0]["tag_name"].lstrip("v") if rel else running
    newer = [r for r in rel if vkey(r["tag_name"]) > vkey(running)]
    sec_rel = [r for r in newer if SECURITY_WORDS.search(r.get("body") or "")]
    for r in newer:
        rid = "rel:" + r["tag_name"]
        if rid not in seen:
            seen.add(rid)
            if not first:
                out.append(f"New Coolify release {r['tag_name']} ({r['published_at'][:10]})"
                           f"{' - mentions SECURITY fixes' if r in sec_rel else ''}\n  {r['html_url']}")

    # Coolify security advisories -> act on high/critical
    action = None
    for a in get("https://api.github.com/repos/coollabsio/coolify/security-advisories?per_page=50&state=published"):
        vulns = a.get("vulnerabilities") or []
        # no version range published -> report only, never auto-act on it
        affected = any(v.get("vulnerable_version_range") and in_range(running, v["vulnerable_version_range"])
                       for v in vulns)
        patched = [p.strip() for v in vulns for p in (v.get("patched_versions") or "").split(",") if p.strip()]
        fixed_by_running = any(vkey(running) >= vkey(p) for p in patched)
        affected = affected and not fixed_by_running
        aid = "ghsa:" + a["ghsa_id"]
        sev = (a.get("severity") or "unknown").lower()
        if aid not in seen:
            seen.add(aid)
            if not first or affected:
                out.append(f"Coolify advisory {a['ghsa_id']} [{sev.upper()}] {a.get('cve_id') or ''}\n  {a['summary']}\n"
                           f"  affects running {running}: {'YES' if affected else 'no'}"
                           f"{'  patched in ' + ', '.join(patched) if patched else '  NO FIX RELEASED'}\n  {a['html_url']}")
        if affected and sev in ("critical", "high"):
            fix = min((p for p in patched if vkey(p) <= vkey(latest)), key=vkey, default=None)
            if fix:
                action = action or ("upgrade", f"{sev} advisory {a['ghsa_id']}: {a['summary']}")
            else:
                action = ("lockdown", f"{sev} advisory {a['ghsa_id']} affects Coolify {running} and no fix is released yet")

    if not action and sec_rel:
        action = ("upgrade", f"Coolify release {sec_rel[0]['tag_name']} contains security fixes")

    # GitHub advisories for the PHP/Traefik stack (informational: Coolify releases ship the fixes)
    for eco, pkg in PKG_ADVISORIES:
        for a in get(f"https://api.github.com/advisories?ecosystem={eco}&affects={pkg}&per_page=10"):
            aid = "ghsa:" + a["ghsa_id"]
            if aid not in seen:
                seen.add(aid)
                if not first and (a.get("severity") or "") in ("high", "critical"):
                    out.append(f"{pkg} advisory {a['ghsa_id']} [{a['severity'].upper()}] {a.get('cve_id') or ''}\n"
                               f"  {a['summary']}\n  {a['html_url']}")

    # CISA Known Exploited Vulnerabilities (actively exploited in the wild)
    kev = get("https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
    for v in kev.get("vulnerabilities", []):
        kid = "kev:" + v["cveID"]
        if kid in seen:
            continue
        seen.add(kid)
        text = f"{v.get('vendorProject', '')} {v.get('product', '')}".lower()
        if not first and any(k in text for k in KEV_KEYWORDS):
            out.append(f"ACTIVELY EXPLOITED (CISA KEV) {v['cveID']}: {v['vendorProject']} {v['product']}\n"
                       f"  {v.get('shortDescription', '')[:300]}\n  Action: {v.get('requiredAction', '')}")

    s["seen"] = sorted(seen)
    s["last_feeds"] = time.strftime("%F %T")
    save(s)

    # A kept AMD test box after the ARM move runs no Coolify: report, never install or lock down here.
    if os.path.exists("/etc/vps-no-coolify") or not running:
        action = None

    if action and action[0] == "upgrade" and vkey(latest) > vkey(running):
        if upgrade_coolify(latest, action[1]) and s.get("lockdown", {}).get("reason", "").startswith("advisory"):
            unlock()
    elif action and action[0] == "lockdown":
        s = load()
        lockdown("advisory: " + action[1], s)
        save(s)

    if first:
        notify("Security watch enabled",
               f"Monitoring started. Coolify running {running}, latest release {latest}.\n"
               f"Baseline recorded: {len(seen)} known releases/advisories/CVEs (only new ones will be emailed).\n\n"
               + ("\n\n".join(out) if out else "No unpatched Coolify advisories affect this version."))
    elif out:
        notify(f"{len(out)} new security item(s)", "\n\n".join(out))


# ------------------------------------------------------------------ host checks (every 5 min)
def sha(path):
    try:
        return hashlib.sha256(read_file(path, "rb")).hexdigest()
    except Exception:
        return None


def snapshot():
    files = sorted({f for pat in WATCHED_FILES for f in glob.glob(pat)})
    return {f: sha(f) for f in files}


def host():
    s = load()
    alerts, critical = [], []

    # keep a lockdown in force: Coolify's containers use restart=always (docker/host restarts revive them)
    if s.get("lockdown") and sh("docker ps -q --filter name=^coolify$"):
        sh("docker stop " + " ".join(CONTROL_PLANE))

    # accounts
    for line in read_file("/etc/passwd").splitlines():
        name, _, uid, *_ = line.split(":")
        if uid == "0" and name != "root":
            sh(f"usermod -L -e 1 {name}; pkill -KILL -u {name}")
            critical.append(f"Extra UID-0 (root) account '{name}' found -> locked and its processes killed")
    logins = sorted(l.split(":")[0] for l in read_file("/etc/passwd").splitlines()
                    if not l.rstrip().endswith(("nologin", "false", "sync")) and l.split(":")[0] != "root")
    known = s.setdefault("logins", logins)
    for u in set(logins) - set(known):
        sh(f"usermod -L -e 1 {u}")
        alerts.append(f"New login-capable account '{u}' -> locked (unlock: sudo usermod -U -e '' {u})")

    # rootkit preload
    if os.path.exists("/etc/ld.so.preload") and read_file("/etc/ld.so.preload").strip():
        critical.append("/etc/ld.so.preload exists (classic rootkit technique):\n" + read_file("/etc/ld.so.preload"))

    # miners / payloads in memory-only dirs
    os.makedirs(QUARANTINE, exist_ok=True)
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
            comm = read_file(f"/proc/{pid}/comm").strip()
            cmd = read_file(f"/proc/{pid}/cmdline", "rb").replace(b"\0", b" ").decode(errors="replace")[:200]
        except Exception:
            continue
        if MINER_NAMES.search(comm) or MINER_NAMES.search(exe) or exe.startswith("/dev/shm/"):
            q = f"{QUARANTINE}/{pid}-{os.path.basename(exe).replace(' (deleted)', '')}"
            try:
                shutil.copy(f"/proc/{pid}/exe", q)
                os.chmod(q, 0)
            except Exception:
                # quarantine is best effort: the process may already be gone; it is killed below either way
                pass
            sh(f"kill -9 {pid}")
            critical.append(f"Malicious-looking process killed: pid {pid} {comm} exe={exe}\n  cmd: {cmd}\n  binary quarantined: {q}")
        elif re.match(r"^/(tmp|var/tmp)/", exe):
            alerts.append(f"Program running from a temp dir: {exe} ({comm})")

    # unknown SSH keys for ubuntu -> revert
    keys = read_file(UBUNTU_KEYS) if os.path.exists(UBUNTU_KEYS) else ""
    base = s.setdefault("ubuntu_keys", keys)
    if keys != base:
        added = [k for k in keys.splitlines() if k.strip() and k not in base.splitlines()]
        if added:
            write_file(UBUNTU_KEYS, base)
            alerts.append("Unknown SSH key(s) added for ubuntu -> removed:\n" + "\n".join(k[:120] for k in added))
        else:
            s["ubuntu_keys"] = keys  # keys removed only: accept

    # root SSH login from a public address
    for line in sh("journalctl -u ssh --since -6min -o cat | grep 'Accepted .* for root from'").splitlines():
        ip = line.split(" from ")[1].split()[0]
        if not re.match(r"^(127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|::1)", ip):
            critical.append(f"Root SSH login from public IP {ip}: {line}")

    # integrity of auth/persistence files (one email per change, then re-baselined)
    snap = snapshot()
    old = s.get("files")
    if old is not None:
        changed = [f for f in snap if old.get(f) != snap[f]]
        removed = [f for f in old if f not in snap]
        if changed or removed:
            alerts.append("Security-relevant files changed (expected after package updates or your own edits):\n"
                          + "\n".join(f"  modified/new: {f}" for f in changed) + "\n"
                          + "\n".join(f"  removed: {f}" for f in removed))
    s["files"] = snap

    if critical:
        lockdown("; ".join(c.splitlines()[0] for c in critical), s)
        notify("CRITICAL intrusion indicators", "\n\n".join(critical + alerts))
    elif alerts:
        notify("Host security alert", "\n\n".join(alerts),
               key="host-" + hashlib.md5("".join(alerts).encode()).hexdigest()[:10], cooldown=21600)
    s["last_host"] = time.strftime("%F %T")
    save(s)


def unlock():
    s = load()
    sh("docker start " + " ".join(CONTROL_PLANE))
    s.pop("lockdown", None)
    save(s)
    print("control plane started")


def status():
    s = load()
    print(json.dumps({k: s.get(k) for k in ("last_feeds", "last_host", "lockdown")}, indent=1),
          f"\nknown items: {len(s.get('seen', []))}, watched files: {len(s.get('files', {}))}")


if __name__ == "__main__":
    {"feeds": feeds, "host": host, "unlock": unlock, "status": status}[sys.argv[1]]()
