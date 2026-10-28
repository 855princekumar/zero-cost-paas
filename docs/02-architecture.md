# 02 · Architecture

[← Case study](01-case-study.md) · [Index](README.md) · [Next: Placeholders →](03-placeholders.md)

> **TL;DR**: One Always Free VM in a dedicated VCN. Traefik (inside Coolify) is the only HTTP(S)
> entry point; it terminates TLS with Let's Encrypt certificates and routes `*.<BASE_DOMAIN>` to app
> containers. Coolify (Laravel/PHP) is the control plane and manages the host as root over an
> **internal** SSH connection. Systemd timers on the host handle DNS updates, health checks,
> security feeds and intrusion checks; Gmail SMTP carries every alert.

![Production architecture](../assets/architecture.svg)

## 1. System context

```mermaid
flowchart TB
    dev(["Developer<br/>(browser + git)"])
    users(["End users"])
    gh[("GitHub<br/>repositories")]
    duck["DuckDNS<br/>(free DNS)"]
    le["Let's Encrypt<br/>(ACME CA)"]
    gmail["Gmail SMTP<br/>(alerts)"]
    feeds["Trusted feeds<br/>GitHub Advisories · CISA KEV"]
    ociapi["OCI API<br/>(signing key only)"]

    subgraph OCI["Oracle Cloud · Always Free · home region"]
        vm["Coolify VPS<br/>Ubuntu 24.04 + Docker"]
    end

    users -- "HTTPS 443" --> vm
    dev -- "HTTPS dashboard (2FA)" --> vm
    dev -- "SSH SSH_PORT (key only)" --> vm
    vm -- "clone / webhooks" --> gh
    vm -- "IP update every 5 min" --> duck
    users -. "resolve *.BASE_DOMAIN" .-> duck
    le -- "HTTP-01 challenge :80" --> vm
    vm -- "SMTP 587 STARTTLS" --> gmail
    vm -- "poll every 6 h" --> feeds
    dev -- "provision / migrate" --> ociapi
    vm -- "self-migration (least-privilege user)" --> ociapi
```

## 2. Components on the VM

```mermaid
flowchart LR
    subgraph Host["Ubuntu 24.04 host"]
        sshd["sshd<br/>22 (internal) · SSH_PORT (public)"]
        f2b["fail2ban<br/>sshd + coolify-login"]
        ipt["iptables<br/>INPUT · DOCKER-USER · OUTPUT"]
        timers["systemd timers<br/>duckdns · health-check · health-report<br/>security-watch host/feeds · apt"]
        msmtp["msmtp + notify"]
        subgraph Docker["Docker Engine (pool 172.20.0.0/14)"]
            traefik["coolify-proxy<br/>Traefik :80/:443"]
            coolify["coolify<br/>Laravel/PHP control plane"]
            rt["coolify-realtime<br/>websockets"]
            db[("coolify-db<br/>PostgreSQL")]
            redis[("coolify-redis")]
            sentinel["coolify-sentinel<br/>metrics"]
            apps["your app containers<br/>(built by coolify-helper)"]
        end
    end
    traefik --> coolify
    traefik --> rt
    traefik --> apps
    coolify --> db
    coolify --> redis
    coolify -- "SSH as root via host.docker.internal:22" --> sshd
    timers --> msmtp
    f2b --> ipt
```

| Component | Role | Notes |
|---|---|---|
| **Traefik** (`coolify-proxy`) | Edge reverse proxy, TLS termination, ACME client | Only container reachable from the internet (80/443) |
| **Coolify** | Control plane: builds, deploys, domains, notifications | PHP/Laravel; dashboard served via Traefik at `https://<BASE_DOMAIN>` |
| **coolify-db / redis** | Coolify state & queues | Never exposed; Postgres dumped during migration |
| **coolify-helper** | Ephemeral build containers | Has the Docker socket → why Coolify RCEs are host-root RCEs |
| **sshd** | Operator access (public `<SSH_PORT>`) + Coolify host management (internal 22) | Root allowed only from local/docker/VCN ranges |
| **fail2ban** | Bans brute force on SSH and the dashboard | Dashboard bans go into `DOCKER-USER` |
| **systemd timers** | DuckDNS, health checks, daily report, security watch, unattended upgrades | All idempotent oneshots |
| **msmtp + notify** | Email delivery via Gmail with per-issue cooldown | Credentials root-only (`/etc/msmtprc`, 0600) |

