"""Runs on the new ARM VM (oci-finalize.service) after migrate.py has switched DNS to it.

  1. delete the old AMD VM and its boot volume (frees 50 GB of the 200 GB free storage)
  2. grow this VM's boot volume to 200 GB and expand the filesystem online
  3. resize to the free-tier max (2 OCPU / 12 GB); OCI reboots the VM and this service runs again
  4. remove the migration API key and SSH key from this VM, disable this service

Idempotent; systemd retries on failure (e.g. no capacity for the resize yet).
"""
import os
import socket
import subprocess
import time

import provision as P

log = P.log
MIGRATOR_DIR = os.path.dirname(os.path.abspath(__file__))

me = P.instance(socket.gethostname()) or P.instance(P.ARM_NAME)

src = P.instance(P.MICRO_NAME)
if src:
    log("terminating", P.MICRO_NAME)
    P.comp.terminate_instance(src.id, preserve_boot_volume=False)
    P.wait(P.comp, P.comp.get_instance(src.id), "TERMINATED")
    time.sleep(60)  # let the boot volume deletion settle before resizing ours

bv = P.boot_volume(me)
if bv.size_in_gbs < P.TOTAL_FREE_GB:
    log("growing boot volume", bv.size_in_gbs, "->", P.TOTAL_FREE_GB)
    P.bs.update_boot_volume(bv.id, P.oci.core.models.UpdateBootVolumeDetails(size_in_gbs=P.TOTAL_FREE_GB))
    P.wait(P.bs, P.bs.get_boot_volume(bv.id), "AVAILABLE")
    time.sleep(30)

# online filesystem growth (no-op when already full size)
root = subprocess.run(["findmnt", "-no", "SOURCE", "/"], capture_output=True, text=True).stdout.strip()
disk = "/dev/" + subprocess.run(["lsblk", "-no", "pkname", root], capture_output=True, text=True).stdout.strip()
part = root[len(disk):].lstrip("p")
subprocess.run(f"echo 1 > /sys/class/block/{os.path.basename(disk)}/device/rescan", shell=True)
subprocess.run(["growpart", disk, part])
subprocess.run(["resize2fs", root])

if (me.shape_config.ocpus, me.shape_config.memory_in_gbs) != P.ARM_MAX:
    log("resizing to", P.ARM_MAX, "- the VM reboots and this service runs again")
    P.comp.update_instance(me.id, P.oci.core.models.UpdateInstanceDetails(
        shape_config=P.oci.core.models.UpdateInstanceShapeConfigDetails(ocpus=P.ARM_MAX[0], memory_in_gbs=P.ARM_MAX[1])))
    time.sleep(600)  # reboot happens during this wait
    raise SystemExit(1)  # if no reboot came, let systemd retry

log("finalized:", me.shape_config.ocpus, "OCPU /", me.shape_config.memory_in_gbs, "GB /", P.TOTAL_FREE_GB, "GB disk")
subprocess.run(["/usr/local/sbin/notify", "[MIGRATION] Done: ARM VM at free-tier max"],
               input=f"{me.shape_config.ocpus} OCPU / {me.shape_config.memory_in_gbs} GB RAM / {P.TOTAL_FREE_GB} GB disk.\n"
                     f"The old AMD VM is deleted. Disk:\n" + subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout,
               text=True)
subprocess.run("sed -i '/oci-migrator/d' /home/ubuntu/.ssh/authorized_keys", shell=True)
subprocess.run(f"shred -u {MIGRATOR_DIR}/api_key.pem; rm -f {MIGRATOR_DIR}/config", shell=True)
subprocess.run("systemctl disable oci-finalize.service", shell=True)
