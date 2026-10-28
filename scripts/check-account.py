"""Read-only pre-flight: verify OCI API auth, account type (free-only vs PAYG), Always Free limits and usage.

  python scripts/check-account.py

Nothing is created. Run this before provisioning to learn what YOUR tenancy allows
(the A1 limit varies per account: documented 4 OCPU / 24 GB, but some free-only tenancies get 2 / 12).
"""
import os

import oci

cfg = oci.config.from_file(os.environ.get("OCI_CONFIG_FILE", oci.config.DEFAULT_LOCATION))
oci.config.validate_config(cfg)
T = cfg["tenancy"]
idc = oci.identity.IdentityClient(cfg)

ten = idc.get_tenancy(T).data
print(f"auth OK  tenancy home region key: {ten.home_region_key}  configured region: {cfg['region']}")
ads = idc.list_availability_domains(T).data
print("availability domains:", len(ads))

try:
    subs = oci.tenant_manager_control_plane.SubscriptionClient(cfg).list_subscriptions(compartment_id=T).data
    for s in subs.items:
        print(f"subscription payment_model={s.payment_model}  trial_end={s.end_date:%Y-%m-%d}")
        if s.payment_model == "FREE_TRIAL":
            print("  -> free-only account: cannot be billed; paid shapes are limited to 0 once the trial ends")
except Exception as e:  # not available in every tenancy
    print("subscription lookup unavailable:", getattr(e, "message", e))

lim = oci.limits.LimitsClient(cfg)
checks = [("compute", "standard-a1-core-count", "AD"), ("compute", "standard-a1-memory-count", "AD"),
          ("compute", "standard-e2-micro-core-count", "AD"), ("compute", "standard-e4-core-count", "AD"),
          ("block-storage", "total-free-storage-gb-regional", "REGION"), ("vcn", "vcn-count", "REGION")]
print(f"\n{'service':14} {'limit':34} {'used':>6} {'available':>10}")
for svc, name, scope in checks:
    kw = {"availability_domain": ads[0].name} if scope == "AD" else {}
    try:
        a = lim.get_resource_availability(svc, name, T, **kw).data
        print(f"{svc:14} {name:34} {a.used!s:>6} {a.available!s:>10}")
    except Exception as e:
        print(f"{svc:14} {name:34} {'n/a':>6}  ({getattr(e, 'message', e)})")

comp = oci.core.ComputeClient(cfg)
live = [i for i in comp.list_instances(T).data if i.lifecycle_state != "TERMINATED"]
print("\ninstances:", [(i.display_name, i.shape, i.lifecycle_state) for i in live] or "none")
