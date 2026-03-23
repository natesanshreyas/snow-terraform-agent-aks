"""Agent 1: Azure inventory scanner using iterative MCP tool calls.

Runs before the main provisioning agent. The LLM iteratively queries the Azure
environment (via Azure MCP CLI + ARG) to determine:
  - Does the requested resource already exist?
  - What shared infrastructure (VNets, Key Vaults, SQL servers, etc.) is available
    and should be REFERENCED rather than recreated?
  - What naming conventions and standard tags are in use?

Produces an InventoryContext that Agent 2 uses to generate targeted Terraform
that avoids duplicating expensive shared infrastructure.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests as _requests

from .openai_client import OpenAISettings, chat_completion

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class InventoryContext:
    resource_group: str
    rg_exists: bool
    existing_resources: List[Dict]           # resources found in the target RG
    naming_prefix: str                       # inferred e.g. "cortex-"
    standard_tags: Dict[str, str]            # tags in common use
    notes: str                               # plain-English summary for Agent 2
    requested_resource_exists: bool = False  # is the specific requested resource already there?
    shared_infrastructure: Dict[str, str] = field(default_factory=dict)
    # e.g. {"key_vault": "cortex-kv", "vnet": "cortex-vnet", "app_service_plan": "asp-cortex"}


# ---------------------------------------------------------------------------
# ARM token cache (same pattern as azure_mcp_assistant.py)
# ---------------------------------------------------------------------------

_ARM_TOKEN_CACHE: Dict[str, Any] = {}


def _get_arm_token() -> str:
    if _ARM_TOKEN_CACHE.get("token") and _ARM_TOKEN_CACHE.get("expires_at", 0) > time.time() + 60:
        return _ARM_TOKEN_CACHE["token"]
    from azure.identity import DefaultAzureCredential
    token = DefaultAzureCredential().get_token("https://management.azure.com/.default")
    _ARM_TOKEN_CACHE["token"] = token.token
    _ARM_TOKEN_CACHE["expires_at"] = token.expires_on
    return token.token


def _run_arg_query(kql: str, subscription_id: str) -> Dict[str, Any]:
    """Execute a KQL query against Azure Resource Graph REST API."""
    token = _get_arm_token()
    url = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body: Dict[str, Any] = {"query": kql}
    if subscription_id:
        body["subscriptions"] = [subscription_id]

    resp = _requests.post(
        url,
        headers=headers,
        params={"api-version": "2021-03-01"},
        json=body,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"ARG query failed ({resp.status_code}): {resp.text[:400]}")

    data = resp.json()
    results = data.get("data", [])
    if not isinstance(results, list):
        results = []
    return {"results": results, "totalRecords": data.get("totalRecords", len(results))}


# ---------------------------------------------------------------------------
# Azure MCP CLI helpers (inlined from azure_mcp_assistant.py pattern)
# ---------------------------------------------------------------------------

_WRITE_KEYWORDS: set = {
    "create", "delete", "update", "put", "patch", "write",
    "set", "remove", "add", "deploy", "move", "restore",
    "start", "stop", "restart", "reset", "assign", "unassign",
    "attach", "detach", "enable", "disable", "cancel", "purge",
    "import", "export",
}

_ARG_VIRTUAL_COMMAND: Dict[str, Any] = {
    "command": "arg query",
    "description": (
        "Azure Resource Graph KQL query — use for app-centric, tag-based, cross-resource-type, "
        "and resource-group-scoped queries. Supports case-insensitive =~ operator. "
        "Table: 'Resources'. "
        "Examples: "
        "\"Resources | where tags['app'] =~ 'cortex' | project name, type, resourceGroup, location, tags\" "
        "\"Resources | where resourceGroup =~ 'my-rg' | project name, type, location, tags\" "
        "\"Resources | where type =~ 'microsoft.keyvault/vaults' | project name, resourceGroup, location\" "
        "\"ResourceContainers | where type =~ 'microsoft.resources/subscriptions/resourcegroups' "
        "| where name =~ 'rg-cortex-prod' | project name, location\""
    ),
    "options": ["--query"],
}


def _is_readonly(name: str) -> bool:
    parts = set(re.split(r"[_\-\s/]+", name.lower()))
    return not (parts & _WRITE_KEYWORDS)


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        start = match.start()
        try:
            parsed, _ = decoder.raw_decode(text[start:])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    raise ValueError("No valid JSON object found in LLM output")


def _format_mcp_result(result: Dict[str, Any], max_chars: int = 8000) -> str:
    content = result.get("content")
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str):
                chunks.append(item["text"])
            elif "json" in item:
                chunks.append(json.dumps(item["json"], ensure_ascii=False))
        output = "\n".join(chunks).strip()
    else:
        output = json.dumps(result, ensure_ascii=False)
    return output[:max_chars] + ("\n...[truncated]" if len(output) > max_chars else "")


def _run_azmcp_cli(
    command_base: str,
    subcommand: str,
    arguments: Optional[Dict[str, Any]] = None,
    timeout_seconds: int = 60,
) -> Dict[str, Any]:
    argv = shlex.split(command_base)
    if not argv:
        raise RuntimeError("AZURE_MCP_SERVER_COMMAND is empty")

    # Strip "server start" suffix — CLI subcommands don't use it
    if "server" in argv:
        argv = argv[:argv.index("server")]

    argv.extend(shlex.split(subcommand))

    for key, value in (arguments or {}).items():
        opt = str(key).strip()
        if not opt.startswith("--"):
            opt = f"--{opt}"
        if isinstance(value, bool):
            if value:
                argv.append(opt)
            continue
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                argv.extend([opt, str(item)])
            continue
        argv.extend([opt, str(value)])

    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_seconds)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")

    # Try to extract JSON from stdout first, then combined output
    for source in [proc.stdout or "", output]:
        try:
            return _extract_first_json_object(source)
        except ValueError:
            continue

    if proc.returncode != 0:
        raise RuntimeError(f"Azure MCP CLI failed ({proc.returncode}): {output[:800]}")
    raise RuntimeError(f"Azure MCP CLI returned no JSON: {output[:400]}")


def _build_cli_manifest(tools: List[Dict[str, Any]], question: str) -> List[Dict[str, Any]]:
    """Score and filter tools by relevance to the question; always include core commands."""
    keywords = [w.lower() for w in re.findall(r"[a-zA-Z0-9_\-]+", question) if len(w) > 2]

    scored: List[tuple] = []
    for tool in tools:
        command = str(tool.get("command", "")).strip()
        if not command or not _is_readonly(command):
            continue

        description = str(tool.get("description", "")).strip()
        text = f"{command} {description}".lower()
        score = sum(3 for kw in keywords if kw in text)
        if command in {"subscription list", "group list"}:
            score += 2

        options = [
            opt.get("name")
            for opt in (tool.get("option") or [])
            if isinstance(opt, dict) and isinstance(opt.get("name"), str)
        ]
        scored.append((score, {"command": command, "description": description[:180], "options": options[:20]}))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [entry for _, entry in scored[:30]]

    # Always include core discovery commands
    always_include = {
        "subscription list", "group list",
        "storage account list", "keyvault list",
        "network vnet list", "appservice plan list",
        "aks cluster list", "vm list",
        "postgres flexible server list", "cosmos list",
        "monitor workspace list",
    }
    current = {e["command"] for e in selected}
    for _, entry in scored:
        if entry["command"] in always_include and entry["command"] not in current:
            selected.append(entry)
            current.add(entry["command"])

    dedup: Dict[str, Dict] = {e["command"]: e for e in selected}
    return [_ARG_VIRTUAL_COMMAND] + [e for e in dedup.values() if e["command"] != "arg query"]


# ---------------------------------------------------------------------------
# Parse LLM final answer into InventoryContext
# ---------------------------------------------------------------------------


def _parse_inventory_context(data: Dict[str, Any]) -> InventoryContext:
    existing = data.get("existing_resources", [])
    if not isinstance(existing, list):
        existing = []

    shared = data.get("shared_infrastructure", {})
    if not isinstance(shared, dict):
        shared = {}

    standard_tags = data.get("standard_tags", {})
    if not isinstance(standard_tags, dict):
        standard_tags = {}

    return InventoryContext(
        resource_group=str(data.get("resource_group", "")),
        rg_exists=bool(data.get("rg_exists", False)),
        existing_resources=existing,
        naming_prefix=str(data.get("naming_prefix", "")),
        standard_tags=standard_tags,
        notes=str(data.get("notes", "")),
        requested_resource_exists=bool(data.get("requested_resource_exists", False)),
        shared_infrastructure=shared,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_SCAN_QUESTION_TEMPLATE = """\
