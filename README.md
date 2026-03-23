# ServiceNow → Terraform Provisioning Agent — AKS Accelerator

An AI agent that listens for approved ServiceNow tickets, generates Terraform HCL, opens a GitHub PR, and posts the PR link back to the ticket as a work note.

## How it works

```
SNOW Business Rule (on ticket approval)
  → POST /api/provision  {"ticket_id": "RITM0001234"}

Agent (running in AKS)
  → SNOW MCP:     read ticket details + validate approval + cost center
  → Azure MCP:    scan existing Azure inventory (Agent 1)
  → Azure OpenAI: generate Terraform HCL
  → Evaluators:   score HCL on security / compliance / quality (retry if fail)
  → GitHub MCP:   create branch → push files → open PR
  → SNOW MCP:     post PR link as work note
```

---

## Deployment modes

| | POC | Production |
|---|---|---|
| **Execution** | Synchronous — agent runs inline, SNOW waits | Async — SNOW gets 202 immediately, worker processes from queue |
| **Infra** | `infra/aks-standalone/` or `infra/aks-existing/` (flags off) | `infra/aks-existing/` (all flags on) |
| **Pods** | 1 API pod | 1 API pod + 1–5 worker pods (KEDA-scaled) |
| **Extra services** | None | Service Bus, Blob Storage, App Insights, APIM, Key Vault |

---

## Prerequisites

### Azure
- An Azure subscription
- An **Azure OpenAI resource** with a model deployed (gpt-4o or gpt-4.1 recommended)
- An **App Registration** (service principal) with a client secret

### ServiceNow
- A ServiceNow instance (dev PDI is fine for POC)
- Admin credentials

