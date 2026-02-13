from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from services.oci_client import OCIGenAIService

logger = logging.getLogger(__name__)


class ArchitectureVisionAgent:
    """Multi-phase extractor for OCI architecture diagrams with strict JSON output."""

    OUTPUT_TEMPLATE: Dict[str, Any] = {
        "compute": [],
        "kubernetes": [],
        "databases": [],
        "networking": [],
        "load_balancers": [],
        "security": [],
        "storage": [],
        "streaming": [],
        "on_prem_connectivity": [],
        "high_availability_pattern": [],
        "relationships": [],
        "confidence_scores": [],
    }

    def __init__(self, oci_service: OCIGenAIService | None = None) -> None:
        self.oci_service = oci_service or OCIGenAIService()

    def _response_format(self) -> Dict[str, Any]:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "compute": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "kubernetes": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "databases": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "networking": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "load_balancers": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "security": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "storage": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "streaming": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "on_prem_connectivity": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "high_availability_pattern": {"type": "array", "items": {"$ref": "#/$defs/component"}},
                "relationships": {"type": "array", "items": {"$ref": "#/$defs/relationship"}},
                "confidence_scores": {"type": "array", "items": {"$ref": "#/$defs/confidence"}},
            },
            "required": [
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
                "relationships",
                "confidence_scores",
            ],
            "$defs": {
                "component": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "label": {"type": "string"},
                        "details": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["name", "label", "details", "confidence"],
                },
                "relationship": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "direction": {"type": "string"},
                        "connection_type": {"type": "string"},
                        "tier": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["source", "target", "direction", "connection_type", "tier", "confidence"],
                },
                "confidence": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "component": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["component", "confidence"],
                },
            },
        }
        return {
            "type": "JSON_SCHEMA",
            "json_schema": {
                "name": "architecture_diagram_multiphase_extraction",
                "strict": True,
                "schema": schema,
            },
        }

    def _vision_prompt(self, diagram_type: str) -> str:
        return (
            "You are an OCI cloud architecture analyst. "
            "Analyze the provided architecture diagram image. "
            "Extract only visually verifiable components. "
            "Do not infer hidden services. "
            "If uncertain, mark confidence as low. "
            "Return structured JSON only. "
            "No explanations.\n"
            f"diagram_type={diagram_type}\n"
            "Step A - component detection: compute, kubernetes, databases, networking, load_balancers, security, storage, streaming, on_prem_connectivity, high_availability_pattern.\n"
            "Step B - relationship and topology extraction: source-target connections, public/private tier, data flow, ingress, egress, HA and node pool signals if visible.\n"
            "Step C - confidence scores for every detected component.\n"
            "Never assume services not visible. Never infer specific shapes unless explicitly written. "
            "Never assume AD configuration unless visible. Never infer database type unless labeled."
        )

    def extract_architecture_from_image(self, image_data_uri: str, diagram_type: str) -> Dict[str, Any]:
        try:
            raw = self.oci_service.analyze_diagram(
                image_data_uri=image_data_uri,
                prompt=self._vision_prompt(diagram_type),
                response_format=self._response_format(),
            )
            parsed = self._parse_json(raw)
            normalized = self._normalize(parsed)
            logger.info("Diagram %s services detected: %s", diagram_type, self._detected_service_names(normalized))
            return normalized
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
        component_keys = [
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

        for key in component_keys:
            items = payload.get(key) if isinstance(payload.get(key), list) else []
            normalized[key] = [self._normalize_component(item) for item in items]

        relationships = payload.get("relationships") if isinstance(payload.get("relationships"), list) else []
        normalized["relationships"] = [self._normalize_relationship(item) for item in relationships if isinstance(item, dict)]

        confidence_scores = payload.get("confidence_scores") if isinstance(payload.get("confidence_scores"), list) else []
        normalized["confidence_scores"] = [self._normalize_confidence_score(item) for item in confidence_scores if isinstance(item, dict)]

        if not normalized["confidence_scores"]:
            normalized["confidence_scores"] = self._build_confidence_from_components(normalized)

        return normalized

    def _normalize_component(self, item: Any) -> Dict[str, Any]:
        if not isinstance(item, dict):
            item = {"name": str(item)}
        return {
            "name": str(item.get("name") or "Unknown").strip(),
            "label": str(item.get("label") or "").strip(),
            "details": str(item.get("details") or "").strip(),
            "confidence": self._normalize_confidence(item.get("confidence")),
        }

    def _normalize_relationship(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "source": str(item.get("source") or "unknown").strip(),
            "target": str(item.get("target") or "unknown").strip(),
            "direction": str(item.get("direction") or "unknown").strip(),
            "connection_type": str(item.get("connection_type") or "unknown").strip(),
            "tier": str(item.get("tier") or "unknown").strip(),
            "confidence": self._normalize_confidence(item.get("confidence")),
        }

    def _normalize_confidence_score(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "component": str(item.get("component") or "unknown").strip(),
            "confidence": self._normalize_confidence(item.get("confidence")),
        }

    def _build_confidence_from_components(self, payload: Dict[str, Any]) -> List[Dict[str, str]]:
        scores: List[Dict[str, str]] = []
        for key in [k for k in payload.keys() if k not in {"relationships", "confidence_scores"}]:
            for item in payload.get(key, []):
                if isinstance(item, dict) and item.get("name"):
                    scores.append(
                        {
                            "component": str(item.get("name")),
                            "confidence": self._normalize_confidence(item.get("confidence")),
                        }
                    )
        return scores

    @staticmethod
    def _normalize_confidence(value: Any) -> str:
        confidence = str(value or "low").strip().lower()
        return confidence if confidence in {"high", "medium", "low"} else "low"

    @staticmethod
    def _detected_service_names(payload: Dict[str, Any]) -> List[str]:
        services: List[str] = []
        for key, value in payload.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get("name"):
                        services.append(str(item["name"]))
        return services
