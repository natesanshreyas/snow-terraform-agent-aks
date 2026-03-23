# Pattern: Azure Key Vault

Instead of storing credentials in `k8s/secret.yaml`, the pod reads them directly from Key Vault at startup via the AKS Secrets Store CSI driver. `secret.yaml` is not needed.

## What you need

- An Azure Key Vault with secrets stored in it
- AKS Secrets Store CSI driver enabled on your cluster
- A pod managed identity (Workload Identity) with Key Vault Secrets User role

## If you need to create a Key Vault

```bash
cd infra/aks-existing
terraform apply \
  -var="subscription_id=<your-sub-id>" \
  -var="resource_group_name=<your-rg>" \
  -var="kube_context=<your-context>" \
  -var="create_key_vault=true" \
  -var="key_vault_name=<globally-unique-name>" \
  -var="tenant_id=<your-tenant-id>" \
  -var="pod_identity_object_id=<your-managed-identity-object-id>"
```

## If you already have a Key Vault

Grant the pod identity read access:

```bash
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee <pod-managed-identity-client-id> \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<kv-name>
```

## Step 1 — Store secrets in Key Vault

```bash
az keyvault secret set --vault-name <kv-name> --name SERVICENOW-PASSWORD --value <value>
az keyvault secret set --vault-name <kv-name> --name GITHUB-PAT --value <value>
az keyvault secret set --vault-name <kv-name> --name AZURE-CLIENT-SECRET --value <value>
az keyvault secret set --vault-name <kv-name> --name AZURE-OPENAI-API-KEY --value <value>
```

## Step 2 — Enable CSI driver on your cluster

```bash
az aks enable-addons \
  --resource-group <rg> \
  --name <cluster-name> \
  --addons azure-keyvault-secrets-provider
```

## Step 3 — Apply the SecretProviderClass

Create `k8s/secret-provider-class.yaml` with your values and apply it:

```yaml
apiVersion: secrets-store.csi.x-k8s.io/v1
kind: SecretProviderClass
metadata:
  name: snow-terraform-agent-kv
spec:
  provider: azure
  parameters:
    usePodIdentity: "false"
    clientID: "<MANAGED_IDENTITY_CLIENT_ID>"
    keyvaultName: "<KEY_VAULT_NAME>"
    tenantId: "<TENANT_ID>"
    objects: |
      array:
        - |
          objectName: SERVICENOW-PASSWORD
          objectType: secret
        - |
          objectName: GITHUB-PAT
          objectType: secret
        - |
          objectName: AZURE-CLIENT-SECRET
          objectType: secret
        - |
          objectName: AZURE-OPENAI-API-KEY
          objectType: secret
  secretObjects:
    - secretName: snow-terraform-agent-secrets
      type: Opaque
      data:
        - objectName: SERVICENOW-PASSWORD
          key: SERVICENOW_PASSWORD
        - objectName: GITHUB-PAT
          key: GITHUB_PERSONAL_ACCESS_TOKEN
        - objectName: AZURE-CLIENT-SECRET
          key: AZURE_CLIENT_SECRET
        - objectName: AZURE-OPENAI-API-KEY
          key: AZURE_OPENAI_API_KEY
```

```bash
kubectl apply -f k8s/secret-provider-class.yaml -n <namespace>
```

## Step 4 — Mount in deployment.yaml

Add a volume to `k8s/deployment.yaml` so the CSI driver syncs the secrets:

```yaml
spec:
  volumes:
    - name: secrets-store
      csi:
        driver: secrets-store.csi.k8s.io
        readOnly: true
        volumeAttributes:
          secretProviderClass: snow-terraform-agent-kv
  containers:
    - name: snow-terraform-agent
      volumeMounts:
        - name: secrets-store
          mountPath: "/mnt/secrets"
          readOnly: true
```

The CSI driver creates the Kubernetes secret `snow-terraform-agent-secrets` automatically. The existing `secretRef` in `deployment.yaml` picks it up — no other code changes needed. Do not apply `secret.yaml`.
