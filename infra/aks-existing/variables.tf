variable "subscription_id" {
  description = "Azure subscription ID where optional resources will be created."
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
# Service Bus
# ---------------------------------------------------------------------------

variable "create_service_bus" {
  description = "Create an Azure Service Bus namespace and queue for async provisioning mode + KEDA autoscaling."
  type        = bool
  default     = false
}

variable "service_bus_name" {
  description = "Name for the Service Bus namespace (must be globally unique). Required if create_service_bus = true."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Blob Storage
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Application Insights
# ---------------------------------------------------------------------------

variable "create_app_insights" {
  description = "Create an Application Insights workspace for telemetry and evaluator score logging."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# API Management
# ---------------------------------------------------------------------------

variable "create_apim" {
  description = "Create an Azure API Management instance (Consumption tier) in front of the AKS ingress."
  type        = bool
  default     = false
}

variable "apim_name" {
  description = "Name for the APIM instance (must be globally unique). Required if create_apim = true."
  type        = string
  default     = ""
}

variable "apim_publisher_name" {
  description = "Publisher name shown in the APIM developer portal. Required if create_apim = true."
  type        = string
  default     = ""
}

variable "apim_publisher_email" {
  description = "Publisher email for APIM notifications. Required if create_apim = true."
  type        = string
  default     = ""
}

variable "aks_ingress_url" {
  description = "URL of the AKS ingress endpoint that APIM will route to (e.g. http://snow-agent.eastus2.cloudapp.azure.com). Required if create_apim = true."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Key Vault
# ---------------------------------------------------------------------------

variable "create_key_vault" {
  description = "Create an Azure Key Vault to store sensitive credentials instead of k8s secret.yaml."
  type        = bool
  default     = false
}

variable "key_vault_name" {
  description = "Name for the Key Vault (3-24 chars, globally unique). Required if create_key_vault = true."
  type        = string
  default     = ""
}

variable "tenant_id" {
  description = "Azure AD tenant ID. Required if create_key_vault = true."
  type        = string
  default     = ""
}

variable "pod_identity_object_id" {
  description = "Object ID of the managed identity used by the agent pods. Granted Key Vault Secrets User role. Required if create_key_vault = true."
  type        = string
  default     = ""
}
