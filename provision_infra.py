#!/usr/bin/env python3
"""
k8s-seminar Infrastructure VM Provisioner
==========================================

Provisions a single 'infra' VM on Hetzner Cloud and deploys:
  - Harbor container registry  → https://harbor.k8s.it-scholar.com
  - ArgoCD GitOps controller   → https://argocd.k8s.it-scholar.com

Both run inside k3s on the infra VM.  Caddy handles Let's Encrypt TLS
via HTTP-01 challenge (DNS set grey-cloud / proxy OFF).

After Harbor and ArgoCD are running the script also:
  • Fetches the k3s kubeconfigs from group1-primary and group2-primary.
  • Registers those two clusters in ArgoCD so it can deploy workloads.
  • Creates a system-level Harbor robot account and injects an
    image-pull Secret into the default namespace of every group cluster
    so pods can pull from harbor.k8s.it-scholar.com.

Usage:
    pip install -r requirements.txt
    # (same .env as provision.py)
    python provision_infra.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import socket
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
import paramiko

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

INFRA_VM_NAME     = "infra"
INFRA_SERVER_TYPE = "cpx42"         # 8 vCPU / 16 GB RAM — comfortably runs k3s + Harbor + ArgoCD
IMAGE_NAME        = "ubuntu-24.04"
LOCATION_NAME     = "nbg1"
DOMAIN_SUFFIX     = "k8s.it-scholar.com"
HETZNER_KEY_NAME  = "k8s-seminar-provisioner"
INFRA_FW_NAME     = "k8s-seminar-infra-fw"

HARBOR_DOMAIN     = f"harbor.{DOMAIN_SUFFIX}"
ARGOCD_DOMAIN     = f"argocd.{DOMAIN_SUFFIX}"

HARBOR_NODEPORT   = 30002   # HTTP NodePort for Harbor (TLS terminated by Caddy)
ARGOCD_NODEPORT   = 30080   # HTTP NodePort for ArgoCD server (insecure mode)

HARBOR_ROBOT_NAME = "seminar-clusters"   # system-level robot account

# ─────────────────────────────────────────────────────────────────────────────
# Helm chart values
# ─────────────────────────────────────────────────────────────────────────────

_HARBOR_VALUES_TPL = """\
expose:
  type: nodePort
  tls:
    enabled: false
  nodePort:
    ports:
      http:
        port: 80
        nodePort: {harbor_nodeport}

externalURL: https://{harbor_domain}

harborAdminPassword: "{admin_password}"

persistence:
  enabled: true
  resourcePolicy: keep
  persistentVolumeClaim:
    registry:
      size: 10Gi
    jobservice:
      jobLog:
        size: 1Gi
    database:
      size: 1Gi
    redis:
      size: 1Gi
    trivy:
      size: 5Gi

trivy:
  enabled: true

notary:
  enabled: false

portal:
  replicas: 1
core:
  replicas: 1
jobservice:
  replicas: 1
registry:
  replicas: 1
"""

_ARGOCD_VALUES_TPL = """\
server:
  extraArgs:
    - --insecure
  service:
    type: NodePort
    nodePortHttp: {argocd_nodeport}
    nodePortHttps: 30443

configs:
  params:
    server.insecure: "true"
