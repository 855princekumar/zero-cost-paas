# 04 · OCI API Setup (No Console)

[← Placeholders](03-placeholders.md) · [Index](README.md) · [Next: Provisioning →](05-provisioning.md)

> **TL;DR**: The only thing taken from the Oracle web console is an **API signing key** (generated
> once under *My profile → API keys*). From then on everything, audit, network, VMs, budgets, IAM -
> is done with the **OCI Python SDK** from a virtualenv. Before building anything, query the account
> type and the real service limits: they decide the architecture.

## 1. The API signing key

When you add an API key in OCI you get a private key (`.pem`), its public key, a fingerprint and a
config snippet like:

```ini
[DEFAULT]
user=<OCI_USER_OCID>
fingerprint=<OCI_API_KEY_FINGERPRINT>
tenancy=<OCI_TENANCY_OCID>
region=<OCI_REGION>
key_file=<path to your private keyfile> # TODO
```

Move the key out of `Downloads`, lock it down, and complete `key_file`:

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force $env:USERPROFILE\.oci | Out-Null
Copy-Item .\<downloaded-private>.pem $env:USERPROFILE\.oci\oci_api_key.pem
icacls $env:USERPROFILE\.oci\oci_api_key.pem /inheritance:r /grant:r "$($env:USERNAME):R"
```

```bash
# Linux / macOS
mkdir -p ~/.oci && cp <downloaded-private>.pem ~/.oci/oci_api_key.pem && chmod 600 ~/.oci/oci_api_key.pem
```

`~/.oci/config` then points `key_file` at that file. **Never commit it** (see `.gitignore`).

## 2. SDK instead of `oci-cli`

`pip install oci-cli` failed on Python 3.14: it pins an old **PyYAML** without wheels for that
interpreter, and building it needs MSVC. The CLI is only a wrapper, the **SDK** (`pip install oci`)
has the same API surface and installs cleanly.

```bash
python -m venv ~/.oci-cli-venv
~/.oci-cli-venv/bin/pip install oci        # Windows: .oci-cli-venv\Scripts\pip.exe
```

## 3. Validate authentication

```python
import oci
cfg = oci.config.from_file()            # or OCI_CONFIG_FILE
oci.config.validate_config(cfg)
idc = oci.identity.IdentityClient(cfg)
ten = idc.get_tenancy(cfg["tenancy"]).data
print(ten.name, ten.home_region_key)
print([ad.name for ad in idc.list_availability_domains(cfg["tenancy"]).data])
```

A successful call proves the key, fingerprint and OCIDs line up. Many regions have a **single
availability domain** with three **fault domains**: the capacity-retry loop later rotates through them.

## 4. Is the account free-only or Pay-As-You-Go?

This is the single most important fact for a "$0 forever" design:

```python
sc = oci.tenant_manager_control_plane.SubscriptionClient(cfg)
for s in sc.list_subscriptions(compartment_id=cfg["tenancy"]).data.items:
    print(s.payment_model, s.start_date, s.end_date)
```

| `payment_model` | Meaning | Can it be charged? |
|---|---|---|
| `FREE_TRIAL` (end date in the past) | Trial over, **free-only** | **No**: non-free resources simply can't be created |
| `PAY_AS_YOU_GO` | Upgraded account | **Yes**: budgets only *alert*, they do not block |

## 5. Real limits, not the brochure

```python
lim = oci.limits.LimitsClient(cfg)
ad = idc.list_availability_domains(cfg["tenancy"]).data[0].name
for name in ["standard-a1-core-count", "standard-a1-memory-count",
             "standard-e2-micro-core-count", "standard-e4-core-count"]:
    a = lim.get_resource_availability("compute", name, cfg["tenancy"], availability_domain=ad).data
    print(name, "used", a.used, "available", a.available)
a = lim.get_resource_availability("block-storage", "total-free-storage-gb-regional", cfg["tenancy"]).data
print("free block storage used", a.used, "available", a.available)
```

What this tenancy returned:

| Limit | Value | Implication |
|---|---|---|
| `standard-a1-core-count` | **2** | Not the commonly quoted 4 |
| `standard-a1-memory-count` | **12** GB | Not 24 |
| `standard-e2-micro-core-count` | 2 | Two AMD Micro VMs (1/8 OCPU, 1 GB) |
| `standard-e4-core-count`, `e5`, … | **0** | Paid shapes are impossible → no accidental spend |
| `total-free-storage-gb-regional` | 200 GB | Shared by every boot + block volume |
| `vcn-count` | 2 | One VCN is enough |

> If your tenancy shows 4/24 for A1, set `ARM_MAX = (4, 24)` in [`provision.py`](../scripts/provision.py).

## 6. Existing resources audit

```python
comp = oci.core.ComputeClient(cfg); vn = oci.core.VirtualNetworkClient(cfg)
print([(i.display_name, i.lifecycle_state) for i in comp.list_instances(cfg["tenancy"]).data])
print([(v.display_name, v.cidr_block) for v in vn.list_vcns(cfg["tenancy"]).data])
```

An empty tenancy means the scripts own every resource they create (all are tagged
`freeform_tags={"purpose": "always-free"}` and found again by display name, idempotency).

## 7. Budget alert (belt and braces)

Budgets are free. Even on a free-only account, a **$1 budget** with an alert on *actual* and
*forecast* spend at 1% gives an early warning if anything ever changes:

```python
b = oci.budget.BudgetClient(cfg)
bud = b.create_budget(oci.budget.models.CreateBudgetDetails(
    compartment_id=cfg["tenancy"], target_type="COMPARTMENT", targets=[cfg["tenancy"]],
    amount=1, reset_period="MONTHLY", display_name="free-tier-guard")).data
for typ in ("ACTUAL", "FORECAST"):
    b.create_alert_rule(bud.id, oci.budget.models.CreateAlertRuleDetails(
        type=typ, threshold=1, threshold_type="PERCENTAGE", recipients="<ALERT_EMAIL>"))
```

## 8. Least-privilege identity for automation

The workstation key is a tenancy admin key. Anything that runs **on the VM** (the self-migrator)
uses a separate IAM user with only what it needs ([ADR 0016](adr/0016-least-privilege-automation-identity.md)):

```text
Allow group <OCI_AUTOMATION_USER> to manage instance-family in tenancy
Allow group <OCI_AUTOMATION_USER> to manage volume-family in tenancy
Allow group <OCI_AUTOMATION_USER> to use virtual-network-family in tenancy
Allow group <OCI_AUTOMATION_USER> to inspect compartments in tenancy
```

The user has API keys only (no console password, no auth tokens). Its key is deleted from the VM
when the migration finishes.

[Next: Provisioning →](05-provisioning.md)
