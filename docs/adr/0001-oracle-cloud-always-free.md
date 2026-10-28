# ADR-0001: Oracle Cloud Always Free as the host

- **Status:** Accepted — 2026-10-28
- **Deciders:** project owner
- **Related:** [ADR-0003](0003-stay-free-only-no-payg.md), [ADR-0004](0004-ampere-a1-target-amd-micro-standin.md), [Case study](../01-case-study.md), [Cost guardrails](../12-cost-guardrails.md)

## Context

The goal is a **permanent, $0** live Linux environment to test and publish projects the way
Vercel/Netlify do — real public HTTPS, Docker, git deploys — without a trial that expires and
without any chance of a bill.

Free tiers compared at decision time:

| Provider | Free compute | Expires? | Notes |
|---|---|---|---|
| **OCI Always Free** | Ampere A1 Flex (documented up to 4 OCPU / 24 GB) + 2× E2.1.Micro (1/8 OCPU, 1 GB) | **No** | 200 GB block storage, 10 TB/month egress, 2 VCNs, 1 flexible LB, Object Storage |
| AWS Free Tier | t2/t3.micro 1 GB | 12 months (or credit-based) | Bills after the period |
| GCP Free Tier | e2-micro 1 GB | No | Tiny; egress limits |
| Azure | B1s 1 GB | 12 months | Bills after |
| Vercel / Netlify hobby | Serverless only | No | No root, no Docker, no long-running services, ToS limits commercial use |

## Decision

Host the platform on **OCI Always Free** in the tenancy's **home region** (Always Free compute exists
only there). Use the Ampere A1 shape as the long-term server and E2.1.Micro as a stand-in
([ADR-0004](0004-ampere-a1-target-amd-micro-standin.md)).

## Consequences

**Positive**
- Real VM with root, Docker, systemd — anything that runs on Linux runs here.
- Generous egress (10 TB/month) — no surprise bandwidth bills for public sites.
- ARM A1 cores are full physical cores; 1 Gbps network per OCPU.

**Negative**
- **Capacity scarcity:** A1 launches frequently fail with `Out of host capacity` in popular regions.
- **Account-specific limits:** this tenancy's A1 limit was **2 OCPU / 12 GB**, half the documented
  maximum. Always check your own limits via the Limits API before planning.
- **Idle reclaim:** on free-only tenancies, VMs idle (CPU, network and memory < 20 % over 7 days)
  can be stopped.
- **ARM64 architecture:** a minority of Docker images are amd64-only.
- Single home region; no multi-region redundancy for free.

## Alternatives considered

| Option | Why not |
|---|---|
| AWS/GCP/Azure free tiers | 1 GB RAM, time-limited, or bill after expiry |
| Serverless (Vercel/Netlify) | No Docker/root, no databases or workers, vendor lock-in — the thing we are replacing |
| Home server + tunnel | Depends on home power/ISP, exposes home network, not "cloud" |
| Paid VPS (~$5/mo) | Violates the zero-cost constraint |
