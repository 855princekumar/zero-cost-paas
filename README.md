<div align="center">

<img src="assets/banner.svg" alt="Zero-Cost PaaS: self-hosted Vercel/Netlify on Oracle Cloud Always Free + Coolify" width="100%"/>

# Zero-Cost PaaS

**A production-grade, self-hosted Vercel/Netlify alternative on Oracle Cloud's Always Free tier.**
It costs $0 a month and was provisioned entirely through the OCI API, with no console clicks.
It deploys from Git with automatic HTTPS, emails you alerts, patches itself, and contains threats automatically.

![CI](https://img.shields.io/badge/CI-gitleaks_%C2%B7_shellcheck_%C2%B7_lint-2ea44f?style=flat-square&logo=githubactions&logoColor=white)
![Cost](https://img.shields.io/badge/cost-%240.00%2Fmonth-22c55e?style=flat-square)
![Oracle Cloud](https://img.shields.io/badge/Oracle_Cloud-Always_Free-C74634?style=flat-square&logo=oracle&logoColor=white)
![Coolify](https://img.shields.io/badge/PaaS-Coolify_v4-6b16ed?style=flat-square)
![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04_LTS-E95420?style=flat-square&logo=ubuntu&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Engine-2496ED?style=flat-square&logo=docker&logoColor=white)
![Traefik](https://img.shields.io/badge/Proxy-Traefik-24A1C1?style=flat-square&logo=traefikproxy&logoColor=white)
![Let's Encrypt](https://img.shields.io/badge/TLS-Let's_Encrypt-003A70?style=flat-square&logo=letsencrypt&logoColor=white)
![DuckDNS](https://img.shields.io/badge/DNS-DuckDNS_wildcard-f5c518?style=flat-square)
![Python](https://img.shields.io/badge/IaC-Python_OCI_SDK-3776AB?style=flat-square&logo=python&logoColor=white)
![Bash](https://img.shields.io/badge/Automation-Bash_%2B_systemd-4EAA25?style=flat-square&logo=gnubash&logoColor=white)
![Security](https://img.shields.io/badge/security-defense_in_depth-0ea5e9?style=flat-square)
![2FA](https://img.shields.io/badge/auth-2FA_enforced-14b8a6?style=flat-square)
![Secrets](https://img.shields.io/badge/secrets-gitleaks_scanned-8b5cf6?style=flat-square)
![ADRs](https://img.shields.io/badge/ADRs-18-f59e0b?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

</div>

---

## Contents

| | Section | What you'll find |
|---|---|---|
| 1 | [At a glance](#at-a-glance) | Results in one table |
| 2 | [Architecture](#architecture) | End-to-end diagram and request flow |
| 3 | [What's inside](#whats-inside) | Every capability, grouped |
| 4 | [Quick start](#quick-start) | From an empty Oracle account to deploying apps |
| 5 | [Documentation index](#documentation-index) | 15 chapters, case study to runbook |
| 6 | [Architecture Decision Records](#architecture-decision-records) | Why each choice was made |
| 7 | [Repository layout](#repository-layout) | Scripts and docs map |
| 8 | [Security & privacy](#security--privacy) | How this repo stays secret-free |

---

## At a glance

| Metric | Result |
|---|---|
| Monthly cost | **$0.00**. The tenancy is free-only and cannot be billed: paid shapes are limited to 0, plus a $1 budget alert. |
| Provisioning | **100% API-driven**: OCI Python SDK plus cloud-init. The console was only used to create the API key. |
| Public attack surface | **3 TCP ports**: `<SSH_PORT>`, 80, 443. Verified by an external port scan. |
| Time to HTTPS for a new app | about **2 minutes**, with a per-app Let's Encrypt certificate issued and renewed automatically |
| Patch latency for Coolify security releases | **≤ 6 hours**, applied automatically. The first run upgraded 4.5.0 → 4.5.2 because of an RCE advisory. |
| Host health and intrusion checks | **every 5 minutes**, with email alerts and automated containment |
| Hardware upgrade path | Self-migrating **AMD Micro → Ampere A1**, with a health-gated DNS cutover and no laptop needed |
| Decisions documented | **18 ADRs** |

<p align="center"><img src="assets/cost-breakdown.svg" alt="Cost breakdown" width="90%"/></p>

---

## Architecture

<p align="center"><img src="assets/architecture.svg" alt="End-to-end architecture" width="100%"/></p>

```mermaid
flowchart LR
    dev["Developer<br/>git push"] -->|webhook / deploy| coolify
    user["Visitor"] -->|"https://app.BASE_DOMAIN"| dns["DuckDNS<br/>*.BASE_DOMAIN"]
    dns --> sl
    subgraph oci["Oracle Cloud · home region · Always Free"]
      sl["Security List<br/>SSH_PORT · 80 · 443"] --> fw["Host firewall<br/>iptables INPUT + DOCKER-USER"]
      subgraph vm["Ubuntu 24.04 VM"]
        fw --> traefik["Traefik<br/>Let's Encrypt"]
        traefik --> apps["Your apps<br/>(containers)"]
        traefik --> coolify["Coolify<br/>dashboard + API"]
        coolify --> db[("PostgreSQL<br/>Redis")]
        timers["systemd timers<br/>duckdns · health · security-watch · upgrades"]
      end
    end
    traefik <-->|ACME HTTP-01| le["Let's Encrypt"]
    timers -->|SMTP 587| mail["Gmail<br/>ALERT_EMAIL"]
    timers -->|feeds| feeds["GitHub advisories<br/>CISA KEV"]
    laptop["Laptop<br/>OCI API key"] -->|OCI SDK| oci
```

Details are in [docs/02-architecture.md](docs/02-architecture.md), which covers components, network topology, trust boundaries and sequence diagrams.

---

## What's inside

<table>
<tr><td width="50%" valign="top">

**🏗️ Infrastructure as code (headless)**
- OCI Python SDK provisioning of the VCN, gateway, routes, security list and subnet
- cloud-init first boot: updates, Docker, swap, fail2ban, firewall helper
- Retries for "Out of host capacity" across fault domains, with a smaller-shape fallback
- Read-only pre-flight check of account type, limits and usage

**🚀 Platform (your own Vercel/Netlify)**
- Coolify v4 installed headlessly: admin seeded, sign-up and API off
- Git push to deploy (GitHub App, public repos, Docker images, Compose)
- Wildcard app domains `https://<APP_NAME>.<BASE_DOMAIN>`
- Automatic Let's Encrypt certificates via Traefik, renewed automatically

**🌐 DNS & TLS at $0**
- DuckDNS with wildcard resolution, kept current by a systemd timer
- HTTP → HTTPS redirect and a certificate per host

</td><td width="50%" valign="top">

**🛡️ Security (defense in depth)**
- Only `<SSH_PORT>`/80/443 public, enforced in three firewall layers
- SSH key-only, no root from outside, fail2ban with escalating bans
- Coolify login: 2FA, brute-force bans in `DOCKER-USER`
- sysctl hardening, unattended upgrades with auto-reboot
- Blocking of outbound connections to mining-pool ports

**🤖 Self-defending automation**
- Feeds: Coolify releases and advisories, Laravel/Livewire/Traefik GitHub advisories, CISA KEV
- Auto-upgrade on security fixes, **lockdown** of the control plane when no fix exists
- Intrusion checks: UID-0 accounts, rootkit preload, miners, rogue SSH keys, file integrity

**📬 Observability**
- Gmail SMTP alerts with per-issue cooldowns, a daily health report and a boot notice
- Coolify notifications: deploys, server reachability, disk > 80%, backups

**🔁 Self-migration AMD → ARM**
- Waits for Ampere capacity, then moves Coolify with no data loss
- Health-gated DNS cutover, then the old VM deletes itself and the new one grows to the free maximum

</td></tr>
</table>

<p align="center"><img src="assets/security-layers.svg" alt="Security layers" width="90%"/></p>

---

## Quick start

> Read [docs/03-placeholders.md](docs/03-placeholders.md) first. Every `<PLACEHOLDER>` is explained there.

```bash
# 0. Oracle Cloud account (free) -> create an API signing key -> ~/.oci/config   (docs/04)
python -m venv ~/.oci-venv && ~/.oci-venv/bin/pip install oci
cp scripts/config.example.env keys/config.env && $EDITOR keys/config.env && set -a && . keys/config.env && set +a

# 1. What does my tenancy allow? (read-only)
python scripts/check-account.py

# 2. Network (only <SSH_PORT>/80/443 public) + VM. harden.sh is embedded in cloud-init,
#    so the VM is hardened and reachable on <SSH_PORT> from its first boot             (docs/05, docs/08)
python scripts/provision.py network
python scripts/provision.py micro                       # prints <VM_PUBLIC_IP>

# 3. DNS: create <DUCKDNS_SUBDOMAIN> at duckdns.org, then install the updater with your token (docs/06)
VM="ssh -i keys/oci_vm -p $SSH_PORT ubuntu@<VM_PUBLIC_IP>"
scp -i keys/oci_vm -P $SSH_PORT scripts/*.sh scripts/security-watch.py ubuntu@<VM_PUBLIC_IP>:/tmp/
$VM "sudo DUCKDNS_TOKEN=$DUCKDNS_TOKEN DUCKDNS_DOMAIN=$DUCKDNS_DOMAIN bash /tmp/harden.sh"

# 4. Coolify, headless                                                              (docs/07)
$VM "sudo ADMIN_USERNAME=$ADMIN_USERNAME ADMIN_EMAIL=$ALERT_EMAIL ADMIN_PASSWORD='$ADMIN_PASSWORD' VPS_DOMAIN=$VPS_DOMAIN bash /tmp/coolify-setup.sh"

# 5. Alerts + security automation                                                   (docs/09, docs/10)
$VM "cd /tmp && sudo ALERT_EMAIL=$ALERT_EMAIL VPS_DOMAIN=$VPS_DOMAIN GMAIL_APP_PASSWORD=$GMAIL_APP_PASSWORD bash monitoring.sh \
     && sudo ALERT_EMAIL=$ALERT_EMAIL bash security.sh && rm -f /tmp/*.sh /tmp/security-watch.py"

# 6. (optional) self-migration to Ampere A1                                         (docs/11)
python scripts/create-automation-user.py && bash scripts/deploy-migrator.sh
```

Then open `https://<BASE_DOMAIN>`, change the password, **enable 2FA**, pick **"This machine"**, and deploy.

---

## Documentation index

| # | Chapter | Highlights |
|---|---|---|
| 01 | [Case study](docs/01-case-study.md) | Constraints, discoveries, outcome, lessons |
| 02 | [Architecture](docs/02-architecture.md) | C4-style views, network, trust boundaries, sequences |
| 03 | [Placeholders & config](docs/03-placeholders.md) | Every placeholder explained, plus where secrets live |
| 04 | [OCI API setup](docs/04-oci-api-setup.md) | API key, SDK, account type and limits, without the console |
| 05 | [Provisioning](docs/05-provisioning.md) | VCN, security list, cloud-init, capacity retries |
| 06 | [DNS & TLS](docs/06-dns-and-tls.md) | DuckDNS wildcard, Let's Encrypt, why not Cloudflare |
| 07 | [Coolify](docs/07-coolify.md) | Headless install, configuration, deploying apps |
| 08 | [Security hardening](docs/08-security-hardening.md) | Layers, configs, threat model, scan results |
| 09 | [Monitoring & alerts](docs/09-monitoring-and-alerts.md) | Email pipeline, health checks, daily report |
| 10 | [Security automation](docs/10-security-automation.md) | Vulnerability feeds, guardrails, lockdown |
| 11 | [AMD → ARM migration](docs/11-amd-to-arm-migration.md) | Self-migrating pipeline, dry run, bugs caught |
| 12 | [Cost guardrails](docs/12-cost-guardrails.md) | Why it stays $0, limits, idle reclaim |
| 13 | [Operations runbook](docs/13-operations-runbook.md) | Day-2 tasks, secret rotation, incident response |
| 14 | [Troubleshooting](docs/14-troubleshooting.md) | Every real issue hit, with symptom → cause → fix |
| 15 | [Production readiness](docs/15-production-readiness.md) | Scorecard, gaps, roadmap |

The full index with reading paths is in [docs/README.md](docs/README.md).

---

## Architecture Decision Records

Each significant choice is recorded with its context, the alternatives considered and the consequences: see [docs/adr/](docs/adr/README.md).
Examples: [free-only, no Pay-As-You-Go](docs/adr/0003-stay-free-only-no-payg.md),
[why no Cloudflare with DuckDNS](docs/adr/0008-no-cloudflare-with-duckdns.md),
[defense-in-depth network layers](docs/adr/0011-defense-in-depth-network-layers.md),
[automated lockdown](docs/adr/0014-automated-security-guardrails-and-lockdown.md),
[self-migrating AMD → ARM](docs/adr/0015-self-migrating-amd-to-arm.md).

<p align="center"><img src="assets/automation-matrix.svg" alt="Automation matrix" width="90%"/></p>

---

## Repository layout

```text
.
├── README.md                    ← you are here
├── docs/                        ← 15 chapters + ADRs
│   ├── README.md                   index and reading paths
│   ├── 01-case-study.md … 15-production-readiness.md
│   └── adr/                        18 Architecture Decision Records
├── assets/                      ← SVG infographics (architecture, security, cost, flows)
├── scripts/
│   ├── config.example.env          every variable, as placeholders
│   ├── check-account.py            read-only pre-flight (auth, account type, limits)
│   ├── provision.py                network + VM (OCI SDK)
│   ├── cloud-init.yaml             first boot
│   ├── harden.sh                   SSH, firewall, fail2ban, sysctl, DuckDNS timer
│   ├── coolify-setup.sh            headless Coolify install + configuration
│   ├── monitoring.sh               email alerts, health checks, daily report
│   ├── security.sh                 auto-patching, egress guard, security-watch timers
│   ├── security-watch.py           vulnerability feeds + intrusion guardrails
│   ├── create-automation-user.py   least-privilege IAM identity
│   ├── migrate.py · finalize.py    AMD → ARM self-migration
│   └── deploy-migrator.sh          runs the migration as a service on the VM
├── SECURITY.md · CHANGELOG.md · LICENSE
└── .github/workflows/ci.yml     gitleaks · shellcheck · syntax · link check
```

---

## Security & privacy

- **No secrets or personal data are in this repository.** Everything deployment-specific is a `<PLACEHOLDER>`, documented in [docs/03-placeholders.md](docs/03-placeholders.md).
- `.gitignore` blocks `keys/`, `*.pem`, `*.env` and similar files, and CI runs **gitleaks** on every push.
- See [SECURITY.md](SECURITY.md) for how to report problems and what to rotate.

<div align="center">

*Built for the cost of a domain name you don't have to buy.* · MIT License

</div>
