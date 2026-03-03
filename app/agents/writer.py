"""Writer agent for SoW section drafting."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.llm import call_llm
from app.services.rag_service import SectionChunk

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "templates" / "prompts"


def _jinja_env():  # type: ignore[return]
    """Return a Jinja2 environment pointed at the prompts directory.

    Import is deferred so a missing jinja2 install raises at call time
    (with a clear message) rather than crashing the entire app at startup.
    """
    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "jinja2 is required but not installed — run: pip install jinja2"
        ) from exc

    return Environment(
        loader=FileSystemLoader(str(_PROMPTS_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


class WriterAgent:
    """Generates section-level SoW content."""

    def write_section(
        self,
        section_name: str,
        context: dict[str, Any],
        rag_context: list[SectionChunk] | None = None,
        disallowed_services: list[str] | None = None,
        diagram_components: dict | None = None,
    ) -> str:
        """Create a section body in professional consulting style.

        Args:
            diagram_components: Structured components dict extracted from the target
                architecture diagram analysis (ArchitectureVisionAgent output). When
                provided for the ARCHITECTURE COMPONENTS section the LLM is instructed
                to use only the real services identified in the diagram rather than
                generating generic descriptions.
        """
        examples = "\n\n".join(
            f"Reference Example {idx}:\n{chunk.text}"
            for idx, chunk in enumerate(rag_context or [], start=1)
        )

        env = _jinja_env()

        system_prompt = env.get_template("writer_system.j2").render(
            disallowed_services=disallowed_services or [],
        ).strip()

        user_prompt = env.get_template("writer_user.j2").render(
            section_name=section_name,
            context_json=json.dumps(context, ensure_ascii=False),
            examples=examples,
            diagram_components=diagram_components,
        ).strip()

        logger.debug(
            "writer.render_prompts section=%s system_len=%d user_len=%d",
            section_name,
            len(system_prompt),
            len(user_prompt),
        )

        return call_llm(system_prompt=system_prompt, user_prompt=user_prompt).strip()
