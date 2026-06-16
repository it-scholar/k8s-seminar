terraform {
  required_version = ">= 1.5"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.49"
    }
  }
}

module "kubernetes" {
  source  = "hcloud-k8s/kubernetes/hcloud"
  version = "4.7.0"

  cluster_name = var.cluster_name
  hcloud_token = var.hcloud_token

  # Export kubeconfig and talosconfig locally after apply
  cluster_kubeconfig_path  = "kubeconfig"
  cluster_talosconfig_path = "talosconfig"

  # ---------------------------------------------------------------------------
  # Control plane — 3 nodes for HA (must be odd)
  # ---------------------------------------------------------------------------
  control_plane_nodepools = [
    {
      name     = "control"
      type     = "cpx31"
      location = "hel1"
      count    = 3
    }
  ]

  # ---------------------------------------------------------------------------
  # Workers — 6 static nodes
  # ---------------------------------------------------------------------------
  worker_nodepools = [
    {
      name     = "worker"
      type     = "cpx31"
      location = "hel1"
      count    = 6
    }
  ]

  # ---------------------------------------------------------------------------
  # Cluster Autoscaler — additional pool that scales 0–3 on demand
  # ---------------------------------------------------------------------------
  cluster_autoscaler_nodepools = [
    {
      name     = "autoscaler"
      type     = "cpx31"
      location = "hel1"
      min      = 0
      max      = 3
    }
  ]

  # ---------------------------------------------------------------------------
  # Add-ons
  # ---------------------------------------------------------------------------
  cert_manager_enabled       = true
  cilium_gateway_api_enabled = true
}
