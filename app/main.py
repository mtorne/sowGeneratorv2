"""FastAPI entrypoint for SoW generation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agents.planner import PlannerAgent
from app.agents.qa import QAAgent
from app.agents.writer import WriterAgent
from app.services.doc_builder import DocumentBuilder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Swarm SoW Generator", version="0.1.0")


class SowInput(BaseModel):
    """Input payload for SoW generation."""

    client: str = Field(..., min_length=1)
    project_name: str = Field(..., min_length=1)
    cloud: str = Field(..., min_length=1)
    scope: str = Field(..., min_length=1)
    duration: str = Field(..., min_length=1)


class SowOutput(BaseModel):
    """Output payload for generated SoW file."""

    file: str


def _assemble_document(sections: list[tuple[str, str]]) -> str:
    """Convert section tuples into a single markdown-like text body."""
    chunks: list[str] = []
    for title, body in sections:
        chunks.append(f"{title}\n{'-' * len(title)}\n{body.strip()}\n")
    return "\n".join(chunks).strip()


@app.get("/health")
def health() -> dict[str, str]:
    """Basic health endpoint."""
    return {"status": "ok"}


@app.post("/generate-sow", response_model=SowOutput)
def generate_sow(payload: SowInput) -> SowOutput:
    """Generate a Statement of Work DOCX file with manual multi-agent orchestration."""
    planner = PlannerAgent()
    writer = WriterAgent()
    qa = QAAgent()

    context: dict[str, Any] = payload.model_dump()

    try:
        sections = planner.plan_sections(context)
        drafted_sections: list[tuple[str, str]] = []
        for section in sections:
            section_content = writer.write_section(section_name=section, context=context)
            drafted_sections.append((section, section_content))

        assembled = _assemble_document(drafted_sections)
        reviewed = qa.review_document(assembled)

        project_root = Path(__file__).resolve().parent
        builder = DocumentBuilder(template_path=project_root / "templates" / "sow_template.docx")
        file_name = builder.build(full_document=reviewed, output_dir=project_root)
        return SowOutput(file=file_name)
    except Exception as exc:
        logger.exception("SoW generation failed")
        raise HTTPException(status_code=500, detail="Failed to generate SoW") from exc
