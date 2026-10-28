# Placeholders & Configuration Reference

Every value that is personal, secret, or specific to one deployment is written in this
documentation as `<UPPER_SNAKE_CASE>`. Replace them with your own values. **Never commit the real
values**, secrets live only in a local `keys/` directory (git-ignored) and on the VM in root-only files.

> Rule of thumb: if a value would let someone log in, send mail as you, change DNS, call the cloud
> API, or identify you, it is a placeholder here.

## Identity & accounts

| Placeholder | What it is | Example (fake) | Where it is used | Secret? |
|---|---|---|---|---|
| `<ALERT_EMAIL>` | Gmail address that **sends and receives** alerts | `ops.alerts@gmail.com` | `monitoring.sh`, `security.sh`, Coolify email settings, OCI budget alert | Personal |
| `<GMAIL_APP_PASSWORD>` | 16-char Google **App Password** (not your Gmail password) | `abcdabcdabcdabcd` | `/etc/msmtprc`, Coolify SMTP | **Yes** |
| `<ADMIN_USERNAME>` | Coolify admin display name | `admin` | Coolify headless install | No |
| `<ADMIN_PASSWORD>` | Coolify admin password (≥8, upper+lower+digit+symbol) | `Ex@mple-Passw0rd-xyz` | Coolify headless install, then changed in UI | **Yes** |
| `<GITHUB_USER>` | Your GitHub username (optional, for app deploys) | `octocat` | Coolify GitHub App | No |

## Oracle Cloud (OCI)

| Placeholder | What it is | Example (fake) | Secret? |
|---|---|---|---|
| `<OCI_TENANCY_OCID>` | Tenancy OCID | `ocid1.tenancy.oc1..aaaa…` | Identifier |
| `<OCI_USER_OCID>` | API user OCID | `ocid1.user.oc1..aaaa…` | Identifier |
| `<OCI_API_KEY_FINGERPRINT>` | Fingerprint of the uploaded API public key | `12:34:…:ef` | Identifier |
| `<OCI_API_PRIVATE_KEY>` | API signing private key (`.pem`) | `keys/oci_api_key.pem` | **Yes** |
| `<OCI_REGION>` | Your **home region** (Always Free compute only exists there) | `eu-frankfurt-1` | No |
| `<OCI_AUTOMATION_USER>` | Least-privilege IAM user used by the migrator | `vps-automation` | No |

`~/.oci/config` (never committed):

```ini
[DEFAULT]
user=<OCI_USER_OCID>
fingerprint=<OCI_API_KEY_FINGERPRINT>
tenancy=<OCI_TENANCY_OCID>
region=<OCI_REGION>
key_file=~/.oci/oci_api_key.pem
```

## DNS & TLS

| Placeholder | What it is | Example (fake) | Secret? |
|---|---|---|---|
| `<DUCKDNS_SUBDOMAIN>` | Your DuckDNS name (without `.duckdns.org`) | `myapps` | No |
| `<BASE_DOMAIN>` | `<DUCKDNS_SUBDOMAIN>.duckdns.org`: Coolify dashboard | `myapps.duckdns.org` | No |
| `<APP_NAME>` | Per-app subdomain → `https://<APP_NAME>.<BASE_DOMAIN>` | `blog` | No |
| `<DUCKDNS_TOKEN>` | DuckDNS account token (can repoint your domain!) | `xxxxxxxx-xxxx-…` | **Yes** |

## Network & access

| Placeholder | What it is | Default / example | Secret? |
|---|---|---|---|
| `<VM_PUBLIC_IP>` | VM public IPv4 (ephemeral, kept while the VM exists) | `203.0.113.10` | No |
| `<VM_PRIVATE_IP>` | VM address inside the VCN | `10.0.0.x` | No |
| `<SSH_PORT>` | Public SSH port | `22022` | No |
| `<SSH_PRIVATE_KEY>` | Your SSH key for the VM | `keys/oci_vm` | **Yes** |
| VCN CIDR | Virtual Cloud Network range | `10.0.0.0/16` (subnet `10.0.0.0/24`) | No |
| Docker address pool | Must not overlap the VCN | `172.20.0.0/14`, /24 per network | No |

## Other clouds

Placeholders for running the same stack on Google Cloud or AWS (`<GCP_PROJECT_ID>`,
`<GCP_BILLING_ACCOUNT_ID>`, `<AWS_REGION>`, `<AWS_ACCOUNT_ID>`) are listed in
[17 · Same stack on Google Cloud or AWS](17-other-clouds-gcp-aws.md#placeholders-added-by-this-chapter).

## Environment variables used by the scripts

The scripts in [`scripts/`](../scripts) read these (see [`scripts/config.example.env`](../scripts/config.example.env)):

| Variable | Maps to |
|---|---|
| `VPS_DOMAIN` | `<BASE_DOMAIN>` |
| `DUCKDNS_DOMAIN` | `<DUCKDNS_SUBDOMAIN>` |
| `DUCKDNS_TOKEN` | `<DUCKDNS_TOKEN>` |
| `ALERT_EMAIL` | `<ALERT_EMAIL>` |
| `GMAIL_APP_PASSWORD` | `<GMAIL_APP_PASSWORD>` |
| `SSH_PORT` | `<SSH_PORT>` |
| `OCI_CONFIG_FILE` | path to the OCI config (default `~/.oci/config`) |

## Where secrets live (and never leave)

```text
your-laptop/
└── keys/                      # git-ignored, permissions locked to your user
    ├── oci_api_key.pem        # <OCI_API_PRIVATE_KEY>
    ├── oci_vm / oci_vm.pub    # <SSH_PRIVATE_KEY>
    ├── coolify-admin.txt      # <ADMIN_PASSWORD> (delete after you change it)
    ├── duckdns-token.txt      # <DUCKDNS_TOKEN>
    └── gmail-app-password.txt # <GMAIL_APP_PASSWORD>

VM (root-only, mode 600)
├── /etc/duckdns/env           # DUCKDNS_TOKEN
├── /etc/msmtprc               # GMAIL_APP_PASSWORD
├── /data/coolify/source/.env  # Coolify APP_KEY, DB/Redis passwords (back this up offline)
└── /opt/oci-migrator/         # least-privilege API key, only until the ARM move finishes
```
