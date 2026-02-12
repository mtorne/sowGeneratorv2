"""Writer agent for SoW section drafting."""

from __future__ import annotations

import json
from typing import Any

from app.services.llm import call_llm
from app.services.rag_service import SectionChunk


class WriterAgent:
    """Generates section-level SoW content."""

    def write_section(
        self,
        section_name: str,
        context: dict[str, Any],
        rag_context: list[SectionChunk] | None = None,
        disallowed_services: list[str] | None = None,
    ) -> str:
        """Create a section body in professional consulting style."""
        examples = "\n\n".join(
            f"Reference Example {idx}:\n{chunk.text}"
            for idx, chunk in enumerate(rag_context or [], start=1)
        )
        disallowed_note = ""
        if disallowed_services:
            disallowed_note = f"Forbidden services: {', '.join(disallowed_services)}."

        system_prompt = (
            "You are a senior Oracle Cloud Solution Architect writing a formal Statement of Work. "
            "Follow the established enterprise tone and structure. Reuse phrasing when appropriate "
            "but adapt to project specifics. Do not invent services not present in project data."
        )
        user_prompt = (
            f"Write section: {section_name}\n"
            "Maintain similar structure as reference examples.\n\n"
            f"Project data JSON:\n{json.dumps(context, ensure_ascii=False)}\n\n"
            f"Retrieved similar section examples:\n{examples or 'No retrieved examples available.'}\n\n"
            f"{disallowed_note}\n"
            "Output only the section content."
        )
        return call_llm(system_prompt=system_prompt, user_prompt=user_prompt).strip()
