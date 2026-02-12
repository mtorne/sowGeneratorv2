"""FastAPI entrypoint for SoW generation."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agents.qa import QAAgent
from app.agents.structure_controller import StructureController
from app.agents.writer import WriterAgent
from app.services.doc_builder import DocumentBuilder
from app.services.rag_service import SectionAwareRAGService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Swarm SoW Generator", version="0.2.0")

_allowed_origins = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

KNOWN_SERVICES = {
    "oke",
    "mysql",
    "streaming",
    "object storage",
    "compute",
    "load balancer",
    "autonomous database",
    "api gateway",
    "vault",
    "waf",
}



class ServiceValidationError(RuntimeError):
    """Raised when generated output includes services outside explicit allow-list."""

KNOWN_SERVICES = {
    "oke",
    "mysql",
    "streaming",
    "object storage",
    "compute",
    "load balancer",
    "autonomous database",
    "api gateway",
    "vault",
    "waf",
}


class SowInput(BaseModel):
    """Input payload for SoW generation."""

    client: str = Field(..., min_length=1)
    project_name: str = Field(..., min_length=1)
    cloud: str = Field(..., min_length=1)
    scope: str = Field(..., min_length=1)
    duration: str = Field(..., min_length=1)
    industry: str | None = None
    services: list[str] = Field(default_factory=list)


class SowOutput(BaseModel):
    """Output payload for generated SoW files."""

    file: str
    markdown_file: str


def _assemble_document(sections: list[tuple[str, str]]) -> str:
    """Convert section tuples into a single markdown-like text body."""
    chunks: list[str] = []
    for title, body in sections:
        chunks.append(f"{title}\n{'-' * len(title)}\n{body.strip()}\n")
    return "\n".join(chunks).strip()


def _resolve_generated_file(file_name: str) -> Path:
    """Resolve generated output file under app root and prevent path traversal."""
    if not file_name or "/" in file_name or "\\" in file_name:
        raise HTTPException(status_code=400, detail="Invalid file name")

    app_root = Path(__file__).resolve().parent
    file_path = (app_root / file_name).resolve()
    if file_path.parent != app_root:
        raise HTTPException(status_code=400, detail="Invalid file location")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return file_path


def _mentioned_services(text: str) -> set[str]:
    lowered = text.casefold()
    return {service for service in KNOWN_SERVICES if re.search(rf"\b{re.escape(service)}\b", lowered)}


def _allowed_services(context: dict[str, Any]) -> set[str]:
    """Normalize allowed services explicitly provided in request payload."""
    return {s.casefold() for s in context.get("services", []) if isinstance(s, str) and s.strip()}


def _disallowed_services(context: dict[str, Any]) -> list[str]:
    """Return disallowed services only when caller explicitly supplies allowed services."""
    allowed = _allowed_services(context)
    if not allowed:
        return []
    return sorted(service for service in KNOWN_SERVICES if service not in allowed)


@app.get("/health")
def health() -> dict[str, str]:
    """Basic health endpoint."""
    return {"status": "ok"}


@app.get("/files/{file_name}")
def download_generated_file(file_name: str) -> FileResponse:
    """Download a generated SoW output file."""
    file_path = _resolve_generated_file(file_name)
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if file_path.suffix.lower() == ".md":
        media_type = "text/markdown"
    return FileResponse(path=str(file_path), filename=file_path.name, media_type=media_type)


@app.post("/generate-sow", response_model=SowOutput)
def generate_sow(payload: SowInput) -> SowOutput:
    """Generate SoW DOCX and Markdown files using deterministic section orchestration."""
    writer = WriterAgent()
    qa = QAAgent()

    context: dict[str, Any] = payload.model_dump()

    try:
        project_root = Path(__file__).resolve().parent
        structure = StructureController(template_root=project_root / "templates")
        rag_service = SectionAwareRAGService.from_env()

        drafted_sections: list[tuple[str, str]] = []
        for section in structure.sections():
            if structure.is_static(section):
                section_content = structure.inject_template(section)
            else:
                rag_context = rag_service.retrieve_section_context(section=section, project_data=context)
                disallowed = _disallowed_services(context)
                section_content = writer.write_section(
                    section_name=section,
                    context=context,
                    rag_context=rag_context,
                    disallowed_services=disallowed,
                )

                if disallowed:
                    mentioned = _mentioned_services(section_content)
                    invalid_services = [svc for svc in mentioned if svc in set(disallowed)]
                    if invalid_services:
                        raise ServiceValidationError(
                            f"Disallowed services in {section}: {', '.join(sorted(invalid_services))}"
                        )

            drafted_sections.append((section, section_content))

        assembled = _assemble_document(drafted_sections)
        reviewed = qa.review_document(assembled)

        builder = DocumentBuilder(template_path=project_root / "templates" / "sow_template.docx")
        file_name = builder.build(full_document=reviewed, output_dir=project_root)
        markdown_name = builder.build_markdown(full_document=reviewed, output_dir=project_root)
        return SowOutput(file=file_name, markdown_file=markdown_name)
    except ServiceValidationError as exc:
        logger.warning("SoW generation failed validation: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("SoW generation failed")
        raise HTTPException(status_code=500, detail="Failed to generate SoW") from exc
