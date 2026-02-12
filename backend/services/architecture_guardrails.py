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

        if "load balancer" in " ".join(all_components) and "ingress" not in text and "load balancer" not in text:
            issues.append(f"{section_name}: Load balancer detected but ingress explanation is missing.")

        on_prem_present = any("on-prem" in name or "on prem" in name for name in all_components)
        if on_prem_present and "vpn" not in text and "drg" not in text:
            issues.append(f"{section_name}: On-prem components detected but VPN/DRG connectivity is not addressed.")

        return issues

    @staticmethod
    def _collect_component_names(state: Dict[str, Any]) -> set[str]:
        names: set[str] = set()
        for key, value in state.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get("name"):
                        names.add(str(item["name"]).lower())
            elif key == "kubernetes" and isinstance(value, dict):
                for item in value.get("components", []):
                    if isinstance(item, dict) and item.get("name"):
                        names.add(str(item["name"]).lower())
                if value.get("present"):
                    names.add("oke")
                    names.add("kubernetes")
        return names
