# Pattern: Application Insights

Enables distributed tracing for every agent run — each MCP tool call, LLM call, and provisioning result is captured as a span. Evaluator scores are also logged to Azure AI Foundry if configured.

## What you need

- An Application Insights resource (workspace-based)
- Its connection string

## If you need to create one

```bash
cd infra/aks-existing
terraform apply \
  -var="subscription_id=<your-sub-id>" \
  -var="resource_group_name=<your-rg>" \
  -var="kube_context=<your-context>" \
  -var="create_app_insights=true"

# Get the connection string
terraform output app_insights_connection_string
```

## If you already have one

Get the connection string from the Azure Portal:

```
App Insights resource → Overview → Connection String
```

Or via CLI:
```bash
az monitor app-insights component show \
  --resource-group <rg> \
  --app <app-insights-name> \
  --query connectionString -o tsv
```

## Enable it

Set the connection string in `k8s/configmap.yaml`:

```yaml
APPLICATIONINSIGHTS_CONNECTION_STRING: "InstrumentationKey=...;IngestionEndpoint=..."
```

That's it. `telemetry.py` reads this env var and configures Azure Monitor automatically when the pod starts. No code changes needed.

## What gets traced

- Every `POST /api/provision` request (end-to-end duration)
- Every MCP tool call (tool name, server, duration, success/fail)
- Every Azure OpenAI call (prompt tokens, completion tokens, duration)
- Every provisioning run result (ticket ID, PR URL, eval scores)

## Foundry evaluator logging (optional)

If you also have an Azure AI Foundry project, set:

```yaml
AZURE_AI_PROJECT_CONNECTION_STRING: "<your-foundry-project-connection-string>"
```

Evaluator scores (security / compliance / quality) for each Terraform generation will appear in the Foundry Evaluations tab.
