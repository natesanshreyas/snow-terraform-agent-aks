variable "subscription_id" {
  description = "Azure subscription ID where optional resources (ASB, Blob, App Insights) will be created."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group for optional Azure resources. Can be the same RG as your existing AKS cluster or a separate one."
  type        = string
}

variable "location" {
  description = "Azure region for optional resources. Should match your existing AKS region."
  type        = string
  default     = "eastus2"
}

variable "kube_context" {
  description = "kubectl context name for the existing cluster. Run 'kubectl config get-contexts' to find it."
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace to create for the agent."
  type        = string
  default     = "snow-terraform-agent"
}

# ---------------------------------------------------------------------------
# Optional feature flags
# ---------------------------------------------------------------------------

variable "create_service_bus" {
  description = "Create an Azure Service Bus namespace and queue for async provisioning mode."
  type        = bool
  default     = false
}

variable "service_bus_name" {
  description = "Name for the Service Bus namespace (must be globally unique). Required if create_service_bus = true."
  type        = string
  default     = ""
}

variable "create_blob_storage" {
  description = "Create a Storage Account and 'runs' container for async run state. Use together with create_service_bus."
  type        = bool
  default     = false
}

variable "storage_account_name" {
  description = "Name for the Storage Account (3-24 chars, lowercase, globally unique). Required if create_blob_storage = true."
  type        = string
  default     = ""
}

variable "create_app_insights" {
  description = "Create an Application Insights workspace for telemetry and evaluator score logging."
  type        = bool
  default     = false
}
