from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from services.oci_client import OCIGenAIService

logger = logging.getLogger(__name__)


class ArchitectureVisionAgent:
    """Extract deterministic architecture JSON from a diagram image using OCI multimodal models."""

    OUTPUT_TEMPLATE: Dict[str, Any] = {
        "compute": [],
        "networking": [],
        "databases": [],
        "kubernetes": {"present": False, "components": [], "confidence": "low"},
        "load_balancers": [],
        "security_components": [],
        "storage": [],
        "integration_components": [],
    }

    def __init__(self, oci_service: OCIGenAIService | None = None) -> None:
        self.oci_service = oci_service or OCIGenAIService()

    def _response_format(self) -> Dict[str, Any]:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "compute": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "networking": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "databases": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "kubernetes": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "present": {"type": "boolean"},
                        "components": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["present", "components", "confidence"],
                },
                "load_balancers": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "security_components": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "storage": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "integration_components": {"type": "array", "items": {"$ref": "#/$defs/component"}},
            },
            "required": [
                "compute",
                "networking",
                "databases",
                "kubernetes",
                "load_balancers",
                "security_components",
                "storage",
                "integration_components",
            ],
            "$defs": {
                "component": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "category": {"type": "string"},
                        "platform": {"type": "string"},
                        "topology": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "notes": {"type": "string"},
                    },
                    "required": ["name", "category", "platform", "topology", "confidence", "notes"],
                }
            },
        }
        return {
            "type": "JSON_SCHEMA",
            "json_schema": {
                "name": "architecture_diagram_extraction",
                "strict": True,
                "schema": schema,
            },
        }

    def _vision_prompt(self, diagram_type: str) -> str:
        return (
            "You are an enterprise architecture diagram extraction engine. "
            f"Extract only what is visible in this {diagram_type} architecture diagram. "
            "Identify OCI services, on-prem components, networking topology, security layers, data tier, ingress/egress, "
            "high availability patterns, and deployment structure if visible. "
            "Do not infer unseen services. Do not hallucinate. "
            "When uncertain, include candidate with confidence='low'. "
            "Output JSON only matching the requested schema."
        )

    def extract_architecture_from_image(self, image_data_uri: str, diagram_type: str) -> Dict[str, Any]:
        try:
            raw = self.oci_service.analyze_diagram(
                image_data_uri=image_data_uri,
                prompt=self._vision_prompt(diagram_type),
                response_format=self._response_format(),
            )
            parsed = self._parse_json(raw)
            return self._normalize(parsed)
        except Exception as exc:
            logger.error("Architecture diagram extraction failed (%s): %s", diagram_type, exc)
            return self._normalize({})

    def _parse_json(self, raw: str) -> Dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                return {}
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}

    def _normalize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = json.loads(json.dumps(self.OUTPUT_TEMPLATE))
        for key in [
            "compute",
            "networking",
            "databases",
            "load_balancers",
            "security_components",
            "storage",
            "integration_components",
        ]:
            raw_items = payload.get(key) if isinstance(payload.get(key), list) else []
            normalized[key] = [self._normalize_component(item, key) for item in raw_items]

        k8s = payload.get("kubernetes") if isinstance(payload.get("kubernetes"), dict) else {}
        normalized["kubernetes"] = {
            "present": bool(k8s.get("present", False)),
            "components": [self._normalize_component(item, "kubernetes") for item in (k8s.get("components") or []) if isinstance(item, dict)],
            "confidence": self._normalize_confidence(k8s.get("confidence")),
        }
        return normalized

    def _normalize_component(self, item: Any, category: str) -> Dict[str, Any]:
        if not isinstance(item, dict):
            item = {"name": str(item)}
        return {
            "name": str(item.get("name") or "Unknown").strip(),
            "category": str(item.get("category") or category).strip(),
            "platform": str(item.get("platform") or "unknown").strip(),
            "topology": str(item.get("topology") or "unknown").strip(),
            "confidence": self._normalize_confidence(item.get("confidence")),
            "notes": str(item.get("notes") or "").strip(),
        }

    @staticmethod
    def _normalize_confidence(value: Any) -> str:
        confidence = str(value or "low").strip().lower()
        return confidence if confidence in {"high", "medium", "low"} else "low"