### GitHub
- A GitHub org or account
- A Terraform modules repo with examples (see [Terraform modules repo](#terraform-modules-repo))
- A **Personal Access Token** with `repo` + `workflow` scopes

### Local tools
- Azure CLI (`az`) — logged in (`az login`)
- Terraform >= 1.5
- kubectl
- Docker (or use `az acr build`)

---

## Path A — POC / Standalone (no existing cluster)

### Step 1 — Provision infrastructure

```bash
cd infra/aks-standalone
terraform init
terraform apply
```

Takes ~10 minutes. Note these outputs:

```
hostname            = "snow-agent.eastus2.cloudapp.azure.com"
acr_login_server    = "snowagentacr.azurecr.io"
aks_connect_command = "az aks get-credentials ..."
```

### Step 2 — Point kubectl at your cluster

```bash
az aks get-credentials --resource-group snow-terraform-agent-rg --name snow-agent-aks
```

### Step 3 — Build and push the container image

```bash
az acr build --registry snowagentacr --image snow-terraform-agent:latest .
```

### Step 4 — Configure the app

Fill in `k8s/configmap.yaml` (see [Config reference](#config-reference)) and create `k8s/secret.yaml`:

```bash
cp k8s/secret.yaml.example k8s/secret.yaml
# edit k8s/secret.yaml
```

### Step 5 — Stamp the ACR and hostname into the manifests

```bash
sed -i 's|<ACR_LOGIN_SERVER>|snowagentacr.azurecr.io|g' k8s/deployment.yaml k8s/worker-deployment.yaml
sed -i 's|<DNS_LABEL>.eastus2.cloudapp.azure.com|snow-agent.eastus2.cloudapp.azure.com|g' k8s/ingress.yaml
```

### Step 6 — Deploy

```bash
kubectl apply -f k8s/deployment.yaml -f k8s/service.yaml -f k8s/ingress.yaml \
              -f k8s/configmap.yaml -f k8s/secret.yaml
kubectl get pods -w
```

> Do not apply `worker-deployment.yaml`, `keda-scaledobject.yaml`, or `keda-auth.yaml` for the POC — those are production-only.

---

## Path B — POC / Existing cluster

### Step 1 — Point kubectl at your cluster

```bash
az aks get-credentials --resource-group <your-rg> --name <your-cluster>
kubectl config get-contexts   # note the context name
```

### Step 2 — Provision namespace

```bash
cd infra/aks-existing
terraform init
terraform apply \
  -var="subscription_id=<your-sub-id>" \
  -var="resource_group_name=<your-rg>" \
  -var="kube_context=<your-context>"
```

### Step 3 — Build and push to your ACR

```bash
az acr build --registry <your-acr-name> --image snow-terraform-agent:latest .
```

### Step 4 — Configure and deploy

Fill in `k8s/configmap.yaml`, create `k8s/secret.yaml`, stamp the ACR name:

```bash
sed -i 's|<ACR_LOGIN_SERVER>|<your-acr>.azurecr.io|g' k8s/deployment.yaml k8s/worker-deployment.yaml
```

Review `k8s/ingress.yaml` — see comments at the top for internal nginx, APIM, and private endpoint patterns.

```bash
kubectl apply -f k8s/deployment.yaml -f k8s/service.yaml -f k8s/ingress.yaml \
              -f k8s/configmap.yaml -f k8s/secret.yaml \
              -n snow-terraform-agent
kubectl get pods -n snow-terraform-agent -w
```

---

## Path C — Production (existing cluster, full async stack)

### Step 1 — Install KEDA on the cluster

```bash
helm repo add kedacore https://charts.kedacore.io
helm repo update
helm install keda kedacore/keda --namespace keda --create-namespace
```

### Step 2 — Provision all production resources

```bash
cd infra/aks-existing
terraform init
terraform apply \
  -var="subscription_id=<your-sub-id>" \
  -var="resource_group_name=<your-rg>" \
  -var="kube_context=<your-context>" \
  -var="create_service_bus=true" \
  -var="service_bus_name=snow-agent-asb" \
  -var="create_blob_storage=true" \
  -var="storage_account_name=snowagentstate" \
  -var="create_app_insights=true" \
  -var="create_apim=true" \
  -var="apim_name=snow-agent-apim" \
  -var="apim_publisher_name=<your-org>" \
  -var="apim_publisher_email=<your-email>" \
  -var="aks_ingress_url=http://<your-aks-hostname>" \
  -var="create_key_vault=true" \
  -var="key_vault_name=snow-agent-kv" \
  -var="tenant_id=<your-tenant-id>" \
  -var="pod_identity_object_id=<your-managed-identity-object-id>"
```

Note the outputs:

```
service_bus_hostname            = "snow-agent-asb.servicebus.windows.net"
storage_account_name            = "snowagentstate"
app_insights_connection_string  = (sensitive — run: terraform output app_insights_connection_string)
apim_gateway_url                = "https://snow-agent-apim.azure-api.net"
key_vault_uri                   = "https://snow-agent-kv.vault.azure.net/"
```

### Step 3 — Build and push image

```bash
az acr build --registry <your-acr-name> --image snow-terraform-agent:latest .
```

### Step 4 — Fill in configmap.yaml

Copy the Terraform outputs into `k8s/configmap.yaml`:

```yaml
AZURE_SERVICE_BUS_HOSTNAME: "snow-agent-asb.servicebus.windows.net"
AZURE_STORAGE_ACCOUNT_NAME: "snowagentstate"
APPLICATIONINSIGHTS_CONNECTION_STRING: "<from terraform output>"
```

### Step 5 — Create secret.yaml

```bash
cp k8s/secret.yaml.example k8s/secret.yaml
# edit k8s/secret.yaml
```

> In full production with Key Vault + Workload Identity, `secret.yaml` is replaced by CSI driver secret injection. See Key Vault notes in `infra/aks-existing/main.tf`.

### Step 6 — Stamp ACR into manifests

```bash
sed -i 's|<ACR_LOGIN_SERVER>|<your-acr>.azurecr.io|g' k8s/deployment.yaml k8s/worker-deployment.yaml
```

### Step 7 — Deploy all manifests

```bash
# Core
kubectl apply -f k8s/configmap.yaml -f k8s/secret.yaml \
              -f k8s/deployment.yaml -f k8s/service.yaml -f k8s/ingress.yaml \
              -n snow-terraform-agent

# Worker + KEDA (edit keda-scaledobject.yaml first — replace <ASB_NAMESPACE> with your ASB namespace name)
kubectl apply -f k8s/worker-deployment.yaml \
              -f k8s/keda-auth.yaml \
              -f k8s/keda-scaledobject.yaml \
              -n snow-terraform-agent

kubectl get pods -n snow-terraform-agent -w
```

### Step 8 — Set up ServiceNow Business Rule

Use the APIM gateway URL (not the AKS ingress URL directly):

**Create the REST Message:**
1. Navigate to **System Web Services → Outbound → REST Messages → New**
2. Fill in:
   - **Name**: `ProvisioningAgent`
   - **Endpoint**: `https://snow-agent-apim.azure-api.net/api/provision`
   - **HTTP Method**: POST
3. Add header: `Content-Type: application/json`
4. Set body: `{"ticket_id": "${ticket_id}"}`

**Create the Business Rule:**
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

## Config reference

### k8s/configmap.yaml — non-sensitive values

| Key | Where to find it |
|-----|-----------------|
| `AZURE_OPENAI_ENDPOINT` | Azure Portal → OpenAI resource → Keys and Endpoint |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Azure Portal → OpenAI resource → Model deployments |
| `AZURE_OPENAI_MODEL_NAME` | Same as deployment name |
| `SERVICENOW_INSTANCE_URL` | e.g. `https://dev123456.service-now.com` |
| `SERVICENOW_USERNAME` | SNOW admin username |
| `GITHUB_ORG` | GitHub org or username |
| `GITHUB_TERRAFORM_REPO` | Terraform modules repo name |
| `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |
| `AZURE_CLIENT_ID` | Azure Portal → App Registrations → Application (client) ID |
| `AZURE_TENANT_ID` | Azure Portal → App Registrations → Directory (tenant) ID |
| `AZURE_SERVICE_BUS_HOSTNAME` | Terraform output: `service_bus_hostname` (production only) |
| `AZURE_STORAGE_ACCOUNT_NAME` | Terraform output: `storage_account_name` (production only) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Terraform output: `app_insights_connection_string` (production only) |

### k8s/secret.yaml — sensitive credentials

| Key | Where to find it |
|-----|-----------------|
| `AZURE_CLIENT_SECRET` | Azure Portal → App Registrations → Certificates & Secrets |
| `SERVICENOW_PASSWORD` | SNOW admin password |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub → Settings → Developer Settings → PATs (`repo` + `workflow`) |
| `AZURE_OPENAI_API_KEY` | Leave blank if `AZURE_OPENAI_USE_AZURE_AD=true` (default) |

**Never commit `secret.yaml` — it is gitignored.**

---

## Terraform modules repo

The agent fetches an example Terraform template from your GitHub repo at:

```
https://github.com/<GITHUB_ORG>/<GITHUB_TERRAFORM_REPO>/contents/examples/storage-account-example/main.tf
```

This template is injected into the LLM system prompt as the pattern for HCL generation. The better your example modules, the better the generated output.

You can fork the reference repo at [`natesanshreyas/terraform-modules-demo`](https://github.com/natesanshreyas/terraform-modules-demo) which includes modules for `resource-group`, `storage-account`, and `openai`.

---

## Ingress options

`k8s/ingress.yaml` defaults to public nginx + Azure Load Balancer. Read the comments at the top of that file for:

- **Internal nginx** — private VNet only
- **APIM in front** — delete ingress.yaml, configure APIM to route to ClusterIP Service
- **Private endpoint** — delete ingress.yaml, use org's internal gateway

---

## Updating the app

```bash
az acr build --registry <your-acr> --image snow-terraform-agent:latest .
kubectl rollout restart deployment/snow-terraform-agent
kubectl rollout restart deployment/snow-terraform-agent-worker   # if running async mode
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

### POC
```
SNOW → AKS (ingress → API pod) → OpenAI + SNOW MCP + GitHub MCP
```

### Production
```
SNOW → APIM → AKS ingress → API pod → Service Bus
                                           ↓
                              Worker pod (KEDA-scaled, 0–5)
                                           ↓
                              OpenAI + SNOW MCP + GitHub MCP + Azure MCP
                                           ↓
                              Blob Storage (run state) + App Insights (traces)
```

### Azure services used

| Service | POC | Production |
|---------|-----|------------|
| AKS | ✅ | ✅ |
| ACR | ✅ | ✅ |
| Azure Load Balancer | ✅ | ✅ |
| Azure OpenAI | ✅ | ✅ |
| Azure Active Directory | ✅ | ✅ |
| Azure API Management | — | ✅ |
| Azure Service Bus | — | ✅ |
| Azure Blob Storage | — | ✅ |
| Application Insights | — | ✅ |
| Log Analytics Workspace | — | ✅ |
| Azure Key Vault | — | ✅ |
