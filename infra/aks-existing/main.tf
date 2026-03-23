# ---------------------------------------------------------------------------
# snow-terraform-agent — Existing AKS deployment
#
# Use this when deploying into an AKS cluster your organization already owns.
# This does NOT create: AKS, ACR, Load Balancer, DNS, or VNet.
#
# What it provisions:
#   - A Kubernetes namespace for the agent
#   - Optional: Azure Service Bus (async job queue)
#   - Optional: Azure Blob Storage (run state persistence)
#   - Optional: Application Insights (telemetry + evaluator logging)
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
# All agent k8s resources (Deployment, Service, Ingress, ConfigMap, Secret)
# should be applied into this namespace:
#   kubectl apply -f k8s/ -n <namespace>
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
# Set create_service_bus = true to enable.
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
