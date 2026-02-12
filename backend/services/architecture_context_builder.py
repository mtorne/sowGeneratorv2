from __future__ import annotations

from typing import Any, Dict, List, Tuple


class ArchitectureContextBuilder:
    """Merge project + extracted architecture signals into normalized context for section generation."""

    COMPONENT_KEYS = [
        "compute",
        "networking",
        "databases",
        "load_balancers",
        "security_components",
        "storage",
        "integration_components",
    ]

    def build(
        self,
        project_data: Dict[str, Any],
        current_architecture_extracted: Dict[str, Any] | None,
        target_architecture_extracted: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        current = self._normalize_extraction(current_architecture_extracted or {})
        target = self._normalize_extraction(target_architecture_extracted or {})

        inconsistencies = self._detect_inconsistencies(current, target)

        return {
            "project_data": project_data,
            "current_state": current,
            "target_state": target,
            "technology_stack": self._build_technology_stack(project_data, current, target),
            "inconsistencies": inconsistencies,
        }

    def _normalize_extraction(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {key: [] for key in self.COMPONENT_KEYS}
        for key in self.COMPONENT_KEYS:
            items = extracted.get(key) if isinstance(extracted.get(key), list) else []
            normalized[key] = self._dedupe_components(items)

        k8s = extracted.get("kubernetes") if isinstance(extracted.get("kubernetes"), dict) else {}
        normalized["kubernetes"] = {
            "present": bool(k8s.get("present", False)),
            "components": self._dedupe_components(k8s.get("components") or []),
            "confidence": str(k8s.get("confidence") or "low").lower(),
        }
        return normalized

    def _dedupe_components(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "Unknown").strip()
            category = str(item.get("category") or "unknown").strip()
            key = (name.lower(), category.lower())
            if key not in deduped:
                deduped[key] = {
                    "name": name,
                    "category": category,
                    "platform": str(item.get("platform") or "unknown").strip(),
                    "topology": str(item.get("topology") or "unknown").strip(),
                    "confidence": str(item.get("confidence") or "low").lower(),
                    "notes": str(item.get("notes") or "").strip(),
                }
        return list(deduped.values())

    def _detect_inconsistencies(self, current: Dict[str, Any], target: Dict[str, Any]) -> List[str]:
        current_db = {c["name"].lower() for c in current.get("databases", [])}
        target_db = {c["name"].lower() for c in target.get("databases", [])}
        issues: List[str] = []

        if "mysql" in current_db and "postgresql" in target_db and "mysql" not in target_db:
            issues.append("Target introduces PostgreSQL while current uses MySQL; validate migration intent.")
        if current.get("kubernetes", {}).get("present") and not target.get("kubernetes", {}).get("present"):
            issues.append("Current architecture indicates Kubernetes but target does not.")
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

        for comp in current.get("security_components", []) + target.get("security_components", []):
            if comp["name"] not in stack["security"]:
                stack["security"].append(comp["name"])

        for comp in current.get("integration_components", []) + target.get("integration_components", []):
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
