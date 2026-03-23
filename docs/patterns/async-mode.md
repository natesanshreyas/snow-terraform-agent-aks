# Pattern: Async Mode (Service Bus + Blob Storage + KEDA)

By default the agent runs synchronously — SNOW fires the webhook and waits for the full response. For production, async mode returns a 202 immediately and a worker pod processes the job from a queue.

## What you need

- An Azure Service Bus namespace with a queue named `provisioning-queue`
- An Azure Storage Account with a container named `runs`
- KEDA installed on your AKS cluster

## If you need to create these resources

```bash
cd infra/aks-existing
terraform apply \
  -var="subscription_id=<your-sub-id>" \
  -var="resource_group_name=<your-rg>" \
  -var="kube_context=<your-context>" \
  -var="create_service_bus=true" \
  -var="service_bus_name=<globally-unique-name>" \
  -var="create_blob_storage=true" \
  -var="storage_account_name=<globally-unique-name>"
```

Note the outputs:
```
service_bus_hostname = "your-asb.servicebus.windows.net"
storage_account_name = "yourstorageaccount"
```

## If you already have these resources

Skip Terraform. Just note your existing:
- Service Bus hostname: `<name>.servicebus.windows.net`
- Storage account name

## Step 1 — Update configmap.yaml

```yaml
AZURE_SERVICE_BUS_HOSTNAME: "<your-asb>.servicebus.windows.net"
AZURE_STORAGE_ACCOUNT_NAME: "<your-storage-account>"
```

That's all the API pod needs. It will automatically switch to async mode when `AZURE_SERVICE_BUS_HOSTNAME` is set.

## Step 2 — Grant the pod identity access

```bash
# Service Bus Data Owner (send + receive)
az role assignment create \
  --role "Azure Service Bus Data Owner" \
  --assignee <pod-managed-identity-client-id> \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ServiceBus/namespaces/<asb-name>

# Storage Blob Data Contributor (read + write run state)
az role assignment create \
  --role "Storage Blob Data Contributor" \
  --assignee <pod-managed-identity-client-id> \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<storage-name>
```

## Step 3 — Install KEDA (if not already on cluster)

```bash
helm repo add kedacore https://charts.kedacore.io
helm repo update
helm install keda kedacore/keda --namespace keda --create-namespace
```

## Step 4 — Edit keda-scaledobject.yaml

Replace `<ASB_NAMESPACE>` with your Service Bus namespace name (just the name, not the full hostname):

```yaml
triggers:
  - type: azure-servicebus
    metadata:
      queueName: provisioning-queue
      namespace: <your-asb-name>      # ← edit this
      messageCount: "1"
```

## Step 5 — Apply worker manifests

```bash
kubectl apply -f k8s/worker-deployment.yaml -n <namespace>
kubectl apply -f k8s/keda-auth.yaml -n <namespace>
kubectl apply -f k8s/keda-scaledobject.yaml -n <namespace>
```

## Verify

```bash
# Should show API pod + worker pod
kubectl get pods -n <namespace>

# KEDA should be watching the queue
kubectl get scaledobject -n <namespace>
```

Send a test ticket through SNOW. The API pod returns 202 immediately. The worker pod picks up the job and processes it. Poll the status endpoint:

```
GET /api/provision/<run_id>/status
```