"""

_CADDY_INSTALL_SH = """\
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy
"""

# ─────────────────────────────────────────────────────────────────────────────
# Credentials — loaded from .env (same file as provision.py)
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        sys.exit(f"ERROR: Missing required environment variable '{key}'. Check .env.")
    return val


HCLOUD_TOKEN    = _require("HCLOUD_TOKEN")
CF_API_TOKEN    = _require("CF_API_TOKEN")
CF_ZONE_ID      = _require("CF_ZONE_ID")
SSH_PRIVATE_KEY = os.path.expanduser(_require("SSH_PRIVATE_KEY_PATH"))
SSH_PUBLIC_KEY  = os.path.expanduser(_require("SSH_PUBLIC_KEY_PATH"))

hc = HCloudClient(token=HCLOUD_TOKEN)
cf = cf_module.Cloudflare(api_token=CF_API_TOKEN)


def _build_group_primaries() -> dict[str, str]:
    raw = _require("STUDENTS")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        sys.exit("ERROR: STUDENTS must contain at least one name.")
    return {n: f"{n}-primary" for n in names}


GROUP_PRIMARIES: dict[str, str] = _build_group_primaries()

# ─────────────────────────────────────────────────────────────────────────────
# Utilities  (mirrors provision.py helpers)
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def gen_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def connection(ip: str, user: str = "root") -> Connection:
    import socket as _socket
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    sock.settimeout(90)
    sock.connect((ip, 22))
    conn = Connection(
        host=ip,
        user=user,
        connect_kwargs={
            "key_filename": SSH_PRIVATE_KEY,
            "timeout": 90,
            "look_for_keys": False,
            "allow_agent": False,
            "sock": sock,
        },
    )
    conn.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return conn


def wait_for_ssh(ip: str, timeout: int = 600) -> None:
    import subprocess
    log(f"  Waiting for SSH on {ip} ...")
    deadline = time.time() + timeout
    last_err: str = ""
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["nc", "-zw3", ip, "22"],
                capture_output=True, timeout=10,
            )
            if r.returncode == 0:
                log(f"  SSH ready: {ip}")
                return
            last_err = f"nc exited {r.returncode}: {r.stderr.decode().strip()}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(3)
    raise TimeoutError(
        f"SSH never became available on {ip} after {timeout}s (last: {last_err})"
    )


def put_text(c: Connection, content: str, remote_path: str) -> None:
    """Upload a string as a remote file (avoids shell-quoting pitfalls)."""
    c.put(io.BytesIO(content.encode("utf-8")), remote_path)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Hetzner VM
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_ssh_key() -> object:
    existing = hc.ssh_keys.get_by_name(HETZNER_KEY_NAME)
    if existing:
        log(f"  Hetzner SSH key '{HETZNER_KEY_NAME}' already exists, reusing.")
        return existing
    pub = Path(SSH_PUBLIC_KEY).read_text().strip()
    log(f"  Uploading SSH key '{HETZNER_KEY_NAME}' to Hetzner ...")
    return hc.ssh_keys.create(name=HETZNER_KEY_NAME, public_key=pub)


def _ensure_firewall() -> object:
    existing = hc.firewalls.get_by_name(INFRA_FW_NAME)
    if existing:
        log(f"  Firewall '{INFRA_FW_NAME}' already exists, reusing.")
        return existing
    log(f"  Creating firewall '{INFRA_FW_NAME}' ...")
    rules = [
        FirewallRule(
            direction="in",
            protocol="tcp",
            port=p,
            source_ips=["0.0.0.0/0", "::/0"],
            description=f"Allow inbound TCP {p}",
        )
        for p in ["22", "80", "443"]
    ]
    result = hc.firewalls.create(name=INFRA_FW_NAME, rules=rules)
    return result.firewall


def provision_vm() -> str:
    """Phase 1: create the infra VM on Hetzner; return its public IPv4."""
    log("=== Phase 1: Hetzner VM ===")

    existing = hc.servers.get_by_name(INFRA_VM_NAME)
    if existing:
        ip = existing.public_net.ipv4.ip
        log(f"  VM '{INFRA_VM_NAME}' already exists (IP: {ip}), skipping creation.")
        log("Phase 1 complete.\n")
        return ip

    ssh_key = _ensure_ssh_key()
    fw      = _ensure_firewall()

    log(f"  Creating VM '{INFRA_VM_NAME}' ({INFRA_SERVER_TYPE}, {IMAGE_NAME}, {LOCATION_NAME}) ...")
    response = hc.servers.create(
        name=INFRA_VM_NAME,
        server_type=ServerType(name=INFRA_SERVER_TYPE),
        image=Image(name=IMAGE_NAME),
        location=Location(name=LOCATION_NAME),
        ssh_keys=[ssh_key],
        firewalls=[fw],
    )
    response.action.wait_until_finished()
    server = hc.servers.get_by_name(INFRA_VM_NAME)
    ip = server.public_net.ipv4.ip
    log(f"  VM '{INFRA_VM_NAME}' created (IP: {ip})")
    log("Phase 1 complete.\n")
    return ip


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Cloudflare DNS
# ─────────────────────────────────────────────────────────────────────────────


def _upsert_dns(name: str, ip: str) -> None:
    all_records = list(cf.dns.records.list(zone_id=CF_ZONE_ID))
    existing = [
        r for r in all_records
        if getattr(r, "name", "") == name and getattr(r, "type", "") == "A"
    ]
    if existing:
        rec = existing[0]
        if getattr(rec, "content", "") == ip:
            log(f"  DNS {name} -> {ip} already correct, skipping.")
            return
        log(f"  Updating DNS {name} -> {ip} ...")
        cf.dns.records.update(
            dns_record_id=rec.id,
            zone_id=CF_ZONE_ID,
            name=name, type="A", content=ip, proxied=False, ttl=300,
        )
    else:
        log(f"  Creating DNS {name} -> {ip} ...")
        cf.dns.records.create(
            zone_id=CF_ZONE_ID,
            name=name, type="A", content=ip, proxied=False, ttl=300,
        )


def setup_dns(ip: str) -> None:
    """Phase 2: create A records for harbor.* and argocd.*."""
    log("=== Phase 2: Cloudflare DNS ===")
    _upsert_dns(HARBOR_DOMAIN, ip)
    _upsert_dns(ARGOCD_DOMAIN, ip)
    log("Phase 2 complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Base OS
# ─────────────────────────────────────────────────────────────────────────────


def configure_base(ip: str) -> None:
    """Phase 3: wait for SSH, then upgrade packages and install essentials."""
    log("=== Phase 3: Base OS Configuration ===")
    wait_for_ssh(ip)
    with connection(ip) as c:
        log("  Upgrading packages ...")
        c.run(
            "apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq",
            hide=True,
        )
        c.run(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            "curl git vim net-tools apt-transport-https ca-certificates gnupg",
            hide=True,
        )
    log("Phase 3 complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — k3s + Helm
# ─────────────────────────────────────────────────────────────────────────────


def install_k3s_and_helm(ip: str) -> None:
    """Phase 4: install k3s (without traefik) and Helm 3."""
    log("=== Phase 4: k3s + Helm ===")
    with connection(ip) as c:
        # k3s
        result = c.run("command -v k3s >/dev/null 2>&1 && echo yes || echo no", hide=True)
        if result.stdout.strip() == "yes":
            log("  k3s already installed, skipping.")
        else:
            log("  Installing k3s (traefik and servicelb disabled) ...")
            c.run(
                "curl -sfL https://get.k3s.io | "
                "INSTALL_K3S_EXEC='--disable traefik --disable servicelb' sh -",
                hide=True,
                timeout=300,
            )

        log("  Waiting for k3s node to become Ready ...")
        for _ in range(60):
            res = c.run(
                "k3s kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready' || true",
                hide=True,
            )
            if res.stdout.strip() == "1":
                break
            time.sleep(5)
        else:
            raise TimeoutError("k3s node never reached Ready state")

        # Make kubeconfig readable by root scripts
        c.run("chmod 600 /etc/rancher/k3s/k3s.yaml", hide=True)
        c.run(
            "grep -qF 'KUBECONFIG' /root/.bashrc || "
            "echo 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml' >> /root/.bashrc",
            hide=True,
        )

        # Helm 3
        result = c.run("command -v helm >/dev/null 2>&1 && echo yes || echo no", hide=True)
        if result.stdout.strip() == "yes":
            log("  Helm already installed, skipping.")
        else:
            log("  Installing Helm 3 ...")
            c.run(
                "curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash",
                hide=True,
                timeout=120,
            )

    log("Phase 4 complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Harbor
# ─────────────────────────────────────────────────────────────────────────────


def install_harbor(ip: str, harbor_password: str) -> None:
    """Phase 5: deploy Harbor via Helm into the 'harbor' namespace."""
    log("=== Phase 5: Harbor ===")

    values = _HARBOR_VALUES_TPL.format(
        harbor_nodeport=HARBOR_NODEPORT,
        harbor_domain=HARBOR_DOMAIN,
        admin_password=harbor_password,
    )

    with connection(ip) as c:
        log("  Adding Harbor Helm repo ...")
        c.run(
            "KUBECONFIG=/etc/rancher/k3s/k3s.yaml "
            "helm repo add harbor https://helm.goharbor.io 2>/dev/null; "
            "KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm repo update",
            hide=True,
        )

        put_text(c, values, "/root/harbor-values.yaml")

        log("  Installing Harbor (may take several minutes) ...")
        c.run(
            "KUBECONFIG=/etc/rancher/k3s/k3s.yaml "
            "helm upgrade --install harbor harbor/harbor "
            "--namespace harbor --create-namespace "
            "--values /root/harbor-values.yaml "
            "--wait --timeout 12m",
            hide=True,
            timeout=800,
        )

    log("Phase 5 complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — ArgoCD
# ─────────────────────────────────────────────────────────────────────────────


def install_argocd(ip: str) -> str:
    """Phase 6: deploy ArgoCD via Helm (insecure mode); return initial admin password."""
    log("=== Phase 6: ArgoCD ===")

    values = _ARGOCD_VALUES_TPL.format(argocd_nodeport=ARGOCD_NODEPORT)

    with connection(ip) as c:
        log("  Adding ArgoCD Helm repo ...")
        c.run(
            "KUBECONFIG=/etc/rancher/k3s/k3s.yaml "
            "helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null; "
            "KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm repo update",
            hide=True,
        )

        put_text(c, values, "/root/argocd-values.yaml")

        log("  Installing ArgoCD ...")
        c.run(
            "KUBECONFIG=/etc/rancher/k3s/k3s.yaml "
            "helm upgrade --install argocd argo/argo-cd "
            "--namespace argocd --create-namespace "
            "--values /root/argocd-values.yaml "
            "--wait --timeout 10m",
            hide=True,
            timeout=700,
        )

        log("  Retrieving initial ArgoCD admin password ...")
        result = c.run(
            "KUBECONFIG=/etc/rancher/k3s/k3s.yaml "
            "kubectl -n argocd get secret argocd-initial-admin-secret "
            "-o jsonpath='{.data.password}' | base64 -d",
            hide=True,
        )
        argocd_password = result.stdout.strip()

    log("Phase 6 complete.\n")
    return argocd_password


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7 — Caddy TLS
# ─────────────────────────────────────────────────────────────────────────────


def configure_caddy(ip: str) -> None:
    """Phase 7: install Caddy and configure TLS reverse-proxy for Harbor + ArgoCD."""
    log("=== Phase 7: Caddy TLS ===")

    caddyfile = (
        f"{HARBOR_DOMAIN} {{\n"
        f"    reverse_proxy localhost:{HARBOR_NODEPORT}\n"
        f"    log {{\n"
        f"        output file /var/log/caddy/harbor-access.log\n"
        f"    }}\n"
        f"}}\n"
        f"\n"
        f"{ARGOCD_DOMAIN} {{\n"
        f"    reverse_proxy localhost:{ARGOCD_NODEPORT} {{\n"
        f"        flush_interval -1\n"
        f"    }}\n"
        f"    log {{\n"
        f"        output file /var/log/caddy/argocd-access.log\n"
        f"    }}\n"
        f"}}\n"
    )

    with connection(ip) as c:
        log("  Installing Caddy ...")
        c.run(_CADDY_INSTALL_SH, hide=True)

        log("  Writing Caddyfile ...")
        c.run("mkdir -p /var/log/caddy", hide=True)
        put_text(c, caddyfile, "/etc/caddy/Caddyfile")
        c.run("systemctl enable caddy && systemctl restart caddy", hide=True)

    log("Phase 7 complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8 — Cluster credentials + Harbor pull secrets
# ─────────────────────────────────────────────────────────────────────────────


def _vm_ip(server_name: str) -> str | None:
    """Return the public IPv4 of a Hetzner server by name, or None."""
    server = hc.servers.get_by_name(server_name)
    return server.public_net.ipv4.ip if server else None


_KUBECONFIG_PATHS = [
    "/etc/kubernetes/admin.conf",        # kubeadm
    "/etc/rancher/k3s/k3s.yaml",         # k3s
]


def _fetch_raw_kubeconfig(vm_ip: str) -> str | None:
    """
    SSH into a group primary and return the raw kubeconfig YAML string.
    Tries kubeadm (/etc/kubernetes/admin.conf) then k3s paths.
    Returns None if neither is present.
    """
    try:
        with connection(vm_ip) as c:
            for path in _KUBECONFIG_PATHS:
                result = c.run(
                    f"[ -f {path} ] && cat {path} || echo NOT_FOUND",
                    hide=True,
                )
                raw = result.stdout.strip()
                if raw != "NOT_FOUND":
                    log(f"    Found kubeconfig at {path}")
                    return raw
        return None
    except Exception as exc:
        log(f"    Warning: SSH to {vm_ip} failed: {exc}")
        return None


def _parse_kubeconfig(raw_yaml: str, external_ip: str) -> dict | None:
    """
    Extract ca, cert, key (base64) from a k3s kubeconfig.
    Uses simple regex to avoid a PyYAML dependency.
    Returns {'server', 'ca_data', 'cert_data', 'key_data'} or None.
    """
    def _field(key: str) -> str | None:
        # Matches:  key: <value>   (whole line, possibly indented)
        m = re.search(rf"^\s*{re.escape(key)}:\s*(\S+)", raw_yaml, re.MULTILINE)
        return m.group(1) if m else None

    ca   = _field("certificate-authority-data")
    cert = _field("client-certificate-data")
    key  = _field("client-key-data")

    if not all([ca, cert, key]):
        log("    Warning: could not parse all kubeconfig fields.")
        return None

    return {
        "server":    f"https://{external_ip}:6443",
        "ca_data":   ca,
        "cert_data": cert,
        "key_data":  key,
    }


def _build_argocd_cluster_secret(cluster_name: str, fields: dict) -> str:
    """
    Build an ArgoCD cluster Secret YAML manifest using TLS client-certificate auth.
    """
    config = json.dumps({
        "tlsClientConfig": {
            "insecure": False,
            "caData":   fields["ca_data"],
            "certData": fields["cert_data"],
            "keyData":  fields["key_data"],
        }
    }, separators=(",", ":"))

    return (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        f"  name: cluster-{cluster_name}\n"
        "  namespace: argocd\n"
        "  labels:\n"
        "    argocd.argoproj.io/secret-type: cluster\n"
        "stringData:\n"
        f"  name: {cluster_name}\n"
        f"  server: \"{fields['server']}\"\n"
        f"  config: '{config}'\n"
    )


def _create_harbor_robot(infra_ip: str, harbor_password: str) -> str | None:
    """
    Create a system-level Harbor robot account via the Harbor API (called
    on the infra VM via localhost to avoid DNS/TLS timing issues).
    Returns the robot secret token, or None on failure.
    """
    log(f"  Creating Harbor robot account '{HARBOR_ROBOT_NAME}' ...")

    payload = json.dumps({
        "name": HARBOR_ROBOT_NAME,
        "description": "k8s-seminar cluster pull/push account",
        "duration": -1,
        "level": "system",
        "permissions": [
            {
                "kind": "project",
                "namespace": "*",
                "access": [
                    {"resource": "repository", "action": "pull"},
                    {"resource": "repository", "action": "push"},
                    {"resource": "artifact",   "action": "read"},
                ],
            }
        ],
    })

    basic_auth = base64.b64encode(f"admin:{harbor_password}".encode()).decode()
    curl_cmd = (
        f"curl -sf -X POST "
        f"-H 'Content-Type: application/json' "
        f"-H 'Authorization: Basic {basic_auth}' "
        f"http://localhost:{HARBOR_NODEPORT}/api/v2.0/robots "
        f"-d '{payload}'"
    )

    basic_auth = base64.b64encode(f"admin:{harbor_password}".encode()).decode()

    # Check if the robot account already exists first
    check_cmd = (
        f"curl -sf "
        f"-H 'Authorization: Basic {basic_auth}' "
        f"http://localhost:{HARBOR_NODEPORT}/api/v2.0/robots"
    )

    for attempt in range(10):
        try:
            with connection(infra_ip) as c:
                # If account already exists, return a sentinel so caller skips creation
                r = c.run(check_cmd, hide=True)
                existing = json.loads(r.stdout.strip())
                match = next((x for x in existing if x.get("name") == f"robot${HARBOR_ROBOT_NAME}"), None)
                if match:
                    log(f"  Robot account robot${HARBOR_ROBOT_NAME} already exists — "
                        "token not re-retrievable; pull secrets skipped this run.")
                    return None

                result = c.run(curl_cmd, hide=True)
                data = json.loads(result.stdout.strip())
                secret = data.get("secret")
                if secret:
                    log(f"  Robot account created (full name: {data.get('name')}).")
                    return secret
        except Exception as exc:
            log(f"    Attempt {attempt + 1}/10 failed: {exc}  — retrying in 30 s ...")
            time.sleep(30)

    log("  WARNING: Could not create Harbor robot account after 10 attempts.")
    return None


def _deploy_pull_secret(vm_ip: str, group_name: str, robot_token: str) -> None:
    """Create a kubernetes.io/dockerconfigjson secret in the group cluster."""
    auth = base64.b64encode(
        f"robot${HARBOR_ROBOT_NAME}:{robot_token}".encode()
    ).decode()

    docker_config = json.dumps({
        "auths": {
            HARBOR_DOMAIN: {
                "username": f"robot${HARBOR_ROBOT_NAME}",
                "password": robot_token,
                "auth": auth,
            }
        }
    })

    manifest = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: harbor-pull-secret\n"
        "  namespace: default\n"
        "type: kubernetes.io/dockerconfigjson\n"
        "stringData:\n"
        f"  .dockerconfigjson: '{docker_config}'\n"
    )

    with connection(vm_ip) as c:
        put_text(c, manifest, "/tmp/harbor-pull-secret.yaml")
        c.run(
            "KUBECONFIG=/etc/rancher/k3s/k3s.yaml "
            "kubectl apply -f /tmp/harbor-pull-secret.yaml; "
            "rm -f /tmp/harbor-pull-secret.yaml",
            hide=True,
        )
    log(f"  [{group_name}] harbor-pull-secret deployed to default namespace.")


def wire_clusters(infra_ip: str, harbor_password: str) -> dict[str, bool]:
    """
    Phase 8: for each group cluster:
      1. Fetch k3s kubeconfig and register cluster in ArgoCD.
      2. Create Harbor pull secret in the cluster's default namespace.
    Returns {group_name: True/False} indicating whether each cluster was wired.
    """
    log("=== Phase 8: Cluster Credentials + Harbor Pull Secrets ===")

    robot_token = _create_harbor_robot(infra_ip, harbor_password)

    results: dict[str, bool] = {}
    for group_name, primary_name in GROUP_PRIMARIES.items():
        log(f"  [{group_name}] Looking up VM '{primary_name}' ...")
        vm_ip = _vm_ip(primary_name)
        if vm_ip is None:
            log(f"  [{group_name}] VM not found on Hetzner — skipping.")
            results[group_name] = False
            continue

        log(f"  [{group_name}] Fetching k3s kubeconfig from {vm_ip} ...")
        raw = _fetch_raw_kubeconfig(vm_ip)
        if raw is None:
            log(
                f"  [{group_name}] k3s not installed on {primary_name} yet. "
                "Re-run provision_infra.py after k3s is set up on that VM."
            )
            results[group_name] = False
            continue

        fields = _parse_kubeconfig(raw, vm_ip)
        if fields is None:
            log(f"  [{group_name}] Failed to parse kubeconfig — skipping.")
            results[group_name] = False
            continue

        # Register cluster in ArgoCD
        log(f"  [{group_name}] Registering cluster in ArgoCD ...")
        secret_yaml = _build_argocd_cluster_secret(group_name, fields)
        with connection(infra_ip) as c:
            put_text(c, secret_yaml, f"/root/argocd-cluster-{group_name}.yaml")
            c.run(
                f"KUBECONFIG=/etc/rancher/k3s/k3s.yaml "
                f"kubectl apply -f /root/argocd-cluster-{group_name}.yaml",
                hide=True,
            )
        log(f"  [{group_name}] Cluster registered → {fields['server']}")

        # Harbor pull secret in the group cluster
        if robot_token:
            log(f"  [{group_name}] Deploying Harbor pull secret ...")
            try:
                _deploy_pull_secret(vm_ip, group_name, robot_token)
            except Exception as exc:
                log(f"  [{group_name}] WARNING: pull secret failed: {exc}")

        results[group_name] = True

    log("Phase 8 complete.\n")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────


def print_summary(
    infra_ip: str,
    harbor_password: str,
    argocd_password: str,
    cluster_results: dict[str, bool],
) -> None:
    sep = "=" * 72
    print(f"\n{sep}")
    print("  INFRASTRUCTURE PROVISIONING COMPLETE")
    print(sep)
    print(f"\n  Infra VM  : {INFRA_VM_NAME}  ({infra_ip})")
    print(f"  SSH       : ssh root@{infra_ip}")

    print(f"\n  Harbor Registry")
    print(f"  URL       : https://{HARBOR_DOMAIN}")
    print(f"  Username  : admin")
    print(f"  Password  : {harbor_password}")
    print(f"  Robot     : robot${HARBOR_ROBOT_NAME}")
    print(f"  Docker    : docker login {HARBOR_DOMAIN}")

    print(f"\n  ArgoCD")
    print(f"  URL       : https://{ARGOCD_DOMAIN}")
    print(f"  Username  : admin")
    print(f"  Password  : {argocd_password}")

    print(f"\n  Group Cluster Registration")
    for group_name, ok in cluster_results.items():
        status = "registered" if ok else "SKIPPED (k3s not found — re-run later)"
        print(f"  {group_name:<10}: {status}")

    print(f"\n  NOTES:")
    print(f"  • Caddy will obtain Let's Encrypt certs on the first HTTPS request.")
    print(f"    Allow ~60 s after DNS propagates before opening the URLs.")
    print(f"  • DNS is grey-cloud (Cloudflare proxy OFF) — required for HTTP-01.")
    print(f"  • Pull secret 'harbor-pull-secret' was created in the default")
    print(f"    namespace of each successfully registered group cluster.")
    print(f"  • To add robot${HARBOR_ROBOT_NAME} to a namespace's serviceAccount:")
    print(f"    kubectl patch sa default -p '{{\"imagePullSecrets\": [{{\"name\": \"harbor-pull-secret\"}}]}}'")
    print(f"  • ArgoCD gRPC (CLI) works via port-forward if needed:")
    print(f"    kubectl port-forward svc/argocd-server -n argocd 8080:80")
    print(sep)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    harbor_password = gen_password()

    # Phase 1: VM
    infra_ip = provision_vm()

    # Phase 2 + 3 can run concurrently (DNS setup is fast; base OS takes time)
    with ThreadPoolExecutor(max_workers=2) as pool:
        dns_fut  = pool.submit(setup_dns, infra_ip)
        base_fut = pool.submit(configure_base, infra_ip)
        dns_fut.result()
        base_fut.result()

    # Phases 4-7 are sequential on the VM
    install_k3s_and_helm(infra_ip)
    install_harbor(infra_ip, harbor_password)
    argocd_password = install_argocd(infra_ip)
    configure_caddy(infra_ip)

    # Phase 8: wire clusters (best-effort — group VMs may not have k3s yet)
    cluster_results = wire_clusters(infra_ip, harbor_password)

    print_summary(infra_ip, harbor_password, argocd_password, cluster_results)


if __name__ == "__main__":
    main()
