"""Write cloud-init.yaml with harden.sh embedded, for any cloud (no OCI SDK needed).

  python scripts/render-cloud-init.py > keys/cloud-init.rendered.yaml

Pass the output as user data: GCP `--metadata-from-file user-data=...`, AWS `--user-data file://...`.
The VM then installs Docker and hardens itself on first boot, so it is reachable on SSH_PORT
(default 22022) and never needs port 22 open to the internet. Contains no secrets.
"""
import base64
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "cloud-init.yaml"), encoding="utf-8") as f:
    text = f.read()
with open(os.path.join(HERE, "harden.sh"), "rb") as f:
    harden = base64.b64encode(f.read()).decode()

entry = ("  - path: /usr/local/sbin/harden\n    permissions: '0755'\n    encoding: b64\n"
         f"    content: {harden}\n")
sys.stdout.write(text.replace("write_files:\n", "write_files:\n" + entry, 1))
