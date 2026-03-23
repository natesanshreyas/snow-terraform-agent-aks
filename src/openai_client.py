from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

load_dotenv()


class OpenAIClientError(Exception):
    pass


@dataclass
class OpenAISettings:
    endpoint: str
    deployment_name: str
    api_version: str
    model_name: str
    api_key: str
    use_azure_ad: bool


_token_cache: Dict[str, Any] = {}


def load_openai_settings() -> OpenAISettings:
    return OpenAISettings(
        endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/"),
        deployment_name=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", ""),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        model_name=os.getenv("AZURE_OPENAI_MODEL_NAME", "gpt-4.1"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        use_azure_ad=os.getenv("AZURE_OPENAI_USE_AZURE_AD", "true").lower() == "true",
    )


def _get_azure_ad_token() -> str:
    if _token_cache.get("token") and _token_cache.get("expires_at", 0) > time.time() + 60:
        return _token_cache["token"]

    client_id = os.getenv("AZURE_CLIENT_ID", "")
    tenant_id = os.getenv("AZURE_TENANT_ID", "")
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "")

    if client_id and tenant_id and client_secret:
        from azure.identity import ClientSecretCredential
        cred = ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    else:
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()

    token = cred.get_token("https://cognitiveservices.azure.com/.default")
    _token_cache["token"] = token.token
    _token_cache["expires_at"] = token.expires_on
    return token.token


def chat_completion(
    settings: OpenAISettings,
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 2000,
) -> str:
    if not settings.endpoint or not settings.deployment_name:
        raise OpenAIClientError("Missing AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_DEPLOYMENT_NAME")
    if not settings.use_azure_ad and not settings.api_key:
        raise OpenAIClientError("Missing AZURE_OPENAI_API_KEY when AZURE_OPENAI_USE_AZURE_AD=false")

    url = f"{settings.endpoint}/openai/deployments/{settings.deployment_name}/chat/completions"
    params = {"api-version": settings.api_version}

    headers = {"Content-Type": "application/json"}
    if settings.use_azure_ad:
        headers["Authorization"] = f"Bearer {_get_azure_ad_token()}"
    else:
        headers["api-key"] = settings.api_key

    body: Dict[str, Any] = {
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "model": settings.model_name,
    }

    # Retry up to 5 times on 429 rate limit with backoff
    for _attempt in range(5):
        response = requests.post(url, params=params, headers=headers, json=body, timeout=90)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 30))
            wait = max(retry_after, 5)
            time.sleep(wait)
            if settings.use_azure_ad:
                headers["Authorization"] = f"Bearer {_get_azure_ad_token()}"
            continue

        if response.status_code >= 400:
            tenant_mismatch = "does not match resource tenant" in response.text.lower()
            can_retry_with_key = settings.use_azure_ad and bool(settings.api_key)

            if tenant_mismatch and can_retry_with_key:
                retry_headers = {"Content-Type": "application/json", "api-key": settings.api_key}
                response = requests.post(url, params=params, headers=retry_headers, json=body, timeout=90)

            if response.status_code >= 400:
                raise OpenAIClientError(f"OpenAI API error {response.status_code}: {response.text}")
        break
    else:
        raise OpenAIClientError(f"OpenAI API error {response.status_code}: {response.text}")

    payload = response.json()
    try:
        return payload["choices"][0]["message"]["content"]
    except Exception as exc:
        raise OpenAIClientError(f"Unexpected OpenAI response: {payload}") from exc


def chat_completion_with_tools(
    settings: OpenAISettings,
    messages: List[Dict],
    tools: List[Dict],
    temperature: float = 0.0,
    max_tokens: int = 4000,
) -> Dict[str, Any]:
    """Call Azure OpenAI with function/tool definitions.

    Returns {"content": str|None, "tool_calls": list|None, "finish_reason": str}.
    """
    if not settings.endpoint or not settings.deployment_name:
        raise OpenAIClientError("Missing AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_DEPLOYMENT_NAME")

    url = f"{settings.endpoint}/openai/deployments/{settings.deployment_name}/chat/completions"
    params = {"api-version": settings.api_version}

    headers = {"Content-Type": "application/json"}
    if settings.use_azure_ad:
        headers["Authorization"] = f"Bearer {_get_azure_ad_token()}"
    else:
        headers["api-key"] = settings.api_key

    body: Dict[str, Any] = {
        "messages": messages,
        "tools": [{"type": "function", "function": t} for t in tools],
        "max_completion_tokens": max_tokens,
    }

    for _attempt in range(5):
        response = requests.post(url, params=params, headers=headers, json=body, timeout=120)

        if response.status_code == 429:
            wait = max(int(response.headers.get("Retry-After", 30)), 5)
            time.sleep(wait)
            if settings.use_azure_ad:
                headers["Authorization"] = f"Bearer {_get_azure_ad_token()}"
            continue

        if response.status_code >= 400:
            if "does not match resource tenant" in response.text.lower() and settings.api_key:
                retry_headers = {"Content-Type": "application/json", "api-key": settings.api_key}
                response = requests.post(url, params=params, headers=retry_headers, json=body, timeout=120)
            if response.status_code >= 400:
                raise OpenAIClientError(f"OpenAI API error {response.status_code}: {response.text}")
        break
    else:
        raise OpenAIClientError(f"OpenAI API error after retries: {response.status_code}")

    payload = response.json()
    try:
        choice = payload["choices"][0]
        message = choice["message"]
        return {
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls"),
            "finish_reason": choice.get("finish_reason", "stop"),
        }
    except Exception as exc:
        raise OpenAIClientError(f"Unexpected OpenAI response: {payload}") from exc
