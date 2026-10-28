# 07 · Coolify: The Self-Hosted Vercel/Netlify

[← DNS & TLS](06-dns-and-tls.md) · [Index](README.md) · [Next: Security hardening →](08-security-hardening.md)

> **TL;DR**: Coolify is installed **headlessly**: the admin account is pre-created from installer
> environment variables, the Docker address pool is moved off the VCN range, and the dashboard
> domain, app wildcard domain and "registration disabled" are set through `php artisan tinker` -
> so there is never a window where a stranger could open the dashboard and claim admin. An end-to-end
> deploy through the API (with a temporary token, revoked afterwards) proved app HTTPS works.

## 1. Headless install

The official installer (`https://cdn.coollabs.io/coolify/install.sh`) was downloaded and **read
before running**. Relevant environment variables:

| Variable | Value | Why |
|---|---|---|
| `ROOT_USERNAME` | `<ADMIN_USERNAME>` | Pre-create the admin; no open sign-up page |
| `ROOT_USER_EMAIL` | `<ALERT_EMAIL>` | Login identity (validated with `email:rfc,dns`) |
| `ROOT_USER_PASSWORD` | `<ADMIN_PASSWORD>` | Must contain upper, lower, digit **and a symbol**, not breached |
| `DOCKER_ADDRESS_POOL_BASE` | `172.20.0.0/14` | Default `10.0.0.0/8` overlaps the VCN `10.0.0.0/16` |
| `DOCKER_ADDRESS_POOL_SIZE` | `24` | One /24 per Docker network |

```bash
vm$ curl -fsSL https://cdn.coollabs.io/coolify/install.sh -o /tmp/coolify-install.sh
vm$ less /tmp/coolify-install.sh            # review it
vm$ sudo env ROOT_USERNAME='<ADMIN_USERNAME>' ROOT_USER_EMAIL='<ALERT_EMAIL>' \
        ROOT_USER_PASSWORD='<ADMIN_PASSWORD>' \
        DOCKER_ADDRESS_POOL_BASE=172.20.0.0/14 DOCKER_ADDRESS_POOL_SIZE=24 \
        bash /tmp/coolify-install.sh
```

Installer steps (9): packages → OpenSSH check → Docker check → daemon config (log rotation +
address pool) → download compose files → `.env` secrets (`APP_KEY`, DB/Redis passwords) → SSH key
for `root@host.docker.internal` → pull images → start & health-check.

> **Gotcha:** if the password lacks a symbol, the `RootUserSeeder` logs
> `Invalid Root User Environment Variables` and **no admin is created**: while registration stays
> open. Port 8000 was closed at the cloud firewall, so nobody could race to register. Fix: re-run the
> seeder with a compliant password, then **delete the `ROOT_*` lines from `.env`**:
>
> ```bash
> vm$ sudo docker exec -e ROOT_USERNAME=… -e ROOT_USER_EMAIL=… -e ROOT_USER_PASSWORD=… \
>       coolify php artisan db:seed --class=RootUserSeeder --force
> vm$ sudo sed -i '/^ROOT_USER/d;/^ROOT_USERNAME/d' /data/coolify/source/.env
> ```

## 2. Configure without the GUI (tinker)

```php
// executed with: docker exec coolify php artisan tinker --execute="$(cat cfg.php)"
$s = \App\Models\InstanceSettings::get();
$s->fqdn = 'https://<BASE_DOMAIN>';          // dashboard behind Traefik + Let's Encrypt
$s->is_registration_enabled = false;          // nobody else can sign up
$s->save();
$srv = \App\Models\Server::find(0);            // "localhost" = this machine
$srv->settings->wildcard_domain = 'https://<BASE_DOMAIN>';   // apps get https://<random>.<BASE_DOMAIN>
$srv->settings->save();
$srv->setupDynamicProxyConfiguration();        // writes Traefik dynamic config for the dashboard
```

The PHP file is copied into the container with **mode 0644**: a `mktemp` file is 0600 and the
non-root PHP process can't read it (tinker then silently receives an empty script).

## 3. Coolify ↔ host

