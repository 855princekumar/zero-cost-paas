"""Provision Always Free VMs (Ubuntu 24.04 + Docker) on OCI.

  python provision.py network   -> create/update VCN, subnet, gateway and security list (OPEN_PORTS)
  python provision.py micro     -> AMD E2.1.Micro (50 GB boot)

The ARM (A1) replacement is handled by migrate.py, which also moves Coolify.
Idempotent: re-running reuses resources found by display name.
Config: ~/.oci/config, or the file in $OCI_CONFIG_FILE.
"""
import base64
import datetime
import os
import sys
import time

import oci

HERE = os.path.dirname(os.path.abspath(__file__))
CLOUD_INIT = os.path.join(HERE, "cloud-init.yaml")
SSH_PUB = os.environ.get("VM_SSH_PUB", os.path.expanduser("~/.ssh/oci_vm.pub"))

ARM_NAME, MICRO_NAME = "free-arm", "free-amd"
ARM_SHAPE, MICRO_SHAPE = "VM.Standard.A1.Flex", "VM.Standard.E2.1.Micro"
ARM_MAX = (2, 12)                       # account limit: 2 OCPU / 12 GB
ARM_TRY_SIZES = [(2, 12), (1, 6)]       # smaller request gets capacity more often; grown afterwards
TOTAL_FREE_GB, MICRO_BOOT_GB = 200, 50
SSH_PORT = int(os.environ.get("SSH_PORT", "22022"))
OPEN_PORTS = [SSH_PORT, 80, 443]   # port 22 stays VCN-internal (Coolify -> host)
RETRY_SLEEP = 30

cfg = oci.config.from_file(os.environ.get("OCI_CONFIG_FILE", oci.config.DEFAULT_LOCATION))
T = cfg["tenancy"]
idc = oci.identity.IdentityClient(cfg)
vn = oci.core.VirtualNetworkClient(cfg)
comp = oci.core.ComputeClient(cfg)
bs = oci.core.BlockstorageClient(cfg)
AD = idc.list_availability_domains(T).data[0].name
FAULT_DOMAINS = [f.name for f in idc.list_fault_domains(T, AD).data]
TAGS = {"purpose": "always-free"}


def log(*a):
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def first(items, name):
    return next((i for i in items if i.display_name == name and i.lifecycle_state not in ("TERMINATED", "TERMINATING")), None)


def instance(name):
    return first(comp.list_instances(T).data, name)


def wait(client, get, state, secs=900):
    return oci.wait_until(client, get, "lifecycle_state", state, max_wait_seconds=secs, succeed_on_not_found=state == "TERMINATED").data


def network():
    vcn = first(vn.list_vcns(T).data, "free-vm-vcn")
    if not vcn:
        vcn = vn.create_vcn(oci.core.models.CreateVcnDetails(
            compartment_id=T, display_name="free-vm-vcn", cidr_blocks=["10.0.0.0/16"],
            dns_label="freevcn", freeform_tags=TAGS)).data
        vcn = wait(vn, vn.get_vcn(vcn.id), "AVAILABLE")

    igw = first(vn.list_internet_gateways(T, vcn_id=vcn.id).data, "free-vm-igw")
    if not igw:
        igw = vn.create_internet_gateway(oci.core.models.CreateInternetGatewayDetails(
            compartment_id=T, vcn_id=vcn.id, display_name="free-vm-igw", is_enabled=True, freeform_tags=TAGS)).data
        igw = wait(vn, vn.get_internet_gateway(igw.id), "AVAILABLE")

    vn.update_route_table(vcn.default_route_table_id, oci.core.models.UpdateRouteTableDetails(route_rules=[
        oci.core.models.RouteRule(destination="0.0.0.0/0", destination_type="CIDR_BLOCK", network_entity_id=igw.id)]))

    ingress = [oci.core.models.IngressSecurityRule(
        protocol="6", source="0.0.0.0/0",
        tcp_options=oci.core.models.TcpOptions(destination_port_range=oci.core.models.PortRange(min=p, max=p)))
        for p in OPEN_PORTS]
    ingress += [
        oci.core.models.IngressSecurityRule(protocol="1", source="0.0.0.0/0",
                                            icmp_options=oci.core.models.IcmpOptions(type=3, code=4)),
        oci.core.models.IngressSecurityRule(protocol="all", source="10.0.0.0/16"),
    ]
    vn.update_security_list(vcn.default_security_list_id, oci.core.models.UpdateSecurityListDetails(
        ingress_security_rules=ingress,
        egress_security_rules=[oci.core.models.EgressSecurityRule(protocol="all", destination="0.0.0.0/0")]))

    sub = first(vn.list_subnets(T, vcn_id=vcn.id).data, "free-vm-subnet")
    if not sub:
        sub = vn.create_subnet(oci.core.models.CreateSubnetDetails(
            compartment_id=T, vcn_id=vcn.id, display_name="free-vm-subnet", cidr_block="10.0.0.0/24",
            dns_label="public", freeform_tags=TAGS)).data
        sub = wait(vn, vn.get_subnet(sub.id), "AVAILABLE")
    return sub


