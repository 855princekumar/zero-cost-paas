# Security Policy

## This repository contains no secrets

Every personal or secret value is a `<PLACEHOLDER>` (see [docs/03-placeholders.md](docs/03-placeholders.md)).
Real keys live only in a local, git-ignored `keys/` directory and in root-only files on the VM.
CI runs [gitleaks](https://github.com/gitleaks/gitleaks) on every push to keep it that way.

If you fork this and deploy it:

1. Keep `keys/` out of git (the provided `.gitignore` does this). Run `git status` before every commit.
2. Change the Coolify admin password after the first login and **enable 2FA**.
3. Back up `/data/coolify/source/.env` offline: its `APP_KEY` decrypts everything Coolify stores.
4. Rotate any secret you ever pasted into a chat, ticket or screenshot (runbook:
   [docs/13-operations-runbook.md](docs/13-operations-runbook.md)).

## Reporting a problem

If you find a security issue in the scripts or the documented setup, please report it privately
(repository **Security** tab → **Report a vulnerability**) instead of opening a public issue.

## Upstream vulnerabilities

The deployed system watches upstream sources itself (Coolify releases and GitHub security
advisories, Laravel/Livewire/Traefik advisories, CISA KEV) and patches or contains automatically.
See [docs/10-security-automation.md](docs/10-security-automation.md).
