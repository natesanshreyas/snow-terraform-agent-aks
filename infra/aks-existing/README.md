# aks-existing — Deploy into Your Existing AKS Cluster

> **Use this when your organization already runs AKS.**
> If you need a cluster spun up from scratch, use [`../aks-standalone/`](../aks-standalone/) instead.

This Terraform does **not** touch your cluster, VNet, ACR, load balancer, or DNS.
It only provisions the application-layer resources the agent needs:

| Resource | Created | Notes |
|----------|---------|-------|
| Kubernetes namespace | Always | Default: `snow-terraform-agent` |
| Azure Service Bus | Optional | Enables async mode (202 response + worker) |
| Azure Blob Storage | Optional | Run state persistence (pair with ASB) |
| Application Insights | Optional | Telemetry + evaluator score logging |

## Prerequisites

Point kubectl at your existing cluster first:
```bash
az aks get-credentials --resource-group <your-rg> --name <your-cluster>
kubectl config get-contexts   # note the context name
```

## Usage

```bash
cd infra/aks-existing
terraform init

# Sync-only (simplest — no ASB, no Blob):
terraform apply \
  -var="subscription_id=<your-sub-id>" \
  -var="resource_group_name=<your-rg>" \
  -var="kube_context=<your-context>"

# Full async mode with observability:
terraform apply \
  -var="subscription_id=<your-sub-id>" \
  -var="resource_group_name=<your-rg>" \
  -var="kube_context=<your-context>" \
  -var="create_service_bus=true" \
  -var="service_bus_name=snow-agent-asb" \
  -var="create_blob_storage=true" \
  -var="storage_account_name=snowagentstate" \
  -var="create_app_insights=true"
```

## After apply

Deploy the k8s manifests into the namespace this created:
```bash
kubectl apply -f k8s/ -n snow-terraform-agent
```

Copy any connection string outputs into `k8s/configmap.yaml` before applying.
