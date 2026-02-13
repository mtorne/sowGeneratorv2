from __future__ import annotations

from typing import Any, Dict, List


class ArchitectureGuardrails:
    """Rule checks for architecture-driven sections to prevent unsupported statements."""

    @staticmethod
    def validate(section_name: str, section_text: str, architecture_context: Dict[str, Any]) -> List[str]:
        text = (section_text or "").lower()
        current = architecture_context.get("current_state", {})
        target = architecture_context.get("target_state", {})
        issues: List[str] = []

        all_components = ArchitectureGuardrails._collect_component_names(current) | ArchitectureGuardrails._collect_component_names(target)

        if "oke" in all_components and "oke" not in text and "kubernetes" not in text:
            issues.append(f"{section_name}: OKE detected in architecture but not reflected in generated text.")

        if "mysql" in all_components and "postgresql" in text and "postgresql" not in all_components:
            issues.append(f"{section_name}: PostgreSQL introduced despite MySQL-only architecture evidence.")

        if "streaming" not in all_components and "streaming" in text:
            issues.append(f"{section_name}: Streaming mentioned without support in project or extracted diagrams.")

        if any("load balancer" in name for name in all_components) and "ingress" not in text and "load balancer" not in text:
            issues.append(f"{section_name}: Load balancer detected but ingress explanation is missing.")

        on_prem_present = any("on-prem" in name or "on prem" in name for name in all_components)
        if on_prem_present and "vpn" not in text and "drg" not in text:
            issues.append(f"{section_name}: On-prem components detected but VPN/DRG connectivity is not addressed.")

        return issues

    @staticmethod
    def validate_document_consistency(sections: Dict[str, str], architecture_context: Dict[str, Any]) -> Dict[str, List[str]]:
        findings: Dict[str, List[str]] = {}
        joined = "\n".join([f"{k}: {v}" for k, v in sections.items()]).lower()
        component_names = ArchitectureGuardrails._collect_component_names(architecture_context.get("current_state", {})) | ArchitectureGuardrails._collect_component_names(architecture_context.get("target_state", {}))

        if "mysql" in component_names and "postgresql" in joined and "postgresql" not in component_names:
            findings["database_consistency"] = ["Document mentions PostgreSQL while architecture evidence indicates MySQL."]

        if "oke" in component_names and "kubernetes" not in joined and "oke" not in joined:
            findings["kubernetes_consistency"] = ["Architecture includes OKE but document sections omit Kubernetes mention."]

        if any("drg" in c or "on-prem" in c or "vpn" in c for c in component_names):
            if "drg" not in joined and "vpn" not in joined:
                findings["networking_consistency"] = ["On-prem connectivity appears in architecture but DRG/VPN is missing in text."]

        return findings

    @staticmethod
    def _collect_component_names(state: Dict[str, Any]) -> set[str]:
        names: set[str] = set()
        for key, value in state.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get("name"):
                        names.add(str(item["name"]).lower())
        return names
