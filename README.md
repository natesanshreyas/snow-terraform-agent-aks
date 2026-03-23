# ServiceNow → Terraform Provisioning Agent — AKS Accelerator

An AI agent that listens for approved ServiceNow tickets, generates Terraform HCL, opens a GitHub PR, and posts the PR link back to the ticket as a work note.

## How it works

```
SNOW Business Rule (on ticket approval)
  → POST /api/provision  {"ticket_id": "RITM0001234"}

Agent (running in AKS)
  → SNOW MCP:    read ticket details
  → Azure OpenAI: generate Terraform HCL
  → GitHub MCP:  create branch → push files → open PR
  → SNOW MCP:    post PR link as work note
```

---

## Choose your infrastructure path

| | `infra/aks-standalone/` | `infra/aks-existing/` |
|---|---|---|
| **Use when** | You need a cluster provisioned from scratch (demo / greenfield) | Your org already runs AKS |
| **Creates** | RG, AKS, ACR, nginx ingress, DNS label | Namespace only (+ optional ASB / Blob / App Insights) |
| **Does not touch** | — | Your cluster, VNet, ACR, LB, DNS |

---

## What you need before starting

### Azure
- An Azure subscription
- An **Azure OpenAI resource** with a model deployed (gpt-4o or gpt-4.1 recommended)
- An **App Registration** (service principal) with a client secret

### ServiceNow
- A ServiceNow instance (dev PDI is fine for POC)
- Admin credentials

### GitHub
- A GitHub org or account
- A Terraform modules repo (can be empty to start)
- A **Personal Access Token** with `repo` + `workflow` scopes

### Local tools
- Azure CLI (`az`) — logged in (`az login`)
- Terraform >= 1.5
- kubectl
- Docker (or use `az acr build` to build without Docker)

---

## Implementation Path — Existing cluster

### Step 1 — Point kubectl at your cluster

```bash
az aks get-credentials --resource-group <your-rg> --name <your-cluster>
kubectl config get-contexts   # note the context name
```

### Step 2 — Provision application-layer resources

```bash
cd infra/aks-existing
terraform init

# Sync-only (simplest):
terraform apply \
  -var="subscription_id=<your-sub-id>" \
  -var="resource_group_name=<your-rg>" \
  -var="kube_context=<your-context>"

# With async mode + observability:
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

Copy any output values (Service Bus hostname, App Insights connection string) into `k8s/configmap.yaml`.

### Step 3 — Build and push to your ACR

```bash
az acr build --registry <your-acr-name> --image snow-terraform-agent:latest .
```

### Step 4 — Configure the app

See [Step 4 — Configure the app](#step-4--configure-the-app-both-paths) below.

### Step 5 — Update the image in deployment.yaml

```bash
sed -i 's|<ACR_LOGIN_SERVER>|<your-acr>.azurecr.io|g' k8s/deployment.yaml
```

### Step 6 — Configure ingress

`k8s/ingress.yaml` is written for a standalone nginx + public LB. If your org has a different setup, read the comments at the top of that file before applying. Common alternatives:

- **Internal nginx** — change the controller service to use an internal load balancer IP
- **APIM in front** — delete `ingress.yaml`, configure APIM to route to `snow-terraform-agent.<namespace>.svc.cluster.local`
- **Private endpoint / no public IP** — delete `ingress.yaml`, use your org's existing internal gateway

### Step 7 — Deploy

```bash
kubectl apply -f k8s/ -n snow-terraform-agent
kubectl get pods -n snow-terraform-agent -w
```

---

## Step 4 — Configure the app 

### 4a — Non-sensitive config (configmap.yaml)

Edit `k8s/configmap.yaml` and replace every value with your own:

| Key | Where to find it |
|-----|-----------------|
| `AZURE_OPENAI_ENDPOINT` | Azure Portal → your OpenAI resource → Keys and Endpoint |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Azure Portal → your OpenAI resource → Model deployments |
| `AZURE_OPENAI_MODEL_NAME` | Same as deployment name |
| `SERVICENOW_INSTANCE_URL` | Your SNOW instance URL e.g. `https://dev123456.service-now.com` |
| `SERVICENOW_USERNAME` | Your SNOW admin username |
| `GITHUB_ORG` | Your GitHub org or username |
| `GITHUB_TERRAFORM_REPO` | Name of your Terraform modules repo |
| `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |
| `AZURE_CLIENT_ID` | Azure Portal → App Registrations → your app → Application (client) ID |
| `AZURE_TENANT_ID` | Azure Portal → App Registrations → your app → Directory (tenant) ID |

### 4b — Sensitive credentials (secret.yaml)

```bash
cp k8s/secret.yaml.example k8s/secret.yaml
```

Edit `k8s/secret.yaml` and fill in:

| Key | Where to find it |
|-----|-----------------|
| `AZURE_CLIENT_SECRET` | Azure Portal → App Registrations → your app → Certificates & Secrets |
| `SERVICENOW_PASSWORD` | Your SNOW admin password |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub → Settings → Developer Settings → Personal Access Tokens (`repo` + `workflow` scopes) |
| `AZURE_OPENAI_API_KEY` | Leave blank if using Azure AD auth (default). Set if `AZURE_OPENAI_USE_AZURE_AD=false` |

**Never commit `secret.yaml` — it is gitignored.**

---

## Step 8 — Set up the ServiceNow Business Rule

In your ServiceNow instance:

### Create the REST Message

1. Navigate to **System Web Services → Outbound → REST Messages → New**
2. Fill in:
   - **Name**: `ProvisioningAgent`
   - **Endpoint**: `http://<your-hostname>/api/provision`
   - **HTTP Method**: POST
3. Add header: `Content-Type: application/json`
4. Set body: `{"ticket_id": "${ticket_id}"}`

### Create the Business Rule

1. Navigate to **System Definition → Business Rules → New**
2. Fill in:
   - **Table**: `sc_req_item`
   - **When**: After Update
   - **Condition**: `current.approval == 'approved' && previous.approval != 'approved'`
3. Enable **Advanced** and paste this script:

```javascript
var rm = new sn_ws.RESTMessageV2('ProvisioningAgent', 'trigger');
rm.setStringParameterNoEscape('ticket_id', current.number);
rm.execute();
```

---

## Updating the app

```bash
az acr build --registry <your-acr> --image snow-terraform-agent:latest .
kubectl rollout restart deployment/snow-terraform-agent
```

---

## Teardown

**Standalone:**
```bash
kubectl delete -f k8s/
cd infra/aks-standalone && terraform destroy
```

**Existing cluster:**
```bash
kubectl delete -f k8s/ -n snow-terraform-agent
cd infra/aks-existing && terraform destroy
```

---

## Architecture

### POC (this repo)
```
SNOW → AKS (ingress → pod) → OpenAI + SNOW MCP + GitHub MCP
```

### Production additions recommended
| Component | Purpose |
|-----------|---------|
| Azure API Management | Rate limiting, auth, private endpoint gateway |
| Azure Service Bus | Async job queue so SNOW doesn't wait on agent completion |
| Azure Blob Storage | Job status persistence and result storage |
| Private Endpoint | Lock AKS to VNet, no public ingress |
| Workload Identity | Replace service principal client secret with pod-level managed identity |
| Application Insights | Observability and eval score logging |
