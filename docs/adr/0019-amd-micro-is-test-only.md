# ADR-0019: The AMD Micro is a test environment, the A1 VM is production

- **Status:** Accepted: 2026-11-04
- **Related:** [ADR-0004](0004-ampere-a1-target-amd-micro-standin.md), [ADR-0015](0015-self-migrating-amd-to-arm.md), [AMD Micro limits](../20-amd-micro-limits.md)

## Context

ADR-0004 launched an AMD E2.1.Micro as a stand-in while Ampere A1 capacity was unavailable. After
a week of real use the Coolify dashboard felt slow even with **no apps deployed**. Measurement on
the VM showed:

| Metric (default install, idle) | Value |
|---|---|
| Dashboard page, median / p90 | 0.6 to 1.0 s / 2.8 to 3.6 s |
| CPU time withheld by the hypervisor (steal) | 61 % |
| Memory in swap | about 700 MB of 1 GB RAM |

Tuning (slower health checks, fewer idle workers, unused services and one agent plugin off)
improved this to a median of 0.4 to 0.6 s and a p90 of about 1 s, with steal at 24 to 33 %. The
limits are the hardware itself: 1 GB RAM and 1/8 of a guaranteed core.

## Decision

1. Treat the AMD Micro as a **test environment**, tuned with `scripts/tune-small-vm.sh`.
2. Treat the **Ampere A1** VM (2 OCPU / 12 GB) as **production**, running Coolify's defaults. The
   migrator resets the small-VM overrides during restore.
3. After the migration, keep the AMD (both VMs are Always Free) as a test box rather than
   deleting it.

## Consequences

**Positive**
- Clear expectations: slowness on the Micro is known and measured, not a fault to chase.
- Production gets a clean, untuned Coolify on hardware that meets its requirements.
- A free second machine for experiments that cannot affect production.

**Negative**
- Until A1 capacity appears, the only running environment is the slow one.
- Two VMs to patch and watch (both use the same scripts and alerts).
- Keeping the AMD means the A1 boot volume stays at 150 GB instead of 200 GB.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep running production on the Micro, tuned harder | Hardware bound: RAM and CPU credit cannot be tuned away; zram was measured and made it worse |
| Delete the Micro after the move | Loses a free test box; the 50 GB it uses is not needed by the A1 today |
| Pay for a bigger shape | Violates [ADR-0003](0003-stay-free-only-no-payg.md) |
