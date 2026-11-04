# 20 · AMD Micro Limits: Test Box, Not Production

[← Private access with Tailscale](19-private-access-with-tailscale.md) · [Index](README.md) · [ADRs →](adr/README.md)

> **TL;DR**: The Always Free **AMD E2.1.Micro** (1/8 OCPU burstable, 1 GB RAM) runs Coolify, but
> slowly, even with **no apps deployed**: a dashboard click took a median of **0.6 to 1.0 s** and
> up to **3.6 s** (p90) before tuning, with the hypervisor withholding **61 %** of CPU time.
> Tuning brought that to **0.4 to 0.6 s** median and **about 1 s** p90. That is good enough for
> testing, not for production. The **Ampere A1** VM (2 OCPU / 12 GB) is the production target
> ([ADR 0019](adr/0019-amd-micro-is-test-only.md)); the AMD stays as a test box after the move.

## The hardware gap

| | AMD E2.1.Micro (this box) | Coolify requirement | Ampere A1 (production) |
|---|---|---|---|
| CPU | 2 vCPU, **1/8 OCPU guaranteed**, bursts above that | 2 cores | 2 OCPU, dedicated, no bursting |
| RAM | **1 GB** (952 MB usable) + 4 GB swap file | 2 GB minimum, 4 GB+ recommended | 12 GB |
| Disk | 50 GB boot volume (network block storage) | 30 GB+ | 150 to 200 GB |
| Architecture | x86-64 | either | arm64 |

## Where the time goes

Measured on the reference deployment with **Coolify 4.6.0 and zero apps**:

1. **Every click is a server round trip.** The dashboard (Laravel + Livewire) renders each tab on
   the server; nothing is cached in the browser that would hide a slow server.
2. **The control plane does not fit in RAM.** Coolify's PHP processes (web workers, queue
   workers, scheduler, realtime) plus Postgres, Redis, Traefik, Docker and the Oracle agent need
   well over 1 GB. About **650 to 700 MB lives in swap** at all times, so a page that touches
   code which was swapped out waits for the network disk first. That is why the first click
   after a quiet spell is the slowest.
3. **CPU credit runs out.** Only 1/8 of a core is guaranteed. Background work (Docker health
   checks every 4 to 5 s on five containers, the per-minute Laravel scheduler, Oracle Cloud Agent
   plugins) spends the burst budget, and then the hypervisor throttles the VM. Linux reports that
   as **steal** time. In 5 days of uptime, `dockerd` + `containerd` had used about 5.7 h of CPU and
   the Oracle agent plugins about 13.5 h: on their own, roughly the whole guaranteed baseline.

## Benchmark

Method (run on the VM, so network distance is excluded; any page served through Traefik and PHP works):

```bash
# 20 loads of the login page, 3 s apart, then 60 s of CPU accounting from /proc/stat
for i in $(seq 20); do curl -s -o /dev/null -w "%{time_total}\n" https://<BASE_DOMAIN>/login; sleep 3; done | sort -n
read -r _ a b c d e f g h _ < /proc/stat; sleep 60; read -r _ A B C D E F G H _ < /proc/stat
tot=$(( (A+B+C+D+E+F+G+H)-(a+b+c+d+e+f+g+h) )); echo "steal $(( 100*(H-h)/tot ))%"
```

| Run | Median | p90 | Max | CPU steal (60 s) | Swap used |
|---|---|---|---|---|---|
| Default install, run 1 | 0.59 s | 3.61 s | 4.81 s | not sampled | about 700 MB |
| Default install, run 2 | 1.01 s | 2.79 s | 3.80 s | **61 %** | 709 MB |
| **Tuned**, run 1 | **0.41 s** | **0.98 s** | 2.21 s | **24 %** | 616 MB |
| **Tuned**, run 2 | **0.59 s** | **1.08 s** | 1.98 s | **33 %** | 614 MB |
| *Rejected:* zram compressed swap | 1.7 to 2.8 s | up to 6.5 s | 6.5 s | **57 to 77 %** | n/a |

