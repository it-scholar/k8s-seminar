#!/usr/bin/env python3
"""
k8s-seminar Hetzner + Cloudflare Provisioner
=============================================

Provisions 2 groups × (1 primary + 1 worker) VMs on Hetzner Cloud,
configures a 'student' user with passwordless sudo on all VMs,
installs code-server + Caddy (Let's Encrypt TLS) on primary VMs,
creates Cloudflare DNS A records, and wires up intra-group SSH by hostname.

Usage:
    pip install -r requirements.txt
    cp .env.example .env   # fill in credentials
    python provision.py
"""

from __future__ import annotations

import io
import os
import secrets
import string
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cloudflare as cf_module
from dotenv import load_dotenv
from fabric import Connection
from hcloud import Client as HCloudClient
from hcloud.firewalls.domain import FirewallRule
from hcloud.images.domain import Image
from hcloud.locations.domain import Location
from hcloud.server_types.domain import ServerType

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — adjust to scale up or change server specs
# ─────────────────────────────────────────────────────────────────────────────

GROUPS: list[dict] = [
    {"name": "group1", "primary": "group1-primary", "workers": ["group1-worker1"]},
    {"name": "group2", "primary": "group2-primary", "workers": ["group2-worker1"]},
]

SERVER_TYPE        = "cpx32"          # 4 vCPU / 8 GB RAM (AMD EPYC)
IMAGE_NAME         = "ubuntu-24.04"
LOCATION_NAME      = "fsn1"           # Falkenstein, Germany
DOMAIN_SUFFIX      = "k8s.it-scholar.com"
HETZNER_KEY_NAME   = "k8s-seminar-provisioner"
PRIMARY_FW_NAME    = "k8s-seminar-primary-fw"
WORKER_FW_NAME     = "k8s-seminar-worker-fw"

# ─────────────────────────────────────────────────────────────────────────────
# Systemd unit for code-server (runs as the student user)
# ─────────────────────────────────────────────────────────────────────────────

CODESERVER_SERVICE = """\
[Unit]
Description=code-server IDE (student)
After=network.target

[Service]
Type=simple
User=student
Group=student
WorkingDirectory=/home/student
ExecStart=/usr/bin/code-server --config /home/student/.config/code-server/config.yaml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

# ─────────────────────────────────────────────────────────────────────────────
# Credentials — loaded from .env
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        sys.exit(f"ERROR: Missing required environment variable '{key}'. Check .env.")
    return val


HCLOUD_TOKEN     = _require("HCLOUD_TOKEN")
CF_API_TOKEN     = _require("CF_API_TOKEN")
CF_ZONE_ID       = _require("CF_ZONE_ID")
SSH_PRIVATE_KEY  = os.path.expanduser(_require("SSH_PRIVATE_KEY_PATH"))
SSH_PUBLIC_KEY   = os.path.expanduser(_require("SSH_PUBLIC_KEY_PATH"))

hc = HCloudClient(token=HCLOUD_TOKEN)
cf = cf_module.Cloudflare(api_token=CF_API_TOKEN)

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def gen_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def connection(ip: str, user: str = "root") -> Connection:
    """Return a Fabric Connection for the given IP."""
    return Connection(
        host=ip,
        user=user,
        connect_kwargs={
            "key_filename": SSH_PRIVATE_KEY,
            "timeout": 30,
            "look_for_keys": False,
            "allow_agent": False,
        },
    )


def wait_for_ssh(ip: str, timeout: int = 300) -> None:
    """Block until SSH port on *ip* accepts connections."""
    log(f"  Waiting for SSH on {ip} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with connection(ip) as c:
                c.run("true", hide=True)
            log(f"  SSH ready: {ip}")
            return
        except Exception:
            time.sleep(6)
    raise TimeoutError(f"SSH never became available on {ip} after {timeout}s")


def put_text(c: Connection, content: str, remote_path: str) -> None:
    """Upload a string as a remote file (avoids shell-quoting pitfalls).
    Uses BytesIO to prevent Paramiko's SFTP size-mismatch bug with StringIO."""
    c.put(io.BytesIO(content.encode("utf-8")), remote_path)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Hetzner Infrastructure