def subnet():
    vcn = first(vn.list_vcns(T).data, "free-vm-vcn")
    return first(vn.list_subnets(T, vcn_id=vcn.id).data, "free-vm-subnet")


def ubuntu_image(shape):
    imgs = comp.list_images(T, operating_system="Canonical Ubuntu", operating_system_version="24.04",
                            shape=shape, sort_by="TIMECREATED", sort_order="DESC").data
    img = next((i for i in imgs if "Minimal" not in i.display_name), imgs[0])
    log("IMAGE", img.display_name)
    return img


def cloud_init():
    """cloud-init.yaml with harden.sh embedded, so the VM is hardened (and reachable on SSH_PORT) from first boot.
    No secrets go in here: user_data is readable from inside the VM via the metadata service."""
    text = open(CLOUD_INIT, encoding="utf-8").read()
    harden = base64.b64encode(open(os.path.join(HERE, "harden.sh"), "rb").read()).decode()
    entry = ("  - path: /usr/local/sbin/harden\n    permissions: '0755'\n    encoding: b64\n"
             f"    content: {harden}\n")
    return text.replace("write_files:\n", "write_files:\n" + entry, 1).encode()


def launch_details(name, shape, sub, img, boot_gb, size=None, fd=None, user_data=None, ssh_keys=None):
    if user_data is None:
        user_data = cloud_init()
    if ssh_keys is None:
        ssh_keys = open(SSH_PUB, encoding="utf-8").read().strip()
    return oci.core.models.LaunchInstanceDetails(
        compartment_id=T, availability_domain=AD, fault_domain=fd, display_name=name, shape=shape,
        shape_config=size and oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=size[0], memory_in_gbs=size[1]),
        source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=img.id, boot_volume_size_in_gbs=boot_gb),
        create_vnic_details=oci.core.models.CreateVnicDetails(subnet_id=sub.id, assign_public_ip=True, hostname_label=name),
        metadata={"ssh_authorized_keys": ssh_keys, "user_data": base64.b64encode(user_data).decode()},
        freeform_tags=TAGS)


def vnic(inst):
    att = comp.list_vnic_attachments(T, instance_id=inst.id).data[0]
    return vn.get_vnic(att.vnic_id).data


def public_ip(inst):
    return vnic(inst).public_ip


def boot_volume(inst):
    att = comp.list_boot_volume_attachments(AD, T, instance_id=inst.id).data[0]
    return bs.get_boot_volume(att.boot_volume_id).data


def is_capacity_error(e):
    return e.status == 429 or (e.status == 500 and "capacity" in (e.message or "").lower())


def launch_with_retry(details, label):
    while True:
        try:
            return comp.launch_instance(details).data
        except oci.exceptions.ServiceError as e:
            if not is_capacity_error(e):
                raise
            log(f"{label}:", e.message, "- retrying")
            time.sleep(RETRY_SLEEP)


def micro(name=MICRO_NAME, **launch_kw):
    sub = network()
    inst = instance(name)
    if not inst:
        img = ubuntu_image(MICRO_SHAPE)
        inst = launch_with_retry(launch_details(name, MICRO_SHAPE, sub, img, MICRO_BOOT_GB, **launch_kw), name)
    inst = wait(comp, comp.get_instance(inst.id), "RUNNING")
    log("MICRO_RUNNING", name, inst.id, "PUBLIC_IP", public_ip(inst))
    return inst


if __name__ == "__main__":
    {"network": network, "micro": micro}[sys.argv[1]]()