Coolify manages "This machine" by SSH-ing **as root to `host.docker.internal:22`** with a key it
generated (`/data/coolify/ssh/keys/…`). The hardening keeps this working while denying root from
anywhere else:

```text
PermitRootLogin no
Match Address 127.0.0.1,::1,172.16.0.0/12,10.0.0.0/16
    PermitRootLogin prohibit-password
```

Check:

```php
echo trim(instant_remote_process(["whoami"], \App\Models\Server::find(0)));   // → root
```

In the onboarding wizard choose **"This machine"** (not Hetzner/Vultr/DigitalOcean/Hostinger, those
create paid servers).

## 4. Deploying apps

| Source | Steps | Best for |
|---|---|---|
| Public Git repo | Projects → + Add → Production → + New Resource → Public Repository | Quick tests |
| **GitHub App** | Sources → + GitHub App → install on repos | Private repos + **auto-deploy on push** (Vercel-like) |
| Docker image | + New Resource → Docker Image | Pre-built images; best on small VMs |
| Docker Compose / Services | One-click services (Postgres, Redis, Plausible, …) | Backends & databases |

Domains: use `https://<APP_NAME>.<BASE_DOMAIN>`: **keep `https://`** so Traefik requests a
certificate. Builds run in `coolify-helper`; on the 1 GB AMD stand-in prefer static sites or
pre-built images, heavy builds belong on the A1.

## 5. End-to-end proof via API

```bash
# temporary token (expires in 1 h), API enabled only for the test
vm$ … tinker: User::find(0)->createToken('e2e-test', ['root'], now()->addHour(), 0)
$ A=https://<BASE_DOMAIN>/api/v1; H="Authorization: Bearer $TOKEN"
$ curl -s -H "$H" $A/servers                                   # server uuid
$ curl -s -H "$H" -d '{"name":"e2e-test"}' -H 'Content-Type: application/json' $A/projects
$ curl -s -H "$H" -H 'Content-Type: application/json' $A/applications/dockerimage -d '{
    "project_uuid":"…","server_uuid":"…","environment_name":"production",
    "docker_registry_image_name":"nginx","docker_registry_image_tag":"alpine",
    "ports_exposes":"80","domains":"https://hello.<BASE_DOMAIN>","instant_deploy":true}'
$ curl -s -o /dev/null -w '%{http_code}\n' https://hello.<BASE_DOMAIN>/     # 200 after ~2 min
```

Then: delete the app (`delete_volumes=true`), delete the project, delete the token, set
`is_api_enabled=false`. Result: Let's Encrypt cert for `hello.<BASE_DOMAIN>`, HTTP 200, no residue.

## 6. 2FA

Profile → **Two-factor Authentication** → Enable → scan the QR in an authenticator app → confirm
with a 6-digit code → **store the recovery codes offline**. Coolify uses Laravel Fortify with
`confirm => true`: once `two_factor_confirmed_at` is set, **every** login requires a TOTP code (no
"remember this device"). With registration disabled and a single user, 2FA is effectively mandatory.

```sql
-- verify
select email, two_factor_confirmed_at is not null as twofa from users;
```

## 7. Mount Coolify logs to the host

Coolify writes an audit log (`auth.user.login_failed` with the real client IP) inside the container.
Coolify's upgrade script honours `/data/coolify/source/docker-compose.custom.yml`, so this override
survives upgrades and migration:

```yaml
services:
  coolify:
    volumes:
      - /data/coolify/logs:/var/www/html/storage/logs
```

This feeds the fail2ban `coolify-login` jail ([08 · Hardening](08-security-hardening.md#l5--fail2ban)).

## 8. Updates

* Coolify's own **auto-update** is enabled (daily).
* [`security-watch.py`](../scripts/security-watch.py) upgrades **immediately** when a release
  mentions security fixes or a high/critical advisory affects the running version, it did exactly
  that on its first run (4.5.0 → 4.5.2) ([10 · Automation](10-security-automation.md)).
* Manual: re-run the installer with a version: `bash install.sh <version>` (keeps `.env`).

[Next: Security hardening →](08-security-hardening.md)
