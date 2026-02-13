from __future__ import annotations

import base64
import json
import logging
import re
import struct
from typing import Any, Dict, List

from services.oci_client import OCIGenAIService

logger = logging.getLogger(__name__)


class ArchitectureVisionAgent:
    """Multi-phase extractor for OCI architecture diagrams with strict JSON output."""

    OUTPUT_TEMPLATE: Dict[str, Any] = {
        "stage1": {
            "regions": [],
            "availability_domains": [],
            "fault_domains": [],
            "vcns": [],
            "subnets": [],
            "compute_instances": [],
            "load_balancers": [],
            "databases": [],
            "storage_services": [],
            "networking_components": [],
            "security_components": [],
            "external_connections": [],
            "replication_mechanisms": [],
            "dns_or_global_routing": [],
            "explicit_text_labels": [],
            "connections": [],
        },
        "stage2": {
            "architecture_pattern": {
                "multi_region": False,
                "active_active": False,
                "active_passive": False,
                "disaster_recovery_mechanism": "",
                "high_availability_mechanism": "",
            },
            "compute_layer": [],
            "application_layer": [],
            "database_layer": [],
            "storage_layer": [],
            "networking_layer": [],
            "security_layer": [],
            "traffic_flow_summary": "",
            "confidence_assessment": {
                "diagram_clarity": "low",
                "component_identification_confidence": "low",
                "overall_confidence": "low",
                "reason": "",
            },
        },
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
                "stage1": {"$ref": "#/$defs/stage1"},
                "stage2": {"$ref": "#/$defs/stage2"},
            },
            "required": ["stage1", "stage2"],
            "$defs": {
                "stage1": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "regions": {"type": "array", "items": {"type": "string"}},
                        "availability_domains": {"type": "array", "items": {"type": "string"}},
                        "fault_domains": {"type": "array", "items": {"type": "string"}},
                        "vcns": {"type": "array", "items": {"type": "string"}},
                        "subnets": {"type": "array", "items": {"type": "string"}},
                        "compute_instances": {"type": "array", "items": {"type": "string"}},
                        "load_balancers": {"type": "array", "items": {"type": "string"}},
                        "databases": {"type": "array", "items": {"type": "string"}},
                        "storage_services": {"type": "array", "items": {"type": "string"}},
                        "networking_components": {"type": "array", "items": {"type": "string"}},
                        "security_components": {"type": "array", "items": {"type": "string"}},
                        "external_connections": {"type": "array", "items": {"type": "string"}},
                        "replication_mechanisms": {"type": "array", "items": {"type": "string"}},
                        "dns_or_global_routing": {"type": "array", "items": {"type": "string"}},
                        "explicit_text_labels": {"type": "array", "items": {"type": "string"}},
                        "connections": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "regions",
                        "availability_domains",
                        "fault_domains",
                        "vcns",
                        "subnets",
                        "compute_instances",
                        "load_balancers",
                        "databases",
                        "storage_services",
                        "networking_components",
                        "security_components",
                        "external_connections",
                        "replication_mechanisms",
                        "dns_or_global_routing",
                        "explicit_text_labels",
                        "connections"
                    ],
                },
                "stage2": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "architecture_pattern": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "multi_region": {"type": "boolean"},
                                "active_active": {"type": "boolean"},
                                "active_passive": {"type": "boolean"},
                                "disaster_recovery_mechanism": {"type": "string"},
                                "high_availability_mechanism": {"type": "string"}
                            },
                            "required": ["multi_region", "active_active", "active_passive", "disaster_recovery_mechanism", "high_availability_mechanism"]
                        },
                        "compute_layer": {"type": "array", "items": {"type": "string"}},
                        "application_layer": {"type": "array", "items": {"type": "string"}},
                        "database_layer": {"type": "array", "items": {"type": "string"}},
                        "storage_layer": {"type": "array", "items": {"type": "string"}},
                        "networking_layer": {"type": "array", "items": {"type": "string"}},
                        "security_layer": {"type": "array", "items": {"type": "string"}},
                        "traffic_flow_summary": {"type": "string"},
                        "confidence_assessment": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "diagram_clarity": {"type": "string", "enum": ["high", "medium", "low"]},
                                "component_identification_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                                "overall_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                                "reason": {"type": "string"}
                            },
                            "required": ["diagram_clarity", "component_identification_confidence", "overall_confidence", "reason"]
                        }
                    },
                    "required": ["architecture_pattern", "compute_layer", "application_layer", "database_layer", "storage_layer", "networking_layer", "security_layer", "traffic_flow_summary", "confidence_assessment"]
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
            "GENERAL RULES for diagram analysis. "
            "Extract only what is visually verifiable. "
            "Do NOT assume services that are not explicitly visible. "
            "Do NOT infer OCI services unless clearly labeled or recognizable. "
            "If text is unreadable or blurred, mark it as unreadable. "
            "If architecture pattern seems implied but not explicitly drawn, do NOT assume it. "
            "Output must be valid JSON. No markdown. No explanations. No conclusions. No recommendations.\n"
            f"diagram_type={diagram_type}\n"
            "STAGE 1 – RAW VISUAL EXTRACTION: fill stage1 fields exactly. "
            "STAGE 2 – STRUCTURED ARCHITECTURE CLASSIFICATION: fill stage2 fields using only stage1 evidence. "
            "If DNS shows Active → Failover between regions, set active_passive=true. "
            "If Data Guard is visible, disaster_recovery_mechanism must be Oracle Data Guard. "
            "If multiple ADs are shown, include this in high_availability_mechanism. "
            "If image clarity is low or text unreadable, lower confidence and explain in reason."
        )

    def _image_metadata(self, image_data_uri: str) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "image_width": 0,
            "image_height": 0,
            "file_size": 0,
            "aspect_ratio": "unavailable",
        }
        if not isinstance(image_data_uri, str) or "," not in image_data_uri:
            return metadata

        try:
            payload = image_data_uri.split(",", 1)[1]
            blob = base64.b64decode(payload)
            metadata["file_size"] = len(blob)
            width, height = self._extract_dimensions(blob)
            metadata["image_width"] = width
            metadata["image_height"] = height
            if width > 0 and height > 0:
                metadata["aspect_ratio"] = f"{width / height:.3f}"
        except Exception:
            return metadata

        return metadata

    def _extract_dimensions(self, blob: bytes) -> tuple[int, int]:
        if blob.startswith(b"\x89PNG\r\n\x1a\n") and len(blob) >= 24:
            width, height = struct.unpack(">II", blob[16:24])
            return int(width), int(height)
        if blob.startswith(b"\xff\xd8"):
            return self._jpeg_dimensions(blob)
        return (0, 0)

    def _jpeg_dimensions(self, blob: bytes) -> tuple[int, int]:
        idx = 2
        while idx + 9 < len(blob):
            if blob[idx] != 0xFF:
                idx += 1
                continue
            marker = blob[idx + 1]
            idx += 2
            if marker in {0xD8, 0xD9}:
                continue
            if idx + 1 >= len(blob):
                break
            segment_len = (blob[idx] << 8) + blob[idx + 1]
            if segment_len < 2 or idx + segment_len > len(blob):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if idx + 7 < len(blob):
                    height = (blob[idx + 3] << 8) + blob[idx + 4]
                    width = (blob[idx + 5] << 8) + blob[idx + 6]
                    return int(width), int(height)
                break
            idx += segment_len
        return (0, 0)

    def extract_architecture_from_image(self, image_data_uri: str, diagram_type: str) -> Dict[str, Any]:
        try:
            metadata = self._image_metadata(image_data_uri)
            logger.info(
                "Diagram %s metadata: width=%s height=%s size_bytes=%s aspect_ratio=%s",
                diagram_type,
                metadata.get("image_width"),
                metadata.get("image_height"),
                metadata.get("file_size"),
                metadata.get("aspect_ratio"),
            )
            if int(metadata.get("image_width") or 0) < 1200:
                logger.warning(
                    "Diagram %s width below 1200px may reduce OCR clarity: width=%s",
                    diagram_type,
                    metadata.get("image_width"),
                )
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
        stage1 = payload.get("stage1") if isinstance(payload.get("stage1"), dict) else {}
        stage2 = payload.get("stage2") if isinstance(payload.get("stage2"), dict) else {}
        normalized["stage1"] = self._normalize_stage1(stage1)
        normalized["stage2"] = self._normalize_stage2(stage2)

        normalized["compute"] = self._components_from_text(normalized["stage1"].get("compute_instances", []), "compute")
        normalized["kubernetes"] = self._components_from_text(normalized["stage2"].get("application_layer", []), "application")
        normalized["databases"] = self._components_from_text(normalized["stage1"].get("databases", []), "database")
        normalized["networking"] = self._components_from_text(normalized["stage1"].get("networking_components", []), "networking")
        normalized["load_balancers"] = self._components_from_text(normalized["stage1"].get("load_balancers", []), "load_balancer")
        normalized["security"] = self._components_from_text(normalized["stage1"].get("security_components", []), "security")
        normalized["storage"] = self._components_from_text(normalized["stage1"].get("storage_services", []), "storage")
        normalized["streaming"] = self._components_from_text([], "streaming")
        normalized["on_prem_connectivity"] = self._components_from_text(normalized["stage1"].get("external_connections", []), "external")
        normalized["high_availability_pattern"] = self._components_from_text(
            [normalized["stage2"].get("architecture_pattern", {}).get("high_availability_mechanism", "")],
            "ha_pattern",
        )
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
            if normalized[key]:
                continue
            items = payload.get(key) if isinstance(payload.get(key), list) else []
            normalized[key] = [self._normalize_component(item) for item in items]

        normalized["relationships"] = [
            self._normalize_relationship({"source": "unknown", "target": "unknown", "direction": "unknown", "connection_type": conn, "tier": "unknown", "confidence": normalized["stage2"]["confidence_assessment"]["overall_confidence"]})
            for conn in normalized["stage1"].get("connections", [])
            if isinstance(conn, str) and conn.strip()
        ]

        normalized["confidence_scores"] = self._build_confidence_from_components(normalized)

        if normalized["stage2"]["confidence_assessment"]["overall_confidence"]:
            normalized["confidence_scores"].append(
                {
                    "component": "overall_diagram",
                    "confidence": self._normalize_confidence(normalized["stage2"]["confidence_assessment"]["overall_confidence"]),
                }
            )

        return normalized

    def _normalize_stage1(self, stage1: Dict[str, Any]) -> Dict[str, List[str]]:
        keys = list(self.OUTPUT_TEMPLATE["stage1"].keys())
        normalized: Dict[str, List[str]] = {}
        for key in keys:
            values = stage1.get(key) if isinstance(stage1.get(key), list) else []
            normalized[key] = [str(v).strip() for v in values if str(v).strip()]
        return normalized

    def _normalize_stage2(self, stage2: Dict[str, Any]) -> Dict[str, Any]:
        base = json.loads(json.dumps(self.OUTPUT_TEMPLATE["stage2"]))
        pattern = stage2.get("architecture_pattern") if isinstance(stage2.get("architecture_pattern"), dict) else {}
        base["architecture_pattern"] = {
            "multi_region": bool(pattern.get("multi_region", False)),
            "active_active": bool(pattern.get("active_active", False)),
            "active_passive": bool(pattern.get("active_passive", False)),
            "disaster_recovery_mechanism": str(pattern.get("disaster_recovery_mechanism") or "").strip(),
            "high_availability_mechanism": str(pattern.get("high_availability_mechanism") or "").strip(),
        }
        for layer in ["compute_layer", "application_layer", "database_layer", "storage_layer", "networking_layer", "security_layer"]:
            values = stage2.get(layer) if isinstance(stage2.get(layer), list) else []
            base[layer] = [str(v).strip() for v in values if str(v).strip()]
        base["traffic_flow_summary"] = str(stage2.get("traffic_flow_summary") or "").strip()
        confidence = stage2.get("confidence_assessment") if isinstance(stage2.get("confidence_assessment"), dict) else {}
        base["confidence_assessment"] = {
            "diagram_clarity": self._normalize_confidence(confidence.get("diagram_clarity")),
            "component_identification_confidence": self._normalize_confidence(confidence.get("component_identification_confidence")),
            "overall_confidence": self._normalize_confidence(confidence.get("overall_confidence")),
            "reason": str(confidence.get("reason") or "").strip(),
        }
        return base

    def _components_from_text(self, values: List[str], label: str) -> List[Dict[str, Any]]:
        confidence = "low"
        return [
            {"name": value, "label": label, "details": "", "confidence": confidence}
            for value in values
            if isinstance(value, str) and value.strip()
        ]

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
