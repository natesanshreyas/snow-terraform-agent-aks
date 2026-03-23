# aks-standalone — Demo / Greenfield Deployment

> **Use this only if you do not already have an AKS cluster.**
> If your organization already runs AKS, use [`../aks-existing/`](../aks-existing/) instead.

This Terraform creates everything from scratch in your subscription:

- Resource Group
- Azure Container Registry (Basic)
- AKS Cluster (2× Standard_D2s_v3 nodes)
- AcrPull role assignment (nodes → ACR)
- nginx Ingress Controller (via Helm) + Azure DNS label

## Usage

```bash
cd infra/aks-standalone
terraform init
terraform apply
```

Note the outputs — you'll use them when configuring `k8s/`:

```
hostname            = "snow-agent.eastus2.cloudapp.azure.com"
acr_login_server    = "snowagentacr.azurecr.io"
aks_connect_command = "az aks get-credentials ..."
```
