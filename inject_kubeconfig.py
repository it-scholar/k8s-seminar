#!/usr/bin/env python3
"""
Merges terraform/kubeconfig into ~/.kube/config for the student user
on every group primary VM listed in seminar-credentials.md.

Usage:
    python inject_kubeconfig.py
"""

from __future__ import annotations

import base64
import os
import socket
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from fabric import Connection
import paramiko

load_dotenv()

SSH_PRIVATE_KEY = os.path.expanduser(
    os.getenv("SSH_PRIVATE_KEY_PATH", "~/.ssh/seminar_id_ed25519")
)

KUBECONFIG_PATH = Path(__file__).parent / "terraform" / "kubeconfig"

# All student primary VMs (student@<ip>)
PRIMARIES = [
    ("Andrea",      "88.198.157.157"),
    ("Thomas",      "167.233.124.217"),
    ("Christopher", "167.233.104.83"),
    ("Niklas",      "167.233.57.254"),
    ("Sven",        "167.233.124.32"),
    ("André",       "167.233.127.72"),
    ("Yannick",     "167.233.111.108"),
    ("Tim",         "167.233.112.10"),
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def connection(ip: str, user: str = "student") -> Connection:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)
    sock.connect((ip, 22))
    conn = Connection(
        host=ip,
        user=user,
        connect_kwargs={
            "key_filename": SSH_PRIVATE_KEY,
            "timeout": 30,
            "look_for_keys": False,
            "allow_agent": False,
            "sock": sock,
        },
    )
    conn.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return conn


def merge_kubeconfig(name: str, ip: str, new_kubeconfig_b64: str) -> None:
    log(f"[{name}] Connecting to {ip} …")
    try:
        c = connection(ip)
    except Exception as e:
        log(f"[{name}] ERROR: Could not connect: {e}")
        return

    try:
        # 1. Write the new kubeconfig to a temp file
        c.run(
            f"echo '{new_kubeconfig_b64}' | base64 -d > /tmp/seminar-admin.kubeconfig",
            hide=True,
        )
        log(f"[{name}] Uploaded new kubeconfig fragment")

        # 2. Ensure ~/.kube exists
        c.run("mkdir -p ~/.kube", hide=True)

        # 3. Check whether an existing kubeconfig is present
        result = c.run("test -f ~/.kube/config && echo EXISTS || echo MISSING", hide=True)
        exists = result.stdout.strip() == "EXISTS"

        if exists:
            log(f"[{name}] Existing ~/.kube/config found — merging")
            # Merge: use KUBECONFIG env var trick, flatten, write atomically
            c.run(
                "KUBECONFIG=~/.kube/config:/tmp/seminar-admin.kubeconfig "
                "kubectl config view --flatten > /tmp/merged.kubeconfig "
                "&& mv /tmp/merged.kubeconfig ~/.kube/config",
                hide=True,
            )
        else:
            log(f"[{name}] No existing ~/.kube/config — installing directly")
            c.run("cp /tmp/seminar-admin.kubeconfig ~/.kube/config", hide=True)

        # 4. Lock down permissions
        c.run("chmod 600 ~/.kube/config", hide=True)

        # 5. Verify — list all contexts
        result = c.run("kubectl config get-contexts", hide=True)
        log(f"[{name}] Contexts after merge:\n{result.stdout.rstrip()}")

        # 6. Cleanup temp files
        c.run("rm -f /tmp/seminar-admin.kubeconfig /tmp/merged.kubeconfig", hide=True)

        log(f"[{name}] Done ✓")

    except Exception as e:
        log(f"[{name}] ERROR during merge: {e}")
    finally:
        c.close()


def main() -> None:
    kubeconfig_raw = KUBECONFIG_PATH.read_bytes()
    kubeconfig_b64 = base64.b64encode(kubeconfig_raw).decode()

    log(f"Loaded kubeconfig from {KUBECONFIG_PATH} ({len(kubeconfig_raw)} bytes)")
    log(f"Targeting {len(PRIMARIES)} primary VM(s)")

    for name, ip in PRIMARIES:
        merge_kubeconfig(name, ip, kubeconfig_b64)


if __name__ == "__main__":
    main()