ServiceNow provisioning request:
{ticket_text}

Scan the Azure subscription to answer:
1. What resource group would this belong to — does it already exist?
2. Does the specific requested resource (storage account, VM, app service, etc.) already exist?
3. What SHARED infrastructure is available and should be REFERENCED rather than recreated:
   VNets, Key Vaults, SQL Servers, App Service Plans, Log Analytics workspaces,
   AKS clusters, ACR registries, Service Bus namespaces, etc.
4. What naming conventions and standard tags are in use?

Return your final answer as a JSON object with this exact schema:
{{
  "resource_group": "<name or empty string>",
  "rg_exists": true/false,
  "requested_resource_exists": true/false,
  "existing_resources": [{{"name": "...", "type": "...", "location": "..."}}],
  "naming_prefix": "<e.g. cortex- or empty>",
  "standard_tags": {{"env": "prod", "app": "cortex"}},
  "shared_infrastructure": {{"key_vault": "...", "vnet": "...", "app_service_plan": "..."}},
  "notes": "<plain-English summary: what exists, what to skip creating, what to reference>"
}}"""

_SYSTEM_PROMPT = """\
You are an Azure inventory scanner — READ-ONLY mode.
Your job is to scan the Azure environment BEFORE Terraform is generated, so the
provisioning agent knows what already exists and what shared infrastructure to reference.

