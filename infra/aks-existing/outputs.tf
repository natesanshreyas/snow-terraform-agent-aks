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

output "apim_gateway_url" {
  description = "Use this URL as the endpoint in the ServiceNow REST Message (replaces the AKS ingress URL). Empty if not created."
  value       = var.create_apim ? azurerm_api_management.apim[0].gateway_url : ""
}

output "key_vault_uri" {
  description = "Key Vault URI for referencing secrets from the pod. Empty if not created."
  value       = var.create_key_vault ? azurerm_key_vault.kv[0].vault_uri : ""
}

output "next_steps" {
  description = "Summary of what to do after terraform apply."
  value = <<-EOT
    Namespace created: ${kubernetes_namespace.agent.metadata[0].name}

    1. Fill in k8s/configmap.yaml with these values:
    ${var.create_service_bus ? "   AZURE_SERVICE_BUS_HOSTNAME: \"${var.service_bus_name}.servicebus.windows.net\"" : "   (Service Bus not created)"}
    ${var.create_blob_storage ? "   AZURE_STORAGE_ACCOUNT_NAME: \"${var.storage_account_name}\"" : "   (Blob Storage not created)"}
    ${var.create_app_insights ? "   APPLICATIONINSIGHTS_CONNECTION_STRING: (run: terraform output app_insights_connection_string)" : "   (App Insights not created)"}

    2. Apply k8s manifests:
       kubectl apply -f k8s/ -n ${kubernetes_namespace.agent.metadata[0].name}

    ${var.create_service_bus ? "3. Apply KEDA manifests (after editing keda-scaledobject.yaml with your ASB namespace name):\n       kubectl apply -f k8s/keda-auth.yaml -n ${kubernetes_namespace.agent.metadata[0].name}\n       kubectl apply -f k8s/keda-scaledobject.yaml -n ${kubernetes_namespace.agent.metadata[0].name}" : ""}

    ${var.create_apim ? "4. Set the ServiceNow REST Message endpoint to:\n       ${azurerm_api_management.apim[0].gateway_url}/api/provision" : ""}

    ${var.create_key_vault ? "5. Store secrets in Key Vault:\n       az keyvault secret set --vault-name ${var.key_vault_name} --name SERVICENOW-PASSWORD --value <value>\n       az keyvault secret set --vault-name ${var.key_vault_name} --name GITHUB-PAT --value <value>\n       az keyvault secret set --vault-name ${var.key_vault_name} --name AZURE-CLIENT-SECRET --value <value>" : ""}
  EOT
}
