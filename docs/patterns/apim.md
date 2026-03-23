# Pattern: Azure API Management

APIM sits in front of the AKS ingress and provides rate limiting and IP allowlisting so only your ServiceNow instance can call the agent endpoint.

## What you need

- An APIM instance (any tier — Consumption is cheapest if you don't already have one)
- The AKS ingress URL (from `infra/aks-standalone/` output or your existing ingress)

## If you need to create an APIM instance

```bash
cd infra/aks-existing
terraform apply \
  -var="subscription_id=<your-sub-id>" \
  -var="resource_group_name=<your-rg>" \
  -var="kube_context=<your-context>" \
  -var="create_apim=true" \
  -var="apim_name=<globally-unique-name>" \
  -var="apim_publisher_name=<your-org>" \
  -var="apim_publisher_email=<your-email>" \
  -var="aks_ingress_url=http://<your-aks-ingress-hostname>"
```

Note the output:
```
apim_gateway_url = "https://<your-apim>.azure-api.net"
```

## If you already have an APIM instance

Add the provisioning API manually in the Azure Portal or via CLI:

```bash
# Create the API
az apim api create \
  --resource-group <rg> \
  --service-name <apim-name> \
  --api-id provisioning-api \
  --display-name "Provisioning API" \
  --path "" \
  --service-url "http://<your-aks-ingress-hostname>"

# Add the POST /api/provision operation
az apim api operation create \
  --resource-group <rg> \
  --service-name <apim-name> \
  --api-id provisioning-api \
  --operation-id trigger-provision \
  --display-name "Trigger Provisioning" \
  --method POST \
  --url-template "/api/provision"
```

Then add a rate limiting policy on the API in the Portal:
```xml
<inbound>
  <rate-limit calls="30" renewal-period="60" />
  <base />
</inbound>
```

## Update the ServiceNow Business Rule

Change the REST Message endpoint from the AKS ingress URL to the APIM gateway URL:

```
https://<your-apim>.azure-api.net/api/provision
```

No code or manifest changes needed — APIM proxies transparently to the AKS ingress.

## Optional: IP allowlist

To restrict calls to your SNOW instance IP only, add this to the inbound policy:

```xml
<inbound>
  <ip-filter action="allow">
    <address-range from="<snow-ip>" to="<snow-ip>" />
  </ip-filter>
  <rate-limit calls="30" renewal-period="60" />
  <base />
</inbound>
```
