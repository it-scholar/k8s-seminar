variable "cluster_name" {
  type        = string
  description = "Name of the Kubernetes cluster."
  default     = "k8s-seminar"
}

variable "hcloud_token" {
  type        = string
  description = "Hetzner Cloud API token. Keep this secret — never commit it."
  sensitive   = true
}
