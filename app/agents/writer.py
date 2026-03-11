"""Writer agent for SoW section drafting."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.services.llm import call_llm
from app.services.rag_service import SectionChunk

logger = logging.getLogger(__name__)

# Sections that are prompted for structured JSON output instead of prose.
# For these sections the LLM is asked to return a JSON object matching the
# schema defined in app/models/section_outputs.py; doc_builder then renders
# the parsed dict directly rather than parsing free-form text.
STRUCTURED_OUTPUT_SECTIONS: frozenset[str] = frozenset({
    "MILESTONE PLAN",
    "HIGH AVAILABILITY",
    "BACKUP STRATEGY",
    "DISASTER RECOVERY",
})

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

        # Structured-output mode: for designated sections the LLM is asked to
        # return a JSON object instead of prose.  Affects both the system-prompt
        # OUTPUT FORMAT block and the section-specific schema instruction.
        json_output = section_name.upper() in STRUCTURED_OUTPUT_SECTIONS

        system_prompt = env.get_template("writer_system.j2").render(
            disallowed_services=disallowed_services or [],
            json_output=json_output,
        ).strip()

        user_prompt = env.get_template("writer_user.j2").render(
            section_name=section_name,
            context_json=json.dumps(context, ensure_ascii=False),
            examples=examples,
            diagram_components=diagram_components,
            json_output=json_output,
        ).strip()

        logger.debug(
            "writer.render_prompts section=%s system_len=%d user_len=%d json_output=%s",
            section_name,
            len(system_prompt),
            len(user_prompt),
            json_output,
        )

        raw = call_llm(system_prompt=system_prompt, user_prompt=user_prompt).strip()

        logger.info(
            "writer.llm_output section=%s len=%d preview=%r",
            section_name,
            len(raw),
            raw[:300],
        )

        if json_output:
            # Strip optional markdown code-fence wrappers that some models add.
            # e.g.  ```json\n{...}\n```  →  {...}
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
            raw = raw.strip()
            logger.debug("writer.json_output section=%s len=%d", section_name, len(raw))

        return raw