# ─────────────────────────────────────────────────────────────────────────────


def ensure_hetzner_ssh_key() -> object:
    """Upload provisioner public key to Hetzner; reuse if already present."""
    existing = hc.ssh_keys.get_by_name(HETZNER_KEY_NAME)
    if existing:
        log(f"Hetzner SSH key '{HETZNER_KEY_NAME}' already exists, reusing.")
        return existing
    pub = Path(SSH_PUBLIC_KEY).read_text().strip()
    log(f"Uploading SSH key '{HETZNER_KEY_NAME}' to Hetzner ...")
    return hc.ssh_keys.create(name=HETZNER_KEY_NAME, public_key=pub)


def ensure_firewall(name: str, extra_ports: list[str]) -> object:
    """Create a Hetzner firewall; return existing one if already present."""
    existing = hc.firewalls.get_by_name(name)
    if existing:
        log(f"Firewall '{name}' already exists, reusing.")
        return existing
    log(f"Creating firewall '{name}' ...")
    rules = [
        FirewallRule(
            direction="in",
            protocol="tcp",
            port=p,
            source_ips=["0.0.0.0/0", "::/0"],
            description=f"Allow inbound TCP {p}",
        )
        for p in (["22"] + extra_ports)
    ]
    result = hc.firewalls.create(name=name, rules=rules)
    return result.firewall


def create_vm(
    name: str,
    role: str,
    ssh_key: object,
    primary_fw: object,
    worker_fw: object,
) -> tuple[str, str]:
    """Create a single VM and return (name, public_ipv4)."""
    existing = hc.servers.get_by_name(name)
    if existing:
        ip = existing.public_net.ipv4.ip
        log(f"  VM '{name}' already exists (IP: {ip}), skipping creation.")
        return name, ip

    fw = primary_fw if role == "primary" else worker_fw
    log(f"  Creating VM '{name}' ({SERVER_TYPE}, {IMAGE_NAME}, {LOCATION_NAME}) ...")
    response = hc.servers.create(
        name=name,
        server_type=ServerType(name=SERVER_TYPE),
        image=Image(name=IMAGE_NAME),
        location=Location(name=LOCATION_NAME),
        ssh_keys=[ssh_key],
        firewalls=[fw],
    )
    response.action.wait_until_finished()
    server = hc.servers.get_by_name(name)
    ip = server.public_net.ipv4.ip
    log(f"  VM '{name}' created (IP: {ip})")
    return name, ip


def provision_hetzner() -> dict[str, str]:
    """Phase 1: create SSH key, firewalls, and all VMs. Returns {name: ip}."""
    log("=== Phase 1: Hetzner Infrastructure ===")

    ssh_key    = ensure_hetzner_ssh_key()
    primary_fw = ensure_firewall(PRIMARY_FW_NAME, ["80", "443", "6443"])
    worker_fw  = ensure_firewall(WORKER_FW_NAME, [])

    tasks: list[tuple[str, str]] = []
    for group in GROUPS:
        tasks.append((group["primary"], "primary"))
        for w in group["workers"]:
            tasks.append((w, "worker"))

    vm_ips: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {
            pool.submit(create_vm, name, role, ssh_key, primary_fw, worker_fw): name
            for name, role in tasks
        }
        for future in as_completed(futures):
            name, ip = future.result()
            vm_ips[name] = ip

    log("Phase 1 complete.\n")
    return vm_ips


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Cloudflare DNS
# ─────────────────────────────────────────────────────────────────────────────


