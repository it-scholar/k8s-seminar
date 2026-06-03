# k8s-seminar

Automated provisioning scripts for a Kubernetes seminar environment hosted on **Hetzner Cloud**, with DNS managed by **Cloudflare** and TLS via **Let's Encrypt / Caddy**.

## Architecture

```
                  Cloudflare DNS (k8s.it-scholar.com)
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
     infra VM (cpx42)   group1-primary    group2-primary
     ├── k3s               (cpx32)           (cpx32)
     ├── Harbor         ├── code-server    ├── code-server
     └── ArgoCD         └── Caddy TLS      └── Caddy TLS
                              │                 │
                        group1-worker1    group2-worker1
                           (cpx32)           (cpx32)
```

Each seminar group gets:
- **Primary VM** — `code-server` IDE accessible at `https://<group>.k8s.it-scholar.com`, acts as the control-plane node.
- **Worker VM** — joins the group's k8s cluster as a worker node.

Shared infrastructure:
- **Harbor** (`harbor.k8s.it-scholar.com`) — private container registry.
- **ArgoCD** (`argocd.k8s.it-scholar.com`) — GitOps controller pre-registered with all group clusters.

## Prerequisites

- Python ≥ 3.11
- A [Hetzner Cloud](https://www.hetzner.com/cloud) project with an API token
- A [Cloudflare](https://cloudflare.com) account managing the target domain with an API token (Zone:Edit DNS)
- An SSH key pair (e.g. `~/.ssh/id_ed25519`) uploaded to Hetzner as `k8s-seminar-provisioner`

## Setup

```bash
# 1. Clone the repo
git clone git@github.com:it-scholar/k8s-seminar.git
cd k8s-seminar

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
cp .env.example .env
$EDITOR .env   # fill in all values

# 4. Provision group VMs (primary + worker per group)
python provision.py

# 5. Provision the shared infra VM (Harbor + ArgoCD)
python provision_infra.py
```

## Scripts

| Script | Purpose |
|---|---|
| `provision.py` | Creates group VMs, sets up `student` user, installs `code-server` + Caddy, creates DNS records, wires intra-group SSH. |
| `provision_infra.py` | Creates the infra VM, deploys k3s, Harbor, and ArgoCD via Helm, registers group clusters in ArgoCD, injects Harbor image-pull secrets. |

## Environment Variables

See [.env.example](.env.example) for all required variables.

| Variable | Description |
|---|---|
| `HCLOUD_TOKEN` | Hetzner Cloud API token |
| `CF_API_TOKEN` | Cloudflare API token (Zone:Edit DNS) |
| `CF_ZONE_ID` | Cloudflare Zone ID for the target domain |
| `SSH_PRIVATE_KEY_PATH` | Path to the private key used to connect to VMs |
| `SSH_PUBLIC_KEY_PATH` | Path to the corresponding public key |

## Server Specs

| Role | Type | vCPU | RAM |
|---|---|---|---|
| group primary / worker | `cpx32` | 4 | 8 GB |
| infra | `cpx42` | 8 | 16 GB |

All VMs run **Ubuntu 24.04** in the `fsn1` (Falkenstein, DE) region.
