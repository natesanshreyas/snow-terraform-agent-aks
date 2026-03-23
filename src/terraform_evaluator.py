"""Terraform HCL quality evaluator using Azure AI Evaluation SDK.

Three SDK-compatible evaluator classes score generated Terraform 1-5 on:
  security   — no hardcoded credentials or sensitive literals
  compliance — required cost_center + ticket_id tags present
  quality    — uses module pattern, not raw resource blocks

All three must score >= 3 to pass.  If a run fails, feedback is returned so the
orchestrator can inject it and ask for regeneration (up to 2 retries).

Results are logged to Azure AI Foundry Evaluations tab via evaluate() when
AZURE_AI_FOUNDRY_PROJECT_CONNECTION_STRING is configured.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Dict

from .openai_client import OpenAISettings, chat_completion

logger = logging.getLogger(__name__)

_PASS_THRESHOLD = 3  # minimum score (out of 5) on each dimension to pass


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    security: int    # 1–5
    compliance: int  # 1–5
    quality: int     # 1–5
    passed: bool
    reason: str      # non-empty only when passed=False


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SECURITY_PROMPT = """\
You are a Terraform security reviewer. Score the HCL below from 1 (very insecure) \
to 5 (very secure) based ONLY on the following criteria:

  5 — No hardcoded secrets, passwords, API keys, or connection strings anywhere; \
sensitive values use input variables or are entirely absent.
  4 — Only minor, non-sensitive literals hardcoded (e.g. a public DNS name).
  3 — Sensitive values use variables; no credentials visible in plain text.
  2 — At least one sensitive value (e.g. a password) is hardcoded.
  1 — Credentials or API keys are clearly visible in the code.

--- main.tf ---
{main_tf}

--- variables.tf ---
{variables_tf}

Return ONLY a JSON object (no markdown, no explanation outside JSON):
{{"score": <integer 1-5>, "reason": "<one concise sentence>"}}"""

_COMPLIANCE_PROMPT = """\
You are a Terraform compliance reviewer. Score the HCL below from 1 (non-compliant) \
to 5 (fully compliant) based ONLY on tag compliance:

Required tags that MUST appear on BOTH the resource group module AND the main \
resource module:
  • cost_center
  • ticket_id  (expected value references the ticket: {ticket_id})

  5 — Both tags present on resource group and main resource.
  4 — Both tags present on the main resource only.
  3 — At least one required tag present somewhere.
  2 — Tags block exists but required tags are absent.
  1 — No tags at all.

--- main.tf ---
{main_tf}

Return ONLY a JSON object (no markdown, no explanation outside JSON):
{{"score": <integer 1-5>, "reason": "<one concise sentence>"}}"""

_QUALITY_PROMPT = """\
You are a Terraform code quality reviewer. Score the HCL below from 1 (poor) \
to 5 (excellent) based ONLY on structural quality:

  5 — Uses module blocks (not raw resource blocks) for all resources; \
variables.tf has types and descriptions; clean, readable structure.
  4 — Mostly uses modules with minor issues.
  3 — Uses at least one module block; acceptable structure.
  2 — Primarily raw resource blocks; minimal use of modules.
  1 — No modules used; sprawling raw resource declarations.

--- main.tf ---
{main_tf}

--- variables.tf ---
{variables_tf}

