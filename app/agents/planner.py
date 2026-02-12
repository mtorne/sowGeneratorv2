"""Planner agent for section planning."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services.llm import call_llm

logger = logging.getLogger(__name__)

_DEFAULT_SECTIONS = [
    "Executive Summary",
    "Scope",
    "Architecture",
    "Timeline",
    "Assumptions",
    "Risks",
]


class PlannerAgent:
    """Generates section plans for SoW documents."""

    def plan_sections(self, context: dict[str, Any]) -> list[str]:
        """Generate structured section names from project context."""
        system_prompt = (
            "You are a planning agent for consulting Statements of Work. "
            "Return only valid JSON with this exact schema: "
            '{"sections": ["Section Name"]}. Keep section titles concise.'
        )
        user_prompt = f"Project input JSON:\n{json.dumps(context, ensure_ascii=False)}"

        raw_output = call_llm(system_prompt=system_prompt, user_prompt=user_prompt)
        parsed = self._parse_sections(raw_output)
        if parsed:
            return parsed
        logger.warning("Planner produced invalid output. Falling back to default sections")
        return _DEFAULT_SECTIONS.copy()

    def _parse_sections(self, text: str) -> list[str]:
        """Parse and validate planner JSON output."""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                return []
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return []

        sections = payload.get("sections") if isinstance(payload, dict) else None
        if not isinstance(sections, list):
            return []
        clean_sections = [str(section).strip() for section in sections if str(section).strip()]
        return clean_sections