Always return strict JSON — exactly one of:
  {"action":"cli_call","command":"<name>","arguments":{},"reason":"..."}
  {"action":"final","answer":{...the schema object...}}

Rules:
(1) Read-only only — never call commands that create, update, or delete.
(2) Copy 'command' VERBATIM from the manifest — never invent names.
(3) --subscription is injected automatically — do NOT include it in arguments.
(4) PREFER 'arg query' for: existence checks, tag-based searches, cross-type scans,
    resource-group-scoped inventory. Use =~ for case-insensitive KQL matching.
(5) Use specific commands (keyvault list, network vnet list, etc.) only when you need
    details ARG does not expose.
(6) Final answer MUST be the JSON schema object — not prose, not an array.
(7) Do not return final until you have checked for the requested resource AND
    surveyed shared infrastructure relevant to the request.\
"""


async def scan_azure_inventory(
    ticket_text: str,
    subscription_id: str,
    openai_settings: OpenAISettings,
    max_iterations: int = 8,
) -> InventoryContext:
    """Agent 1: iterative Azure MCP scan to build provisioning context for Agent 2.

    The LLM drives the scan — it decides which MCP tools/ARG queries to run
    based on the ticket, iteratively building a picture of the environment,
    then returns a structured InventoryContext.
    """
    azure_mcp_command = os.getenv("AZURE_MCP_SERVER_COMMAND", "").strip()
    if not azure_mcp_command:
        raise RuntimeError("AZURE_MCP_SERVER_COMMAND not set — cannot run Agent 1 scan")

    effective_sub = subscription_id or os.getenv("AZURE_SUBSCRIPTION_ID", "").strip()
    question = _SCAN_QUESTION_TEMPLATE.format(ticket_text=ticket_text[:1500])

    # Build tool manifest from Azure MCP CLI
    tools_payload = _run_azmcp_cli(azure_mcp_command, "tools list", timeout_seconds=90)
    tool_entries = tools_payload.get("results")
    if not isinstance(tool_entries, list) or not tool_entries:
        raise RuntimeError("Azure MCP tools list returned no tools")

    manifest_entries = _build_cli_manifest(tool_entries, question)
    allowed_commands = {entry["command"] for entry in manifest_entries}
    command_options: Dict[str, List[str]] = {
        entry["command"]: entry.get("options", []) for entry in manifest_entries
    }
    manifest = json.dumps(manifest_entries, ensure_ascii=False)

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"{question}\n\nAvailable Azure MCP commands:\n{manifest}",
        },
    ]

    for i in range(1, max_iterations + 1):
        decision_raw = await asyncio.to_thread(chat_completion, openai_settings, messages, max_tokens=4000)
        logger.debug("Agent 1 iteration %d: %s", i, decision_raw[:200])

        try:
            decision = _extract_first_json_object(decision_raw)
        except ValueError:
            messages.append({"role": "assistant", "content": decision_raw})
            messages.append({"role": "user", "content": "Response was not valid JSON. Return the correct JSON format."})
            continue

        action = decision.get("action")

        # ── Final answer ──────────────────────────────────────────────────
        if action == "final":
            answer = decision.get("answer")
            # LLM sometimes returns answer as a JSON string — unwrap it
            if isinstance(answer, str):
                try:
                    answer = _extract_first_json_object(answer)
                except ValueError:
                    pass
            if isinstance(answer, dict):
                ctx = _parse_inventory_context(answer)
                logger.info(
                    "Agent 1 complete: rg=%s exists=%s requested_exists=%s shared=%s",
                    ctx.resource_group,
                    ctx.rg_exists,
                    ctx.requested_resource_exists,
                    list(ctx.shared_infrastructure.keys()),
                )
                return ctx
            messages.append({"role": "assistant", "content": decision_raw})
            messages.append({"role": "user", "content": "Final answer must be a JSON object matching the schema. Try again."})
            continue

        # ── Tool call ─────────────────────────────────────────────────────
        if action != "cli_call":
            messages.append({"role": "assistant", "content": decision_raw})
            messages.append({
                "role": "user",
                "content": f'Invalid action "{action}". Must be exactly "cli_call" or "final".',
            })
            continue

        command = str(decision.get("command", "")).strip()
        if command not in allowed_commands:
            messages.append({"role": "assistant", "content": decision_raw})
            messages.append({
                "role": "user",
                "content": f'Command "{command}" is not in the manifest. Use a command exactly as listed.',
            })
            continue

        args = decision.get("arguments", {})
        if not isinstance(args, dict):
            args = {}

        # Execute: ARG query or Azure MCP CLI call
        if command == "arg query":
            kql = str(args.get("--query", "")).strip()
            if not kql:
                messages.append({"role": "assistant", "content": decision_raw})
                messages.append({"role": "user", "content": "arg query requires --query with a KQL expression."})
                continue
            result = _run_arg_query(kql, effective_sub)
        else:
            if effective_sub and "--subscription" not in args and "--subscription" in command_options.get(command, []):
                args["--subscription"] = effective_sub
            result = _run_azmcp_cli(azure_mcp_command, command, arguments=args, timeout_seconds=90)

        preview = _format_mcp_result({"content": [{"json": result}]})
        logger.debug("Agent 1 tool result (%s): %s", command, preview[:300])

        messages.append({
            "role": "assistant",
            "content": json.dumps({
                "action": "cli_call",
                "command": command,
                "arguments": args,
                "reason": decision.get("reason", ""),
            }),
        })
        messages.append({
            "role": "user",
            "content": (
                f"Azure MCP result ({command}):\n{preview}\n"
                "Continue scanning if needed, or return the final JSON answer."
            ),
        })

    raise RuntimeError(f"Agent 1: no final answer after {max_iterations} iterations")