Return ONLY a JSON object (no markdown, no explanation outside JSON):
{{"score": <integer 1-5>, "reason": "<one concise sentence>"}}"""


# ---------------------------------------------------------------------------
# Helper — parse {score, reason} from LLM response
# ---------------------------------------------------------------------------


def _parse_score(text: str) -> dict:
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "score" in obj:
            return obj
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[m.start():])
            if isinstance(obj, dict) and "score" in obj:
                return obj
        except Exception:
            continue
    logger.warning("Evaluator returned unparseable response: %s", text[:300])
    return {"score": 3, "reason": "Could not parse evaluator response"}


# ---------------------------------------------------------------------------
# Azure AI Evaluation SDK-compatible evaluator classes
# Each class has a __call__ that the SDK invokes per data row.
# ---------------------------------------------------------------------------


class SecurityEvaluator:
    """Scores Terraform HCL 1-5 on security (no hardcoded secrets/credentials)."""

    def __init__(self, openai_settings: OpenAISettings):
        self._settings = openai_settings

    def __call__(self, *, main_tf: str, variables_tf: str = "", **kwargs) -> dict:
        prompt = _SECURITY_PROMPT.format(main_tf=main_tf, variables_tf=variables_tf)
        response = chat_completion(
            self._settings,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        result = _parse_score(response)
        score = max(1, min(5, int(result.get("score", 3))))
        return {
            "security_score": score,
            "security_reason": result.get("reason", ""),
            "security_passed": score >= _PASS_THRESHOLD,
        }


class ComplianceEvaluator:
    """Scores Terraform HCL 1-5 on tag compliance (cost_center + ticket_id required)."""

    def __init__(self, openai_settings: OpenAISettings):
        self._settings = openai_settings

    def __call__(self, *, main_tf: str, ticket_id: str = "", **kwargs) -> dict:
        prompt = _COMPLIANCE_PROMPT.format(main_tf=main_tf, ticket_id=ticket_id)
        response = chat_completion(
            self._settings,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        result = _parse_score(response)
        score = max(1, min(5, int(result.get("score", 3))))
        return {
            "compliance_score": score,
            "compliance_reason": result.get("reason", ""),
            "compliance_passed": score >= _PASS_THRESHOLD,
        }


class QualityEvaluator:
    """Scores Terraform HCL 1-5 on structural quality (module pattern usage)."""

    def __init__(self, openai_settings: OpenAISettings):
        self._settings = openai_settings

    def __call__(self, *, main_tf: str, variables_tf: str = "", **kwargs) -> dict:
        prompt = _QUALITY_PROMPT.format(main_tf=main_tf, variables_tf=variables_tf)
        response = chat_completion(
            self._settings,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        result = _parse_score(response)
        score = max(1, min(5, int(result.get("score", 3))))
        return {
            "quality_score": score,
            "quality_reason": result.get("reason", ""),
            "quality_passed": score >= _PASS_THRESHOLD,
        }


# ---------------------------------------------------------------------------
# Core evaluation runner — calls azure.ai.evaluation.evaluate()
# ---------------------------------------------------------------------------


def _run_evaluate(
    main_tf: str,
    variables_tf: str,
    ticket_id: str,
    openai_settings: OpenAISettings,
) -> dict:
    """Synchronous evaluate() call — invoked via asyncio.to_thread."""
    from azure.ai.evaluation import evaluate
    from .telemetry import get_foundry_scope

    # Write a single-row JSONL file for evaluate()
    row = {"main_tf": main_tf, "variables_tf": variables_tf, "ticket_id": ticket_id}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(row) + "\n")
        data_path = f.name

    kwargs: Dict[str, Any] = {
        "evaluation_name": f"terraform-eval-{ticket_id}",
        "data": data_path,
        "evaluators": {
            "security":   SecurityEvaluator(openai_settings),
            "compliance": ComplianceEvaluator(openai_settings),
            "quality":    QualityEvaluator(openai_settings),
        },
        "evaluator_config": {
            "security":   {"main_tf": "${data.main_tf}", "variables_tf": "${data.variables_tf}"},
            "compliance": {"main_tf": "${data.main_tf}", "ticket_id": "${data.ticket_id}"},
            "quality":    {"main_tf": "${data.main_tf}", "variables_tf": "${data.variables_tf}"},
        },
    }

    # Log to Foundry Evaluations tab if project is configured
    scope = get_foundry_scope()
    if scope:
        kwargs["azure_ai_project"] = scope
        logger.info("Foundry project attached — scores will appear in Evaluations tab")

    return evaluate(**kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def evaluate_terraform(
    main_tf: str,
    variables_tf: str,
    ticket_id: str,
    openai_settings: OpenAISettings,
) -> EvalResult:
    """Run all three SDK evaluators via evaluate() and return a structured result."""
    logger.info("Running Terraform evaluation for ticket=%s", ticket_id)

    result = await asyncio.to_thread(
        _run_evaluate, main_tf, variables_tf, ticket_id, openai_settings
    )

    # evaluate() returns {"rows": [...], "metrics": {...}, ...}
    row = result["rows"][0]

    s = max(1, min(5, int(row.get("outputs.security.security_score",   3))))
    c = max(1, min(5, int(row.get("outputs.compliance.compliance_score", 3))))
    q = max(1, min(5, int(row.get("outputs.quality.quality_score",     3))))

    passed = s >= _PASS_THRESHOLD and c >= _PASS_THRESHOLD and q >= _PASS_THRESHOLD

    reasons: list[str] = []
    if s < _PASS_THRESHOLD:
        reasons.append(f"Security ({s}/5): {row.get('outputs.security.security_reason', '')}")
    if c < _PASS_THRESHOLD:
        reasons.append(f"Compliance ({c}/5): {row.get('outputs.compliance.compliance_reason', '')}")
    if q < _PASS_THRESHOLD:
        reasons.append(f"Quality ({q}/5): {row.get('outputs.quality.quality_reason', '')}")

    logger.info(
        "Eval: security=%d compliance=%d quality=%d passed=%s",
        s, c, q, passed,
    )

    return EvalResult(
        security=s,
        compliance=c,
        quality=q,
        passed=passed,
        reason="; ".join(reasons),
    )
