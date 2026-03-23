# ---------------------------------------------------------------------------
# snow-terraform-agent — Existing AKS deployment
#
# Use this when deploying into an AKS cluster your organization already owns.
# This does NOT create: AKS, ACR, Load Balancer, DNS, or VNet.
#
# What it provisions:
#   - A Kubernetes namespace for the agent
#   - Optional: Azure Service Bus      (async job queue + KEDA autoscaling signal)
#   - Optional: Azure Blob Storage     (run state persistence)
#   - Optional: Application Insights   (telemetry + evaluator logging)
#   - Optional: Azure API Management   (rate limiting, IP allowlisting for SNOW webhook)
#   - Optional: Azure Key Vault        (secrets store — replaces k8s secret.yaml in prod)
# ---------------------------------------------------------------------------

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

# ---------------------------------------------------------------------------
# Kubernetes provider — points at your existing cluster.
# Prerequisites:
#   az aks get-credentials --resource-group <rg> --name <cluster>
# Then set kube_context to the context name shown by:
#   kubectl config get-contexts
# ---------------------------------------------------------------------------
provider "kubernetes" {
  config_path    = "~/.kube/config"
  config_context = var.kube_context
}

# ---------------------------------------------------------------------------
# Kubernetes Namespace
# ---------------------------------------------------------------------------
resource "kubernetes_namespace" "agent" {
  metadata {
    name = var.namespace
    labels = {
      app        = "snow-terraform-agent"
      managed_by = "terraform"
    }
  }
}

# ---------------------------------------------------------------------------
# Optional: Azure Service Bus
# Enables async provisioning mode — SNOW gets an immediate 202 response and
# a separate worker pod processes the job from the queue.
# Also drives KEDA autoscaling: queue depth → worker pod count.
# ---------------------------------------------------------------------------
resource "azurerm_servicebus_namespace" "asb" {
  count               = var.create_service_bus ? 1 : 0
  name                = var.service_bus_name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "Standard"

  tags = {
    project    = "snow-terraform-agent"
    managed_by = "terraform"
  }
}

resource "azurerm_servicebus_queue" "provisioning" {
  count        = var.create_service_bus ? 1 : 0
  name         = "provisioning-queue"
  namespace_id = azurerm_servicebus_namespace.asb[0].id
}

# ---------------------------------------------------------------------------
# Optional: Azure Blob Storage
# Stores run state JSON (queued → running → completed/failed) so the status
# endpoint can return progress without holding an HTTP connection open.
# Pair with create_service_bus = true for full async mode.
# ---------------------------------------------------------------------------
resource "azurerm_storage_account" "state" {
  count                    = var.create_blob_storage ? 1 : 0
  name                     = var.storage_account_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  tags = {
    project    = "snow-terraform-agent"
    managed_by = "terraform"
  }
}

resource "azurerm_storage_container" "runs" {
  count                 = var.create_blob_storage ? 1 : 0
  name                  = "runs"
  storage_account_id    = azurerm_storage_account.state[0].id
  container_access_type = "private"
}

# ---------------------------------------------------------------------------
# Optional: Application Insights
# Enables distributed tracing (every MCP tool call, LLM call, and run) and
# surfaces evaluator scores in the Azure AI Foundry evals dashboard.
# ---------------------------------------------------------------------------
resource "azurerm_log_analytics_workspace" "law" {
  count               = var.create_app_insights ? 1 : 0
  name                = "${var.namespace}-law"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_application_insights" "ai" {
  count               = var.create_app_insights ? 1 : 0
  name                = "${var.namespace}-ai"
  location            = var.location
  resource_group_name = var.resource_group_name
  workspace_id        = azurerm_log_analytics_workspace.law[0].id
  application_type    = "web"

  tags = {
    project    = "snow-terraform-agent"
    managed_by = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Optional: Azure API Management (Consumption tier)
# Sits in front of the AKS ingress. Provides:
#   - Rate limiting (30 calls / 60 seconds)
#   - IP restriction (allowlist your SNOW instance IP)
#   - Single stable HTTPS endpoint for the SNOW business rule
#
# Set create_apim = true and provide apim_name, apim_publisher_name,
# apim_publisher_email, and aks_ingress_url.
# ---------------------------------------------------------------------------
resource "azurerm_api_management" "apim" {
  count               = var.create_apim ? 1 : 0
  name                = var.apim_name
  location            = var.location
  resource_group_name = var.resource_group_name
  publisher_name      = var.apim_publisher_name
  publisher_email     = var.apim_publisher_email
  sku_name            = "Consumption_0"

  tags = {
    project    = "snow-terraform-agent"
    managed_by = "terraform"
  }
}

resource "azurerm_api_management_api" "provision" {
  count               = var.create_apim ? 1 : 0
  name                = "provisioning-api"
  resource_group_name = var.resource_group_name
  api_management_name = azurerm_api_management.apim[0].name
  revision            = "1"
  display_name        = "Provisioning API"
  path                = ""
  protocols           = ["https"]
  service_url         = var.aks_ingress_url
}

resource "azurerm_api_management_api_operation" "trigger" {
  count               = var.create_apim ? 1 : 0
  operation_id        = "trigger-provision"
  api_name            = azurerm_api_management_api.provision[0].name
  api_management_name = azurerm_api_management.apim[0].name
  resource_group_name = var.resource_group_name
  display_name        = "Trigger Provisioning"
  method              = "POST"
  url_template        = "/api/provision"
}

resource "azurerm_api_management_api_operation" "status" {
  count               = var.create_apim ? 1 : 0
  operation_id        = "get-status"
  api_name            = azurerm_api_management_api.provision[0].name
  api_management_name = azurerm_api_management.apim[0].name
  resource_group_name = var.resource_group_name
  display_name        = "Get Run Status"
  method              = "GET"
  url_template        = "/api/provision/{run_id}/status"

  template_parameter {
    name     = "run_id"
    required = true
    type     = "string"
  }
}

resource "azurerm_api_management_api_policy" "rate_limit" {
  count               = var.create_apim ? 1 : 0
  api_name            = azurerm_api_management_api.provision[0].name
  api_management_name = azurerm_api_management.apim[0].name
  resource_group_name = var.resource_group_name

  xml_content = <<XML
<policies>
  <inbound>
    <rate-limit calls="30" renewal-period="60" />
    <base />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
</policies>
XML
}

# ---------------------------------------------------------------------------
# Optional: Azure Key Vault
# Stores sensitive credentials (SNOW password, GitHub PAT, OpenAI API key,
# service principal secret) so they never live in k8s secret.yaml plain text.
#
# After creation, store secrets manually via:
#   az keyvault secret set --vault-name <name> --name SERVICENOW-PASSWORD --value <value>
#   az keyvault secret set --vault-name <name> --name GITHUB-PAT --value <value>
#   etc.
#
# The pod reads them at runtime via Azure Workload Identity + CSI driver.
# ---------------------------------------------------------------------------
resource "azurerm_key_vault" "kv" {
  count                    = var.create_key_vault ? 1 : 0
  name                     = var.key_vault_name
  location                 = var.location
  resource_group_name      = var.resource_group_name
  tenant_id                = var.tenant_id
  sku_name                 = "standard"
  purge_protection_enabled = false

  tags = {
    project    = "snow-terraform-agent"
    managed_by = "terraform"
  }
}

# Grant the pod's managed identity read access to secrets
resource "azurerm_role_assignment" "pod_kv_secrets_user" {
  count                = var.create_key_vault && var.pod_identity_object_id != "" ? 1 : 0
  scope                = azurerm_key_vault.kv[0].id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = var.pod_identity_object_id
}
