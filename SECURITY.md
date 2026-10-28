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

## Repository security controls

| Control | How | When |
|---|---|---|
| Secret scanning | gitleaks over the full git history (`ci.yml`) | Every push and pull request |
| Code scanning | CodeQL `security-and-quality` queries for Python and GitHub Actions (`codeql.yml`); the build fails on any finding | Every push, pull request and weekly |
| Dependency alerts | Dependabot alerts (enabled) | Continuously |
| Dependency security fixes | Dependabot security updates open pull requests automatically (enabled) | When an advisory affects a dependency |
| Dependency freshness | Dependabot version updates for GitHub Actions and `scripts/requirements.txt` (`dependabot.yml`) | Weekly |
| Script quality | shellcheck, Python syntax, cloud-init YAML, SVG validity, relative links (`ci.yml`) | Every push |
| Least-privilege CI | Workflows run with `contents: read` (branch guard adds `issues: write` only) | Always |
| Branch deletion | `main` is the default branch, which GitHub refuses to delete | Always |
| Force-push prevention (local) | `.githooks/pre-push` rejects deleting or rewriting `main`; enable with `git config core.hooksPath .githooks` | Every push from a clone |
| Force-push detection (remote) | `branch-guard.yml` fails and opens an alert issue with restore commands if `main` is force-pushed | Every push to `main` |
| Dependency PR review | Dependabot PRs are merged only after every check passes on an up-to-date branch | Each PR |

Branch protection rules and rulesets require GitHub Pro for private repositories; the local hook,
default-branch status and branch guard above are the free substitute. GitHub's hosted code-scanning
and secret-scanning dashboards require GitHub Advanced Security on private repositories. The CI jobs above provide the same coverage without it. If the repository is
made public, both dashboards become free: enable them under **Settings > Code security**, and set
`upload: always` in `codeql.yml`.

## Upstream vulnerabilities

The deployed system watches upstream sources itself (Coolify releases and GitHub security
advisories, Laravel/Livewire/Traefik advisories, CISA KEV) and patches or contains automatically.
See [docs/10-security-automation.md](docs/10-security-automation.md).