Burstable shapes are noisy: the same test minutes apart can differ by 2x. Read the table as
ranges, and benchmark again yourself before drawing conclusions.

## What was tuned

[`scripts/tune-small-vm.sh`](../scripts/tune-small-vm.sh) (idempotent; `revert` restores the
defaults) applies the first three rows. Each one is reversible and leaves features intact.

| Change | Why | Risk check |
|---|---|---|
| Health checks for `coolify`, `coolify-db`, `coolify-redis` every **30 s** instead of 5 s, with 2 s probes while starting | Each check runs a process inside the container through `dockerd`; 3 containers × 12 per minute was steady CPU drain | `start_interval` keeps start-up and Coolify's upgrade `--wait` fast; nothing restarts containers on health status |
| `PHP_FPM_PM_MAX_SPARE_SERVERS=2`, `PHP_FPM_PM_MAX_CHILDREN=8` | Up to 10 idle web workers of about 30 MB each were kept around | 8 concurrent dashboard requests is plenty for a single admin |
| `HORIZON_MAX_PROCESSES=2` (default 4) | Queue workers are about 75 MB each; 4 at once during a deploy pushes the VM deep into swap | Jobs queue a little longer instead of thrashing |
| Disabled host services: ModemManager, fwupd, udisks2, multipathd, rpcbind | Unused on a headless single-disk cloud VM | No iSCSI or multipath volumes attached (`iscsiadm -m session`: none) |
| Oracle Cloud Agent: **Run Command** plugin disabled | Largest agent CPU user, and it lets the cloud console run commands as root: disabling it also shrinks the attack surface | Instance Monitoring stays **enabled**; Oracle's idle-reclaim and dashboard metrics depend on it |

All Coolify overrides live in `/data/coolify/source/docker-compose.custom.yml`, the file Coolify's
upgrade script keeps and merges. The migrator **resets that file** on the ARM VM during restore,
so production runs Coolify's defaults.

To disable the plugin without the console (the change reaches the agent within minutes to an hour):

```python
import oci
M = oci.core.models
cfg = oci.config.from_file()
oci.core.ComputeClient(cfg).update_instance("<INSTANCE_OCID>", M.UpdateInstanceDetails(
    agent_config=M.UpdateInstanceAgentConfigDetails(plugins_config=[
        M.InstanceAgentPluginConfigDetails(name="Compute Instance Run Command", desired_state="DISABLED")])))
```

## What was tried and not kept

| Idea | Result | Why |
|---|---|---|
| zram (compressed swap in RAM, zstd, 60 % of RAM) | **Slower**: median 1.7 s, max 6.5 s, steal up to 77 % | Compression costs CPU this VM does not have, and the zram device takes RAM away from the disk cache, so more reads hit the network disk. Reverted. |
| Turning the [keepalive](12-cost-guardrails.md) off | No difference: 0.29 to 1.1 s off versus 0.31 to 1.9 s on, both within noise | It runs in the idle scheduling class and yields to everything else |
| Disabling Cloud Guard Workload Protection | Not done | It is a security control; it stays on |
| PHP-FPM `ondemand` | Not done | Spawning a worker on each request adds latency on exactly the clicks that matter |

## What this means

- **The AMD Micro is the test environment.** Use it for trying images, checking a compose file,
  staging small static sites. Prefer pre-built images: builds (especially Node/Next.js) are very
  slow here and can run out of memory.
- **The Ampere A1 VM is production.** With 12x the RAM and dedicated cores there is no swapping and
  no CPU credit, so none of the tuning above is needed. The migrator moves Coolify there
  automatically when capacity frees up ([chapter 11](11-amd-to-arm-migration.md)) and, by default,
  keeps the AMD as a test box with Coolify stopped.
- Re-run the benchmark after any change on the Micro; on the A1 the same test is the baseline for
  production.

[ADR index →](adr/README.md)
