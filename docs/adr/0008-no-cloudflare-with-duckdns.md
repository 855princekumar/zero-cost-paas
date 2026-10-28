# ADR-0008: No Cloudflare proxy with DuckDNS

- **Status:** Accepted — 2026-10-28 (revisit when an own domain exists)
- **Related:** [ADR-0006](0006-duckdns-wildcard-dns.md), [ADR-0011](0011-defense-in-depth-network-layers.md), [Production readiness](../15-production-readiness.md)

## Context

Cloudflare's free plan (proxy, WAF, DDoS protection, origin IP hiding) was requested. Cloudflare
only proxies zones whose **nameservers** are delegated to Cloudflare. `duckdns.org` is owned by
DuckDNS; a subdomain user cannot change its nameservers. Cloudflare Tunnel likewise requires a zone
on Cloudflare (quick tunnels on `trycloudflare.com` are random and ephemeral).

## Decision

Do **not** use Cloudflare while on DuckDNS. Compensate at the origin:

- Only ports `<SSH_PORT>`, 80, 443 reachable ([ADR-0011](0011-defense-in-depth-network-layers.md)).
- Brute-force bans + 2FA ([ADR-0012](0012-fail2ban-for-ssh-and-coolify-login.md)).
- Fast patching and automated containment ([ADR-0014](0014-automated-security-guardrails-and-lockdown.md)).

**Upgrade path:** obtain a free Cloudflare-compatible domain (e.g. **eu.org**, approval in 1–4
weeks) or a cheap one, move nameservers to Cloudflare, proxy `<BASE_DOMAIN>` and `*.<BASE_DOMAIN>`,
then restrict 80/443 in the OCI security list to Cloudflare's published IP ranges (origin lockdown).

## Consequences

**Positive**
- Stays $0 and simple; no extra dependency.

**Negative**
- Origin IP is public; no edge WAF/DDoS absorption; volumetric attacks hit the VM directly.
- No edge caching.

## Alternatives considered

| Option | Why not |
|---|---|
| Cloudflare with DuckDNS | Impossible — no nameserver control |
| Cloudflare quick tunnel | Random, ephemeral hostname each run |
| Buy a domain | Violates zero-cost (would be the cleanest solution otherwise) |
