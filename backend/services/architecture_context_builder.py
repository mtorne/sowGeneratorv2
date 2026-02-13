from __future__ import annotations

from typing import Any, Dict, List, Tuple


class ArchitectureContextBuilder:
    """Merge project + extracted architecture signals into normalized context for section generation."""

    COMPONENT_KEYS = [
        "compute",
        "kubernetes",
        "databases",
        "networking",
        "load_balancers",
        "security",
        "storage",
        "streaming",
        "on_prem_connectivity",
        "high_availability_pattern",
    ]

    def build(
        self,
        project_data: Dict[str, Any],
        current_architecture_extracted: Dict[str, Any] | None,
        target_architecture_extracted: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        current = self._normalize_extraction(current_architecture_extracted or {})
        target = self._normalize_extraction(target_architecture_extracted or {})
        cross_validation = self._cross_validate_with_project_data(project_data, current, target)
        inconsistencies = self._detect_inconsistencies(current, target, cross_validation)

        return {
            "project_data": project_data,
            "current_state": current,
            "target_state": target,
            "technology_stack": self._build_technology_stack(project_data, current, target),
            "cross_validation": cross_validation,
            "inconsistencies": inconsistencies,
        }

    def _normalize_extraction(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {key: [] for key in self.COMPONENT_KEYS}
        for key in self.COMPONENT_KEYS:
            items = extracted.get(key) if isinstance(extracted.get(key), list) else []
            normalized[key] = self._dedupe_components(items)
        normalized["relationships"] = extracted.get("relationships") if isinstance(extracted.get("relationships"), list) else []
        normalized["confidence_scores"] = extracted.get("confidence_scores") if isinstance(extracted.get("confidence_scores"), list) else []
        return normalized

    def _dedupe_components(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "Unknown").strip()
            label = str(item.get("label") or "").strip()
            key = (name.lower(), label.lower())
            if key not in deduped:
                deduped[key] = {
                    "name": name,
                    "label": label,
                    "details": str(item.get("details") or "").strip(),
                    "confidence": str(item.get("confidence") or "low").lower(),
                }
        return list(deduped.values())

    def _cross_validate_with_project_data(self, project_data: Dict[str, Any], current: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
        validations: Dict[str, Any] = {
            "mismatches": [],
            "unconfirmed": [],
            "enforcements": [],
        }
        stated_database = str(project_data.get("database") or project_data.get("db") or "").lower()
        diagram_databases = {item.get("name", "").lower() for item in current.get("databases", []) + target.get("databases", [])}

        if stated_database and diagram_databases and stated_database not in diagram_databases:
            validations["mismatches"].append(
                f"project_data database '{stated_database}' does not match diagram database {sorted(diagram_databases)}"
            )

        project_data_text = str(project_data).lower()
        diagram_streaming = [item.get("name") for item in current.get("streaming", []) + target.get("streaming", []) if item.get("name")]
        for stream in diagram_streaming:
            if str(stream).lower() not in project_data_text:
                validations["unconfirmed"].append(f"diagram shows streaming component '{stream}' not declared in project_data")

        on_prem_present = bool(current.get("on_prem_connectivity") or target.get("on_prem_connectivity"))
        if on_prem_present:
            validations["enforcements"].append("on-prem connectivity detected; enforce DRG/VPN mention in architecture sections")

        return validations

    def _detect_inconsistencies(self, current: Dict[str, Any], target: Dict[str, Any], cross_validation: Dict[str, Any]) -> List[str]:
        current_db = {c["name"].lower() for c in current.get("databases", [])}
        target_db = {c["name"].lower() for c in target.get("databases", [])}
        issues: List[str] = []

        if "mysql" in current_db and "postgresql" in target_db and "mysql" not in target_db:
            issues.append("Target introduces PostgreSQL while current uses MySQL; validate migration intent.")

        issues.extend(cross_validation.get("mismatches", []))
        issues.extend(cross_validation.get("unconfirmed", []))
        return issues

    def _build_technology_stack(
        self,
        project_data: Dict[str, Any],
        current: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        stack: Dict[str, List[str]] = {
            "application": [],
            "database": [],
            "infrastructure": [],
            "security": [],
            "integration": [],
        }

        for db in current.get("databases", []) + target.get("databases", []):
            if db["name"] not in stack["database"]:
                stack["database"].append(db["name"])

        infra_sources = current.get("compute", []) + target.get("compute", []) + current.get("networking", []) + target.get("networking", [])
        for comp in infra_sources:
            if comp["name"] not in stack["infrastructure"]:
                stack["infrastructure"].append(comp["name"])

        for comp in current.get("security", []) + target.get("security", []):
            if comp["name"] not in stack["security"]:
                stack["security"].append(comp["name"])

        for comp in current.get("streaming", []) + target.get("streaming", []):
            if comp["name"] not in stack["integration"]:
                stack["integration"].append(comp["name"])

        declared_stack = project_data.get("technology_stack") if isinstance(project_data.get("technology_stack"), dict) else {}
        for key in stack:
            extra = declared_stack.get(key)
            if isinstance(extra, list):
                for val in extra:
                    if isinstance(val, str) and val not in stack[key]:
                        stack[key].append(val)
        return stack
