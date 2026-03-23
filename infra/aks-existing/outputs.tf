output "namespace" {
  description = "Kubernetes namespace created for the agent. Pass this to kubectl apply: kubectl apply -f k8s/ -n <namespace>"
  value       = kubernetes_namespace.agent.metadata[0].name
}

output "service_bus_hostname" {
  description = "Set as AZURE_SERVICE_BUS_HOSTNAME in k8s/configmap.yaml to enable async mode. Empty if not created."
  value       = var.create_service_bus ? "${azurerm_servicebus_namespace.asb[0].name}.servicebus.windows.net" : ""
}

output "storage_account_name" {
  description = "Set as AZURE_STORAGE_ACCOUNT_NAME in k8s/configmap.yaml for run state persistence. Empty if not created."
  value       = var.create_blob_storage ? azurerm_storage_account.state[0].name : ""
}

output "app_insights_connection_string" {
  description = "Set as APPLICATIONINSIGHTS_CONNECTION_STRING in k8s/configmap.yaml. Empty if not created."
  value       = var.create_app_insights ? azurerm_application_insights.ai[0].connection_string : ""
  sensitive   = true
}

output "next_steps" {
  description = "Summary of what to do after terraform apply."
  value = <<-EOT
    Namespace created: ${kubernetes_namespace.agent.metadata[0].name}

    Apply the k8s manifests into this namespace:
      kubectl apply -f k8s/ -n ${kubernetes_namespace.agent.metadata[0].name}

    ${var.create_service_bus ? "Add to k8s/configmap.yaml:\n      AZURE_SERVICE_BUS_HOSTNAME: \"${var.service_bus_name}.servicebus.windows.net\"" : "Service Bus not created (sync mode only)."}

    ${var.create_app_insights ? "Add to k8s/configmap.yaml:\n      APPLICATIONINSIGHTS_CONNECTION_STRING: (run: terraform output app_insights_connection_string)" : "App Insights not created (no telemetry)."}
  EOT
}