## 3. Network topology

```mermaid
flowchart TB
    internet((Internet))
    subgraph VCN["VCN free-vm-vcn · 10.0.0.0/16"]
        igw["Internet Gateway"]
        rt["Default route table<br/>0.0.0.0/0 → IGW"]
        sl["Default security list<br/>IN: tcp SSH_PORT, 80, 443 from 0.0.0.0/0<br/>IN: icmp 3/4 (PMTU) from 0.0.0.0/0<br/>IN: all from 10.0.0.0/16<br/>OUT: all"]
        subgraph SUB["Public subnet free-vm-subnet · 10.0.0.0/24"]
            vm["VM · private VM_PRIVATE_IP<br/>public VM_PUBLIC_IP"]
        end
    end
    internet <--> igw <--> rt --> SUB
    sl -. "stateful filter on VNIC" .- vm
```

| Layer | Inbound allowed | Purpose |
|---|---|---|
| OCI security list | `<SSH_PORT>`, 80, 443 (+ ICMP type 3 code 4, + all from VCN) | Cloud perimeter |
| Host `INPUT` (iptables) | 22, `<SSH_PORT>`, 80, 443, then `REJECT` | Oracle image ships a default REJECT |
| `DOCKER-USER` | DROP original ports 8000, 6001, 6002, 8080 from the public NIC; fail2ban dashboard bans | Docker-published ports bypass `INPUT` |
| `OUTPUT` / `DOCKER-USER` | REJECT common mining-pool ports | Egress guard |

### Ports

| Port | Listener | Public? | Why |
|---|---|---|---|
| `<SSH_PORT>` | sshd | Yes | Operator SSH, key only |
| 22 | sshd | No (VCN/Docker only) | Coolify → host management; migration inside the VCN |
| 80 | Traefik | Yes | ACME HTTP-01 + redirect to HTTPS |
| 443 | Traefik | Yes | Dashboard + all apps |
| 8000 → 8080 | Coolify UI (direct) | No | Served through Traefik instead |
| 6001 / 6002 | realtime / terminal | No | Proxied by Traefik at `/app` and `/terminal/ws` |
| 8080 | Traefik dashboard | No | Never public |
| 5432 / 6379 | Postgres / Redis | No | Not published at all |

## 4. Trust boundaries

```mermaid
flowchart LR
    subgraph T0["Untrusted: Internet"]
        anon["anonymous clients<br/>scanners · bots"]
    end
    subgraph T1["Edge: Traefik"]
        tls["TLS termination<br/>host routing"]
    end
    subgraph T2["Authenticated: Coolify dashboard"]
        ui["admin (password + TOTP 2FA)"]
    end
    subgraph T3["Privileged: host root"]
        root["Docker socket · sshd root · systemd"]
    end
    subgraph T4["Cloud control plane"]
        api["OCI API (signing keys)"]
    end
    anon -->|"443 only"| tls
    tls --> ui
    ui -->|"SSH root (internal only)"| root
    root -->|"least-privilege API key (migration only)"| api
```

Crossing **T2 → T3** is the critical boundary: Coolify legitimately holds root on the host. That is
why the design adds 2FA, dashboard brute-force bans, same-day patching of Coolify advisories, and
a **lockdown** that stops the control plane if compromise indicators appear
([10 · Security automation](10-security-automation.md)).

## 5. Data flows

### 5.1 Request flow

