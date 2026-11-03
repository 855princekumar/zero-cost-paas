#!/usr/bin/env python3
"""Keep an Always Free VM above Oracle's idle-reclaim threshold, using only spare CPU.

Oracle may reclaim (stop) an Always Free VM when, over 7 days, its 95th-percentile CPU, network
and (on Ampere A1) memory utilization all stay under 20 %. Keeping any one of them above 20 % is
enough. This tops CPU up to KEEPALIVE_TARGET (default 25 %) with one worker per vCPU:

  - workers run at SCHED_IDLE, the lowest Linux CPU class: any real process always wins,
  - every 5 s each worker measures what everything else already uses and only adds the
    difference, so on a busy server it does nothing,
  - duty-cycled in 100 ms windows, so the load is smooth (what the 95th percentile sees).

Toggle with `sudo keepalive on|off|status|target N` (installed by keepalive.sh).
Standard library only; runs unprivileged under systemd (DynamicUser).
"""
import multiprocessing as mp
import os
import time

TARGET = min(max(float(os.environ.get("KEEPALIVE_TARGET", "25")), 0), 90) / 100
WINDOW = 0.1   # seconds per duty-cycle window
ADJUST = 5     # seconds between measurements


def machine_cpu():
    """(busy, total) jiffies for the whole machine. Steal counts as busy: on burstable shapes the
    hypervisor throttling shows up as steal, and Oracle sees that time as used."""
    with open("/proc/stat") as f:
        user, nice, system, idle, iowait, irq, softirq, steal = (int(x) for x in f.readline().split()[1:9])
    busy = user + nice + system + irq + softirq + steal
    return busy, busy + idle + iowait


def worker():
    try:
        os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
    except (AttributeError, PermissionError, OSError):
        os.nice(19)
    duty = TARGET
    b0, t0 = machine_cpu()
    p0, w0 = time.process_time(), time.monotonic()
    while True:
        start = time.monotonic()
        end_busy = start + duty * WINDOW
        while time.monotonic() < end_busy:
            pass
        rest = WINDOW - (time.monotonic() - start)
        if rest > 0:
            time.sleep(rest)

        now = time.monotonic()
        if now - w0 >= ADJUST:
            b1, t1 = machine_cpu()
            p1 = time.process_time()
            if t1 > t0:
                system = (b1 - b0) / (t1 - t0)          # whole-machine busy fraction
                own = (p1 - p0) / (now - w0)            # this worker's share of one core ~ all workers' machine share
                others = max(0.0, system - own)
                wanted = min(TARGET, max(0.0, TARGET - others))
                duty = 0.7 * duty + 0.3 * wanted        # smooth, no oscillation
            b0, t0, p0, w0 = b1, t1, p1, now


if __name__ == "__main__":
    n = os.cpu_count() or 1
    procs = [mp.Process(target=worker, daemon=True) for _ in range(n)]
    for p in procs:
        p.start()
    print(f"keepalive: target {TARGET:.0%} CPU on {n} vCPU at SCHED_IDLE", flush=True)
    while all(p.is_alive() for p in procs):
        time.sleep(30)
    raise SystemExit("a worker exited")  # systemd restarts the service
