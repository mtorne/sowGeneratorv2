from __future__ import annotations

import json

from app.agents.architecture_vision import ArchitectureVisionAgent, _ImageMetadata


class _MockMMClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = iter(responses)

    def multimodal_completion(self, prompt: str, image_base64: str, mime_type: str, **kwargs: object) -> str:
        return next(self._responses)


def test_architecture_vision_agent_returns_structured_output(monkeypatch) -> None:
    payload = {
        "diagram_summary": {"diagram_type": "oci", "scope": "regional", "primary_intent": "ha"},
        "components": {"compute": ["OKE"], "kubernetes": [], "databases": [], "networking": [], "load_balancers": [], "security": [], "storage": [], "streaming": [], "on_prem_connectivity": []},
        "relationships": [],
        "high_availability_pattern": {"multi_ad": True, "multi_region": False, "active_active": False, "active_passive": True, "dr_mechanism": "Data Guard"},
        "confidence_assessment": {"diagram_clarity": "high", "component_identification_confidence": "medium", "overall_confidence": "medium", "reason": "Readable labels."},
    }
    agent = ArchitectureVisionAgent(llm_client=_MockMMClient([json.dumps(payload)]))
    monkeypatch.setattr(agent, "_read_image_metadata", lambda **_: _ImageMetadata(width=1200, height=800, fmt="png", mime_type="image/png"))

    result = agent.analyze(file_name="arch.png", content=b"abc", diagram_role="current")

    assert result["format"] == "png"
    assert result["image_resolution"] == {"width": 1200, "height": 800}
    assert result["analysis_confidence"]["overall_confidence"] == "medium"
    assert result["architecture_extraction"]["components"]["compute"] == ["OKE"]


def test_architecture_vision_agent_retries_on_low_confidence(monkeypatch) -> None:
    low = {
        "confidence_assessment": {
            "diagram_clarity": "low",
            "component_identification_confidence": "low",
            "overall_confidence": "low",
            "reason": "blurry",
        }
    }
    high = {
        "diagram_summary": {"diagram_type": "oci", "scope": "regional", "primary_intent": "ha"},
        "components": {"compute": [], "kubernetes": [], "databases": [], "networking": [], "load_balancers": [], "security": [], "storage": [], "streaming": [], "on_prem_connectivity": []},
        "relationships": [],
        "high_availability_pattern": {"multi_ad": False, "multi_region": False, "active_active": False, "active_passive": False, "dr_mechanism": ""},
        "confidence_assessment": {"diagram_clarity": "high", "component_identification_confidence": "high", "overall_confidence": "high", "reason": "clear"},
    }
    agent = ArchitectureVisionAgent(llm_client=_MockMMClient([json.dumps(low), json.dumps(high)]), low_confidence_retries=1)
    monkeypatch.setattr(agent, "_read_image_metadata", lambda **_: _ImageMetadata(width=1200, height=800, fmt="png", mime_type="image/png"))

    result = agent.analyze(file_name="arch.png", content=b"abc", diagram_role="target")

    assert result["analysis_confidence"]["overall_confidence"] == "high"
