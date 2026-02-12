"""Writer agent for SoW section drafting."""

from __future__ import annotations

import json
from typing import Any

from app.services.llm import call_llm


class WriterAgent:
    """Generates section-level SoW content."""

    def write_section(self, section_name: str, context: dict[str, Any]) -> str:
        """Create a section body in professional consulting style."""
        system_prompt = (
            "You are a senior enterprise consulting writer. Produce technically precise, "
            "formal, and concise SoW content. Output plain text only."
        )
        user_prompt = (
            f"Section: {section_name}\n"
            f"Project input JSON:\n{json.dumps(context, ensure_ascii=False)}\n\n"
            "Write only the requested section content."
        )
        return call_llm(system_prompt=system_prompt, user_prompt=user_prompt).strip()
