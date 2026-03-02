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

        system_parts = [
            "You are a senior Oracle Cloud Solution Architect writing a formal Statement of Work. "
            "Follow the established enterprise tone and structure. Reuse phrasing when appropriate "
            "but adapt to project specifics. Do not invent services not present in project data.",
        ]
        if disallowed_services:
            system_parts.append(
                "IMPORTANT: You MUST NOT mention, reference, or recommend the following OCI services "
                f"anywhere in your output: {', '.join(disallowed_services)}. "
                "If any of these services appear in reference examples, ignore them entirely."
            )
        system_prompt = " ".join(system_parts)

        user_prompt = (
            f"Write section: {section_name}\n"
            "Maintain similar structure as reference examples.\n\n"
            f"Project data JSON:\n{json.dumps(context, ensure_ascii=False)}\n\n"
            f"Retrieved similar section examples:\n{examples or 'No retrieved examples available.'}\n\n"
            "Output only the section content."
        )
        return call_llm(system_prompt=system_prompt, user_prompt=user_prompt).strip()
