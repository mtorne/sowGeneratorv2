"""Metadata inference agent — LLM-based structured extraction of customer/project info."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.llm import call_llm

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a technical consultant extracting structured metadata from project briefs.
Return STRICT JSON only. No markdown. No code fences. No extra text.
If a value cannot be determined from the context, use an empty string "" or empty array [].
Never invent data not present in the context.
""".strip()

_USER_TEMPLATE = """\
Extract structured metadata from the project context below.

Return JSON with EXACTLY this schema:
{{
  "country": "country of operations of the customer company",
  "industry": "industry sector (e.g. AI / Computer Vision, Retail, Finance)",
  "company_description": "1-sentence description of what the customer company does",
  "app_architecture_type": "e.g. Microservices (Containers/Kubernetes), Monolith, Serverless",
  "development_languages": "comma-separated programming languages used in the application",
  "hardware_dependencies": "any special hardware (GPUs, FPGAs, etc.) — empty string if none",
  "used_technologies": "comma-separated key technologies and tools currently in use",
  "db_product_edition": "database engine, edition and version (e.g. MySQL 8.0, PostgreSQL 15)",
  "db_server_sizing": "CPU / RAM / storage sizing for the DB tier — or empty if unknown",
  "db_os_requirements": "OS requirements for the database tier",
  "db_size_and_growth": "current DB size and expected growth",
  "db_scalability": "concurrent users / connections / transactions per second",
  "db_availability": "uptime % and HA/DR configuration, RTO/RPO if mentioned",
  "db_backup": "backup strategy (full, incremental, frequency)",
  "db_security": "encryption at rest / in-flight, access control requirements",
  "app_product_version": "application product name and version",
  "app_server_sizing": "compute sizing for the application tier",
  "app_os_dependencies": "OS and runtime dependencies of the application",
  "app_required_features": "key application features or functionality (2-3 sentences)",
  "oci_bom": [
    {{
      "service": "OCI service name (e.g. Oracle Kubernetes Engine (OKE))",
      "sizing_unit": "sizing unit (e.g. Worker Nodes, vCPUs, TB, Instances)",
      "amount": "quantity or size as a string (e.g. 3, 100 GB)",
      "comments": "brief note about this service"
    }}
  ]
}}

Rules:
- For oci_bom, list ALL target OCI services that the application will use after migration.
  Derive them from the architecture_analysis target components and the scope/services fields.
  Include at minimum: compute/OKE, networking (VCN/Load Balancer), storage, database,
  and any application-specific services mentioned.
- Do not list Oracle-internal tooling (e.g. Oracle Labs tooling) in oci_bom.

Project context:
{context_json}

Architecture analysis (if available):
{architecture_json}
""".strip()


class MetadataInferenceAgent:
    """Infers structured customer and project metadata from free-form project context."""

    def infer(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return a structured metadata dict inferred from the project context.

        Never raises — returns an empty dict on any failure so the pipeline
        degrades gracefully when inference is unavailable.
        """
        architecture_analysis = context.get("architecture_analysis") or {}
        architecture_json = json.dumps(architecture_analysis, ensure_ascii=False, indent=2)

        # Strip architecture_analysis from the main context to avoid duplication.
        context_clean = {k: v for k, v in context.items() if k != "architecture_analysis"}
        context_json = json.dumps(context_clean, ensure_ascii=False, indent=2)

        user_prompt = _USER_TEMPLATE.format(
            context_json=context_json,
            architecture_json=architecture_json,
        )

        try:
            raw = call_llm(system_prompt=_SYSTEM_PROMPT, user_prompt=user_prompt).strip()
            raw = self._strip_fences(raw)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError(f"Expected dict, got {type(result)}")
            # Normalise oci_bom to always be a list of dicts
            bom = result.get("oci_bom")
            if not isinstance(bom, list):
                result["oci_bom"] = []
            else:
                result["oci_bom"] = [r for r in bom if isinstance(r, dict)]
            logger.info(
                "metadata_inference.inferred keys=%s bom_entries=%d",
                list(result.keys()),
                len(result["oci_bom"]),
            )
            return result
        except Exception:
            logger.exception("metadata_inference.infer_failed — returning empty metadata")
            return {}

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove surrounding ```json ... ``` fences if present."""
        text = text.strip()
        if not text.startswith("```"):
            return text
        # strip opening fence line
        newline = text.find("\n")
        if newline == -1:
            return text
        body = text[newline + 1:].strip()
        if body.endswith("```"):
            body = body[:-3].strip()
        return body

    @staticmethod
    def _extract_balanced(text: str) -> str:
        """Return the first balanced JSON object from text."""
        start = text.find("{")
        if start == -1:
            return text
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(text[start:], start):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start: i + 1]
        return text
