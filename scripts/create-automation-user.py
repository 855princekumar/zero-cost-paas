"""Create the least-privilege OCI identity used by the AMD->ARM migrator (ADR-0016).

  python scripts/create-automation-user.py

Creates (idempotently) group + user + policy named vps-automation, disables every credential type
except API keys, generates an RSA key pair in keys/ and uploads the public half.
Output (git-ignored): keys/vps-automation_api_key.pem, keys/vps-automation.config

The policy only allows managing instances and volumes and *using* the network; it cannot read
secrets, change IAM, or create paid resources (paid shapes are limited to 0 on a free-only tenancy).
Run it with your own admin API key; review the statements below before you do.
"""
import os

import oci
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

NAME = "vps-automation"
# Tenancies on OCI Identity Domains require an email for every user. Default: a plus-address of
# ALERT_EMAIL (still delivered to your inbox), override with AUTOMATION_USER_EMAIL.
_alert = os.environ.get("ALERT_EMAIL", "")
EMAIL = os.environ.get("AUTOMATION_USER_EMAIL") or (
    _alert.replace("@", f"+{NAME}@", 1) if "@" in _alert else None)
STATEMENTS = [f"Allow group {NAME} to manage instance-family in tenancy",
              f"Allow group {NAME} to manage volume-family in tenancy",
              f"Allow group {NAME} to use virtual-network-family in tenancy",
              f"Allow group {NAME} to inspect compartments in tenancy"]

cfg = oci.config.from_file(os.environ.get("OCI_CONFIG_FILE", oci.config.DEFAULT_LOCATION))
T = cfg["tenancy"]
idc = oci.identity.IdentityClient(cfg)


def find(items):
    return next((i for i in items if i.name == NAME and i.lifecycle_state == "ACTIVE"), None)


grp = find(idc.list_groups(T).data) or idc.create_group(oci.identity.models.CreateGroupDetails(
    compartment_id=T, name=NAME, description="AMD->ARM self-migration")).data
usr = find(idc.list_users(T).data) or idc.create_user(oci.identity.models.CreateUserDetails(
    compartment_id=T, name=NAME, email=EMAIL, description="Used only by the migrator service on the VM")).data
if not any(m.user_id == usr.id for m in idc.list_user_group_memberships(T, group_id=grp.id).data):
    idc.add_user_to_group(oci.identity.models.AddUserToGroupDetails(user_id=usr.id, group_id=grp.id))
if not find(idc.list_policies(T).data):
    idc.create_policy(oci.identity.models.CreatePolicyDetails(
        compartment_id=T, name=NAME, description="Least privilege for VM self-migration", statements=STATEMENTS))

idc.update_user_capabilities(usr.id, oci.identity.models.UpdateUserCapabilitiesDetails(
    can_use_console_password=False, can_use_auth_tokens=False, can_use_smtp_credentials=False,
    can_use_customer_secret_keys=False, can_use_o_auth2_client_credentials=False,
    can_use_db_credentials=False, can_use_api_keys=True))

os.makedirs("keys", exist_ok=True)
key_path = "keys/vps-automation_api_key.pem"
if not os.path.exists(key_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    fp = idc.upload_api_key(usr.id, oci.identity.models.CreateApiKeyDetails(key=pub)).data.fingerprint
    with open("keys/vps-automation.config", "w") as f:
        f.write(f"[DEFAULT]\nuser={usr.id}\nfingerprint={fp}\ntenancy={T}\nregion={cfg['region']}\n"
                f"key_file=/opt/oci-migrator/api_key.pem\n")
print("automation identity ready:", NAME)
