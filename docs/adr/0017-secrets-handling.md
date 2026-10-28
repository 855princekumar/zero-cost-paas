# ADR-0017: Secrets handling

- **Status:** Accepted — 2026-10-28
- **Related:** [ADR-0016](0016-least-privilege-automation-identity.md), [Placeholders](../03-placeholders.md), [Runbook — rotation](../13-operations-runbook.md)

## Context

The build touches eight secrets: OCI admin API key, automation API key, SSH key, DuckDNS token,
Gmail App Password, Coolify admin password, Coolify `APP_KEY`/DB/Redis passwords, and temporary
Coolify API tokens. Some subtle leak paths were identified:

- **Cloud-init `user_data` is readable from inside the VM** via the instance metadata service
  (`169.254.169.254`) — by any process, including containers. Secrets must never be in user_data.
- Coolify's headless install puts `ROOT_USERNAME/ROOT_USER_EMAIL/ROOT_USER_PASSWORD` into
  `/data/coolify/source/.env`.
- Command-line arguments are visible in `ps` to other users.

## Decision

| Secret | Laptop | VM | Notes |
|---|---|---|---|
| OCI admin API key | `keys/`, `~/.oci/` (user-only ACL) | **never** | |
| Automation API key | `keys/` | `/opt/oci-migrator` → `/etc/oci-migrator` (600) | shredded after finalize |
| SSH private key | `keys/` | never | public key via instance metadata only |
| DuckDNS token | `keys/` | `/etc/duckdns/env` (600) | passed over SSH **stdin**, not argv |
| Gmail App Password | `keys/` | `/etc/msmtprc` (600), Coolify DB | stdin; revocable at Google |
| Coolify admin password | `keys/` (delete after change) | hashed in DB | `ROOT_*` lines **removed** from `.env` after seeding |
| Coolify `APP_KEY` etc. | offline backup recommended | `/data/coolify/source/.env` | needed to decrypt the DB on restore |
| Temporary API tokens | — | DB | created with 30–60 min expiry, **revoked** after use; API re-disabled |

Repository rules: `keys/`, `*.pem`, `*.env` (except `*.example.env`), `config` are git-ignored; a CI
secret scan (gitleaks) runs on every push; documentation uses `<PLACEHOLDERS>` only.

## Consequences

**Positive**
- Repository is safe to share; VM compromise exposes only what that VM needs.

**Negative**
- `keys/` is a single local point of failure → keep an encrypted offline backup.
- Gmail App Password and DuckDNS token remain long-lived; rotate on any suspicion.

## Alternatives considered

| Option | Why not |
|---|---|
| OCI Vault | Free tier available, but adds an API dependency at boot for small benefit here |
| SOPS/age-encrypted secrets in repo | Viable for teams; overkill for a single owner, and repo is meant to be shareable |
| Secrets in cloud-init | Leaks via metadata service |
