"""Lightweight architecture vision agent for uploaded diagrams."""

from __future__ import annotations

import imghdr
from typing import Any


class ArchitectureVisionAgent:
    """Extracts deterministic evidence from an uploaded architecture diagram."""

    _TOKEN_MAP = {
        "oke": "OKE",
        "k8s": "Kubernetes",
        "kubernetes": "Kubernetes",
        "mysql": "MySQL",
        "postgres": "PostgreSQL",
        "adb": "Autonomous Database",
        "autonomous": "Autonomous Database",
        "lb": "Load Balancer",
        "load-balancer": "Load Balancer",
        "drg": "DRG",
        "vpn": "VPN",
        "waf": "WAF",
        "vault": "Vault",
        "api-gateway": "API Gateway",
        "stream": "Streaming",
    }

    def analyze(self, file_name: str, content: bytes, diagram_role: str) -> dict[str, Any]:
        fmt = (imghdr.what(None, h=content) or "unknown").lower()
        inferred_components = self._infer_components(file_name=file_name)
        return {
            "diagram_role": diagram_role,
            "file_name": file_name,
            "format": fmt,
            "size_bytes": len(content),
            "inferred_components": inferred_components,
            "analysis_confidence": self._confidence_for(fmt=fmt, size_bytes=len(content), components=inferred_components),
        }

    def _infer_components(self, file_name: str) -> list[str]:
        lowered = (file_name or "").casefold()
        found: list[str] = []
        for token, label in self._TOKEN_MAP.items():
            if token in lowered and label not in found:
                found.append(label)
        return found

    @staticmethod
    def _confidence_for(fmt: str, size_bytes: int, components: list[str]) -> str:
        if size_bytes <= 0:
            return "low"
        if fmt == "unknown" and not components:
            return "low"
        if components:
            return "medium"
        return "low"