def setup_dns(vm_ips: dict[str, str]) -> None:
    """Phase 2: create/update A records for each group's primary VM."""
    log("=== Phase 2: Cloudflare DNS ===")

    for group in GROUPS:
        dns_name = f"{group['name']}.{DOMAIN_SUFFIX}"
        ip       = vm_ips[group["primary"]]

        # Filter client-side for robustness across SDK versions
        all_records = list(cf.dns.records.list(zone_id=CF_ZONE_ID))
        existing = [
            r for r in all_records
            if getattr(r, "name", "") == dns_name and getattr(r, "type", "") == "A"
        ]

        if existing:
            rec = existing[0]
            if getattr(rec, "content", "") == ip:
                log(f"  DNS {dns_name} -> {ip} already correct, skipping.")
                continue
            log(f"  Updating DNS {dns_name} -> {ip} ...")
            cf.dns.records.update(
                dns_record_id=rec.id,
                zone_id=CF_ZONE_ID,
                name=dns_name,
                type="A",
                content=ip,
                proxied=False,
                ttl=300,
            )
        else:
            log(f"  Creating DNS {dns_name} -> {ip} ...")
            cf.dns.records.create(
                zone_id=CF_ZONE_ID,
                name=dns_name,
                type="A",
                content=ip,
                proxied=False,
                ttl=300,
            )

    log("Phase 2 complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Base OS Configuration (all VMs)
# ─────────────────────────────────────────────────────────────────────────────


def _group_for_vm(name: str) -> dict:
    for group in GROUPS:
        if name == group["primary"] or name in group["workers"]:
            return group
    raise ValueError(f"VM '{name}' not found in any group")


def configure_base_vm(name: str, ip: str, vm_ips: dict[str, str]) -> None:
    """
    On a single VM:
      - upgrade packages, install essentials
      - create 'student' user with passwordless sudo
      - place provisioner's public key in student's authorized_keys
      - append group-local /etc/hosts entries
    """
    pub_key = Path(SSH_PUBLIC_KEY).read_text().strip()
    group   = _group_for_vm(name)

    # /etc/hosts block for every VM in this group
    group_vms   = [group["primary"]] + group["workers"]
    hosts_lines = "\n".join(f"{vm_ips[n]}  {n}" for n in group_vms)
    hosts_block = f"\n# k8s-seminar — {group['name']}\n{hosts_lines}\n"

    sudoers_line = "student ALL=(ALL) NOPASSWD:ALL\n"

    log(f"  [{name}] Configuring base OS ...")
    with connection(ip) as c:
        # Update & install packages
        c.run(
            "apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq",
            hide=True,
        )
        c.run(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            "curl git vim net-tools",
            hide=True,
        )

        # Create student user (idempotent)
        c.run("id student &>/dev/null || useradd -m -s /bin/bash student", hide=True)

        # Passwordless sudo
        put_text(c, sudoers_line, "/etc/sudoers.d/student")
        c.run("chmod 440 /etc/sudoers.d/student", hide=True)

        # SSH authorised key for student
        c.run(
            "install -d -m 700 -o student -g student /home/student/.ssh",
            hide=True,
        )
        put_text(c, pub_key + "\n", "/home/student/.ssh/authorized_keys")
        c.run(
            "chmod 600 /home/student/.ssh/authorized_keys && "
            "chown student:student /home/student/.ssh/authorized_keys",
            hide=True,
        )

        # /etc/hosts entries (idempotent via marker comment)
        put_text(c, hosts_block, "/tmp/k8s_hosts_addition")
        c.run(
            "grep -qF 'k8s-seminar' /etc/hosts || "
            "cat /tmp/k8s_hosts_addition >> /etc/hosts; "
            "rm -f /tmp/k8s_hosts_addition",
            hide=True,
        )

    log(f"  [{name}] Base configuration done.")


def configure_all_base(vm_ips: dict[str, str]) -> None:
    """Phase 3: wait for SSH on all VMs, then configure them in parallel."""
    log("=== Phase 3: Base OS Configuration ===")

    with ThreadPoolExecutor(max_workers=len(vm_ips)) as pool:
        ssh_futures = {pool.submit(wait_for_ssh, ip): ip for ip in vm_ips.values()}
        for f in as_completed(ssh_futures):
            f.result()

    with ThreadPoolExecutor(max_workers=len(vm_ips)) as pool:
        cfg_futures = {
            pool.submit(configure_base_vm, name, ip, vm_ips): name
            for name, ip in vm_ips.items()
        }
        for f in as_completed(cfg_futures):
            f.result()

    log("Phase 3 complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — code-server + Caddy on Primary VMs
# ─────────────────────────────────────────────────────────────────────────────

_CADDY_INSTALL = """\
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy
"""


def configure_primary_vm(ip: str, group_name: str, password: str) -> None:
    """Install code-server and Caddy on a primary VM."""
    fqdn = f"{group_name}.{DOMAIN_SUFFIX}"

    cs_config = (
        "bind-addr: 127.0.0.1:8080\n"
        "auth: password\n"
        f"password: {password}\n"
        "cert: false\n"
    )

    caddyfile = (
        f"{fqdn} {{\n"
        f"    reverse_proxy localhost:8080\n"
        f"    log {{\n"
        f"        output file /var/log/caddy/access.log\n"
        f"    }}\n"
        f"}}\n"
    )

    with connection(ip) as c:
        log(f"  [{group_name}] Installing code-server ...")
        c.run("curl -fsSL https://code-server.dev/install.sh | sh", hide=True)

        log(f"  [{group_name}] Configuring code-server ...")
        c.run(
            "install -d -m 755 -o student -g student "
            "/home/student/.config/code-server",
            hide=True,
        )
        put_text(c, cs_config, "/home/student/.config/code-server/config.yaml")
        c.run(
            "chown student:student /home/student/.config/code-server/config.yaml",
            hide=True,
        )

        # Systemd service (runs code-server as the student user system-wide)
        put_text(c, CODESERVER_SERVICE, "/etc/systemd/system/code-server.service")
        c.run(
            "systemctl daemon-reload && systemctl enable --now code-server",
            hide=True,
        )

        log(f"  [{group_name}] Installing Caddy ...")
        c.run(_CADDY_INSTALL, hide=True)

        log(f"  [{group_name}] Configuring Caddy for {fqdn} ...")
        c.run("mkdir -p /var/log/caddy", hide=True)
        put_text(c, caddyfile, "/etc/caddy/Caddyfile")
        c.run("systemctl enable caddy && systemctl restart caddy", hide=True)

    log(f"  [{group_name}] Primary VM setup complete.")


def configure_all_primaries(vm_ips: dict[str, str]) -> dict[str, str]:
    """Phase 4: configure primary VMs in parallel. Returns {group_name: password}."""
    log("=== Phase 4: code-server + Caddy on Primary VMs ===")

    passwords: dict[str, str] = {g["name"]: gen_password() for g in GROUPS}

    with ThreadPoolExecutor(max_workers=len(GROUPS)) as pool:
        futures = {
            pool.submit(
                configure_primary_vm,
                vm_ips[g["primary"]],
                g["name"],
                passwords[g["name"]],
            ): g["name"]
            for g in GROUPS
        }
        for f in as_completed(futures):
            f.result()

    log("Phase 4 complete.\n")
    return passwords


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Intra-Group SSH
# ─────────────────────────────────────────────────────────────────────────────


def setup_intragroup_ssh(vm_ips: dict[str, str]) -> None:
    """
    For each group:
      1. Generate an ed25519 key pair in the student's ~/.ssh on the primary.
      2. Authorise that public key on every worker in the group.
      3. Pre-populate the primary's known_hosts so the first SSH doesn't prompt.
    """
    log("=== Phase 5: Intra-Group SSH ===")

    for group in GROUPS:
        primary_name = group["primary"]
        primary_ip   = vm_ips[primary_name]

        log(f"  [{group['name']}] Generating student SSH key on primary ...")
        with connection(primary_ip) as c:
            c.run(
                "[ -f /home/student/.ssh/id_ed25519 ] || "
                "sudo -u student ssh-keygen -t ed25519 -N '' "
                "-f /home/student/.ssh/id_ed25519 -C 'student@k8s-seminar'",
                hide=True,
            )
            result = c.run("cat /home/student/.ssh/id_ed25519.pub", hide=True)
            student_pubkey = result.stdout.strip()

        for worker_name in group["workers"]:
            worker_ip = vm_ips[worker_name]

            log(f"  [{group['name']}] Authorising primary key on {worker_name} ...")
            with connection(worker_ip) as c:
                # Write to a temp file then append — avoids shell-quoting issues
                put_text(c, student_pubkey + "\n", "/tmp/add_auth_key")
                c.run(
                    "cat /tmp/add_auth_key >> /home/student/.ssh/authorized_keys && "
                    "sort -u /home/student/.ssh/authorized_keys "
                    "    -o /home/student/.ssh/authorized_keys && "
                    "rm -f /tmp/add_auth_key",
                    hide=True,
                )

            log(f"  [{group['name']}] Adding {worker_name} to known_hosts on primary ...")
            with connection(primary_ip) as c:
                # Scan by IP and by short hostname so both work from the primary
                c.run(
                    f"ssh-keyscan -H {worker_ip} >> "
                    f"/home/student/.ssh/known_hosts 2>/dev/null",
                    hide=True,
                )
                c.run(
                    f"ssh-keyscan -H {worker_name} >> "
                    f"/home/student/.ssh/known_hosts 2>/dev/null",
                    hide=True,
                )
                c.run(
                    "sort -u /home/student/.ssh/known_hosts "
                    "    -o /home/student/.ssh/known_hosts && "
                    "chown student:student /home/student/.ssh/known_hosts",
                    hide=True,
                )

        log(f"  [{group['name']}] Intra-group SSH configured.")

    log("Phase 5 complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — Summary
# ─────────────────────────────────────────────────────────────────────────────


def print_summary(vm_ips: dict[str, str], passwords: dict[str, str]) -> None:
    sep = "=" * 72
    print(f"\n{sep}")
    print("  PROVISIONING COMPLETE")
    print(sep)

    for group in GROUPS:
        gname    = group["name"]
        primary  = group["primary"]
        prim_ip  = vm_ips[primary]
        url      = f"https://{gname}.{DOMAIN_SUFFIX}"

        print(f"\n  Group      : {gname}")
        print(f"  Primary VM : {primary}  ({prim_ip})")
        print(f"  URL        : {url}")
        print(f"  Password   : {passwords[gname]}")
        print(f"  SSH        : ssh student@{prim_ip}")
        for w in group["workers"]:
            print(f"  Worker     : {w}  ({vm_ips[w]})")
            print(f"             → from primary: ssh student@{w}")

    print()
    print("  NOTES:")
    print("  • Caddy will obtain a Let's Encrypt certificate on the first HTTPS")
    print("    request. Allow ~60 s after DNS propagates before opening the URLs.")
    print("  • DNS is set as grey-cloud (proxy OFF) — required for HTTP-01 challenge.")
    print("  • Workers are reachable by hostname from their group's primary only.")
    print(sep)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    vm_ips = provision_hetzner()

    # DNS setup (fast) and base OS config (slow) run concurrently
    with ThreadPoolExecutor(max_workers=2) as pool:
        dns_future  = pool.submit(setup_dns, vm_ips)
        base_future = pool.submit(configure_all_base, vm_ips)
        dns_future.result()
        base_future.result()

    passwords = configure_all_primaries(vm_ips)
    setup_intragroup_ssh(vm_ips)
    print_summary(vm_ips, passwords)


if __name__ == "__main__":
    main()