![Request flow](../assets/request-flow.svg)

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant D as DuckDNS
    participant SL as OCI Security List
    participant T as Traefik (coolify-proxy)
    participant A as App container
    B->>D: A? myapp.BASE_DOMAIN
    D-->>B: VM_PUBLIC_IP (wildcard)
    B->>SL: TCP 443
    SL->>T: allowed (443)
    T->>T: TLS handshake (Let's Encrypt cert for this host)
    T->>A: HTTP over docker network (router rule Host())
    A-->>T: 200 OK
    T-->>B: 200 OK (HTTPS)
```

### 5.2 Deploy flow

```mermaid
sequenceDiagram
    autonumber
    participant Dev as Developer
    participant GH as GitHub
    participant C as Coolify
    participant H as coolify-helper
    participant T as Traefik
    participant LE as Let's Encrypt
    Dev->>GH: git push
    GH->>C: webhook (GitHub App) / manual deploy
    C->>H: start build (Nixpacks / Dockerfile / image pull)
    H->>H: build image (arm64 on A1, amd64 on AMD)
    H-->>C: image ready
    C->>T: write router labels for https://APP_NAME.BASE_DOMAIN
    T->>LE: ACME order for new host
    LE->>T: HTTP-01 challenge on :80
    T-->>LE: token response
    LE-->>T: certificate (90 days)
    C-->>Dev: deployment notification (email)
```

### 5.3 ACME HTTP-01

```mermaid
sequenceDiagram
    participant T as Traefik
    participant LE as Let's Encrypt
    participant D as DuckDNS
    T->>LE: newOrder(myapp.BASE_DOMAIN)
    LE-->>T: challenge token
    LE->>D: resolve myapp.BASE_DOMAIN
    D-->>LE: VM_PUBLIC_IP
    LE->>T: GET http://myapp.BASE_DOMAIN/.well-known/acme-challenge/token
    T-->>LE: key authorization
    LE-->>T: valid → certificate stored in /traefik/acme.json
```

### 5.4 Alerting flow

```mermaid
flowchart LR
    subgraph Sources
        ce["Coolify events<br/>deploy · disk · unreachable · backups · patches"]
        hc["health-check (5 min)"]
        hr["health-report (daily 09:00)"]
        sw["security-watch host (5 min) / feeds (6 h)"]
        uu["unattended-upgrades"]
        mg["migrator / finalize"]
    end
    ce -->|"Laravel mailer"| g["Gmail SMTP 587"]
    hc --> n["notify<br/>(cooldown keys)"]
    hr --> n
    sw --> n
    mg --> n
    uu -->|"sendmail → msmtp"| g
    n --> m["msmtp"] --> g --> inbox(["ALERT_EMAIL"])
```

## 6. State & persistence

| Data | Location | Backed up / migrated | Notes |
|---|---|---|---|
| Coolify config & secrets (`APP_KEY`, DB/Redis passwords) | `/data/coolify/source/.env` | Yes, migrated; **back up offline** | Losing `APP_KEY` makes encrypted DB fields unreadable |
| Coolify database | Docker volume `coolify-db` | Yes, `pg_dump -Fc` → `pg_restore` | Projects, servers, env vars, notification settings |
| Coolify SSH keys | `/data/coolify/ssh/keys` | Yes, restored after reinstall | Used for root SSH to the host |
| TLS certificates | `/data/coolify/proxy/acme.json` | Yes | Avoids re-issuance after migration |
| App/service compose files | `/data/coolify/applications`, `/services`, `/databases` | Yes | |
| App data | Named Docker volumes | Yes, tarred per volume | Anonymous (hash-named) volumes skipped |
| Coolify logs (audit) | `/data/coolify/logs` (host mount) | - | Feeds fail2ban `coolify-login` |
| Host security state | `/var/lib/security-watch/state.json` | - | Baselines, seen advisories, lockdown flag |

## 7. Lifecycle of the platform

```mermaid
stateDiagram-v2
    [*] --> AMD_StandIn: A1 capacity unavailable
    AMD_StandIn --> Migrating: migrator gets A1 capacity
    Migrating --> AMD_StandIn: any stage fails (retry in 5 min, DNS untouched)
    Migrating --> ARM_Live: health gate passed → DNS cut-over
    ARM_Live --> ARM_Max: finalize deletes AMD, grows disk to 200 GB, resizes to 2/12
    ARM_Max --> Lockdown: critical indicator / unfixable advisory
    Lockdown --> ARM_Max: operator investigates → unlock
    ARM_Max --> [*]
```

[Next: Placeholders →](03-placeholders.md)
