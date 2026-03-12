"""Writer agent for SoW section drafting."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.services.llm import call_llm
from app.services.rag_service import SectionChunk

logger = logging.getLogger(__name__)

# Sections that are prompted for structured JSON output instead of prose.
# For these sections the LLM is asked to return a JSON object matching the
# schema defined in app/models/section_outputs.py; doc_builder then renders
# the parsed dict directly rather than parsing free-form text.
STRUCTURED_OUTPUT_SECTIONS: frozenset[str] = frozenset({
    "MILESTONE PLAN",
})

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "templates" / "prompts"


def _jinja_env():  # type: ignore[return]
    """Return a Jinja2 environment pointed at the prompts directory.

    Import is deferred so a missing jinja2 install raises at call time
    (with a clear message) rather than crashing the entire app at startup.
    """
    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "jinja2 is required but not installed — run: pip install jinja2"
        ) from exc

    return Environment(
        loader=FileSystemLoader(str(_PROMPTS_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


class WriterAgent:
    """Generates section-level SoW content."""

    # Sections that describe the CURRENT (pre-migration) environment.
    _CURRENT_STATE_SECTIONS: frozenset[str] = frozenset({
        "CURRENT STATE ARCHITECTURE DESCRIPTION",
    })

    # Sections that describe the TARGET OCI architecture.
    _TARGET_SECTIONS: frozenset[str] = frozenset({
        "FUTURE STATE ARCHITECTURE",
        "ARCHITECTURE DEPLOYMENT OVERVIEW",
        "IMPLEMENTATION DETAILS",
        "ARCHITECTURE COMPONENTS",
    })

    def _build_evidence_lines(
        self,
        section_name: str,
        diagram_components: dict | None,
        context: dict,
    ) -> list[str]:
        """Extract mandatory architecture facts from diagram_components for the prompt."""
        if not diagram_components:
            return []

        lines: list[str] = []
        sname = section_name.upper()

        def _join(val: object) -> str:
            if isinstance(val, list):
                return ", ".join(str(i) for i in val if str(i).strip())
            return str(val).strip()

        if sname in self._CURRENT_STATE_SECTIONS:
            for key in ("compute", "databases", "networking"):
                val = diagram_components.get(key)
                if val:
                    joined = _join(val)
                    if joined:
                        lines.append(f"Current {key}: {joined}")
        elif sname in self._TARGET_SECTIONS:
            for key in ("compute", "databases", "networking", "load_balancers", "security"):
                val = diagram_components.get(key)
                if val:
                    joined = _join(val)
                    if joined:
                        label = key.replace("_", " ").title()
                        lines.append(f"Target {label}: {joined}")
            # For IMPLEMENTATION DETAILS, surface deployment topology as an
            # evidence line so the LLM treats region/subnet/gateway data as
            # mandatory facts rather than optional context.
            if sname == "IMPLEMENTATION DETAILS":
                topo = diagram_components.get("deployment_topology")
                if topo and str(topo).strip():
                    lines.append(f"Deployment topology: {str(topo).strip()}")
        elif sname == "CURRENTLY USED TECHNOLOGY STACK":
            arch = context.get("architecture_analysis", {})
            if isinstance(arch, dict):
                current = arch.get("current", {})
                if isinstance(current, dict):
                    for key in ("compute", "databases", "networking"):
                        val = current.get(key)
                        if val:
                            joined = _join(val)
                            if joined:
                                lines.append(f"Current {key}: {joined}")

        return lines

    def _build_guardrails(
        self,
        section_name: str,  # noqa: ARG002 — reserved for future per-section filtering
        diagram_components: dict | None,
    ) -> list[str]:
        """Scan diagram_components for known patterns and return conditional guardrail sentences."""
        if not diagram_components:
            return []

        guardrails: list[str] = []

        def _flatten(val: object) -> list[str]:
            if isinstance(val, list):
                return [str(i) for i in val]
            if isinstance(val, dict):
                result: list[str] = []
                for v in val.values():
                    result.extend(_flatten(v))
                return result
            if val:
                return [str(val)]
            return []

        all_text = " ".join(
            item for v in diagram_components.values() for item in _flatten(v)
        ).lower()

        def _section_text(key: str) -> str:
            return " ".join(_flatten(diagram_components.get(key, []))).lower()

        security_text = _section_text("security")
        databases_text = _section_text("databases")
        ha_text = _section_text("ha_dr") or _section_text("ha")
        lb_text = _section_text("load_balancers")
        topology_text = str(diagram_components.get("deployment_topology", "")).lower()
        on_prem_val = diagram_components.get("on_prem_connectivity") or diagram_components.get("connectivity")
        on_prem_present = bool(on_prem_val) or any(kw in all_text for kw in ("drg", "vpn", "fastconnect"))

        if "oke" in all_text or "kubernetes" in all_text:
            guardrails.append(
                "OKE is present — describe OKE cluster, node pools, and Kubernetes version."
            )

        if "waf" in security_text or "waf" in all_text:
            guardrails.append("WAF is present — describe WAF protection on ingress.")

        if on_prem_present:
            guardrails.append(
                "On-premises connectivity detected — describe DRG/VPN/FastConnect."
            )

        dataguard_present = any(
            kw in t for kw in ("data guard", "dataguard")
            for t in (databases_text, ha_text, all_text)
        )
        if dataguard_present:
            guardrails.append(
                "Data Guard detected — describe replication mode and standby configuration."
            )

        if lb_text.strip() or "load balancer" in all_text:
            guardrails.append(
                "Load Balancer detected — describe ingress routing and health checks."
            )

        if (
            "multi_region" in diagram_components
            or "multi-region" in topology_text
            or "multi region" in topology_text
        ):
            guardrails.append(
                "Multi-region deployment — describe primary/DR region roles."
            )

        # Implementation-specific guardrails: surface when subnet, storage,
        # or DNS patterns are detected so the LLM produces deeper bullets.
        if "subnet" in all_text:
            guardrails.append(
                "Subnets detected — list each subnet with its CIDR, visibility "
                "(public/private), and role (LB, Web, App, DB)."
            )

        if "file storage" in all_text or "fss" in all_text or "mount target" in all_text:
            guardrails.append(
                "File Storage detected — describe mount targets, cross-region "
                "replication, and NFS configuration."
            )

        if "dns" in all_text or "traffic management" in all_text:
            guardrails.append(
                "DNS / traffic management detected — describe routing policy "
                "and regional failover mechanism."
            )

        return guardrails

    def write_section(
        self,
        section_name: str,
        context: dict[str, Any],
        rag_context: list[SectionChunk] | None = None,
        disallowed_services: list[str] | None = None,
        diagram_components: dict | None = None,
    ) -> str:
        """Create a section body in professional consulting style.

        Args:
            diagram_components: Structured components dict extracted from the target
                architecture diagram analysis (ArchitectureVisionAgent output). When
                provided for the ARCHITECTURE COMPONENTS section the LLM is instructed
                to use only the real services identified in the diagram rather than
                generating generic descriptions.
        """
        examples = "\n\n".join(
            f"Reference Example {idx}:\n{chunk.text}"
            for idx, chunk in enumerate(rag_context or [], start=1)
        )

        env = _jinja_env()

        # Structured-output mode: for designated sections the LLM is asked to
        # return a JSON object instead of prose.  Affects both the system-prompt
        # OUTPUT FORMAT block and the section-specific schema instruction.
        json_output = section_name.upper() in STRUCTURED_OUTPUT_SECTIONS

        system_prompt = env.get_template("writer_system.j2").render(
            disallowed_services=disallowed_services or [],
            json_output=json_output,
        ).strip()

        evidence_lines = self._build_evidence_lines(section_name, diagram_components, context)
        guardrails = self._build_guardrails(section_name, diagram_components)

        user_prompt = env.get_template("writer_user.j2").render(
            section_name=section_name,
            context_json=json.dumps(context, ensure_ascii=False),
            scope=context.get("scope", ""),
            examples=examples,
            diagram_components=diagram_components,
            json_output=json_output,
            evidence_lines=evidence_lines,
            guardrails=guardrails,
        ).strip()

        logger.debug(
            "writer.render_prompts section=%s system_len=%d user_len=%d json_output=%s",
            section_name,
            len(system_prompt),
            len(user_prompt),
            json_output,
        )

        raw = call_llm(system_prompt=system_prompt, user_prompt=user_prompt).strip()

        logger.info(
            "writer.llm_output section=%s len=%d preview=%r",
            section_name,
            len(raw),
            raw[:300],
        )

        if json_output:
            # Strip optional markdown code-fence wrappers that some models add.
            # e.g.  ```json\n{...}\n```  →  {...}
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
            raw = raw.strip()
            logger.debug("writer.json_output section=%s len=%d", section_name, len(raw))

        return raw
