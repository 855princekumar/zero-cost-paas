# Changelog

## v1.0.0 (2026-10-28): initial production build

**Infrastructure**
- Headless OCI provisioning with the Python SDK: VCN, internet gateway, route table, security list, subnet.
- Always Free AMD E2.1.Micro stand-in running Ubuntu 24.04, Docker, swap and fail2ban via cloud-init.
- Retry across fault domains for Ampere A1 capacity, with a smaller-shape fallback.

**Platform**
- Coolify 4.5.x installed headlessly: admin seeded, registration and API disabled, wildcard app domains.
- DuckDNS dynamic DNS with wildcard sub-domains, updated by a systemd timer.
- Let's Encrypt certificates via Traefik HTTP-01, issued per app automatically.

**Security**
- Public surface is only `<SSH_PORT>`/80/443.
- Coolify internal ports are blocked in DOCKER-USER.
- SSH is key-only on a non-standard port, with root allowed only from Docker and VCN addresses.
- fail2ban guards SSH and the Coolify login (audit log), banning in DOCKER-USER.
- Kernel sysctl hardening, unattended upgrades with auto-reboot, and a mining-pool egress block.
- `security-watch.py` follows Coolify releases and advisories, GitHub advisories and CISA KEV.
- It auto-upgrades Coolify and auto-contains threats (lockdown). Its first run auto-upgraded 4.5.0 → 4.5.2 for an RCE advisory.

**Observability**
- Gmail SMTP alerts and a 5-minute health check with cooldowns.
- Daily report, boot notice, and Coolify notifications with an 80% disk threshold.

**Migration**
- Self-migrating AMD → ARM pipeline: health-gated DNS cutover, detached restore, and finalize on the target.
- Dry-run validated on a throwaway VM. The dry run caught 4 bugs, all fixed.
