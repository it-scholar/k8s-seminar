output "kubeconfig_path" {
  description = "Path to the generated kubeconfig file."
  value       = "${path.module}/kubeconfig"
}

output "talosconfig_path" {
  description = "Path to the generated talosconfig file."
  value       = "${path.module}/talosconfig"
}
