"""FastAPI entrypoint for SoW generation."""

from __future__ import annotations

import logging
import json
import os
import imghdr
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
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




def _sanitize_validation_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return f"<binary data, {len(value)} bytes>"
    if isinstance(value, dict):
        return {str(k): _sanitize_validation_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_validation_value(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    sanitized_errors: list[dict[str, Any]] = []
    for raw_error in exc.errors():
        item = dict(raw_error)
        if "input" in item:
            item["input"] = _sanitize_validation_value(item.get("input"))
        if "ctx" in item:
            item["ctx"] = _sanitize_validation_value(item.get("ctx"))
        sanitized_errors.append(_sanitize_validation_value(item))
    logger.warning("Request validation failed on %s", request.url.path)
    return JSONResponse(status_code=422, content={"detail": sanitized_errors})


@app.get("/")
def root() -> dict[str, str]:
    """Health endpoint for proxy roots."""
    return {"status": "ok"}

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




def _analyze_uploaded_image(file_name: str, content: bytes, diagram_role: str) -> dict[str, Any]:
    fmt = (imghdr.what(None, h=content) or "unknown").lower()
    analysis = {
        "diagram_role": diagram_role,
        "file_name": file_name,
        "format": fmt,
        "size_bytes": len(content),
    }

    lower_name = (file_name or "").lower()
    inferred_components: list[str] = []
    for token, label in [
        ("oke", "OKE"),
        ("k8s", "Kubernetes"),
        ("kubernetes", "Kubernetes"),
        ("mysql", "MySQL"),
        ("postgres", "PostgreSQL"),
        ("lb", "Load Balancer"),
        ("drg", "DRG"),
        ("vpn", "VPN"),
    ]:
        if token in lower_name and label not in inferred_components:
            inferred_components.append(label)

    analysis["inferred_components"] = inferred_components
    return analysis


def _inject_diagram_analysis_context(section: str, section_content: str, context: dict[str, Any]) -> str:
    architecture = context.get("architecture_analysis") if isinstance(context.get("architecture_analysis"), dict) else {}
    current = architecture.get("current") if isinstance(architecture.get("current"), dict) else None
    target = architecture.get("target") if isinstance(architecture.get("target"), dict) else None

    notes: list[str] = []
    upper = section.upper()
    if "CURRENT STATE ARCHITECTURE" in upper and current:
        notes.append(
            f"Current diagram analyzed: file={current.get('file_name')}, format={current.get('format')}, size={current.get('size_bytes')} bytes."
        )
        components = current.get("inferred_components") or []
        if components:
            notes.append(f"Current diagram inferred components: {', '.join(components)}.")
    if "FUTURE STATE ARCHITECTURE" in upper and target:
        notes.append(
            f"Target diagram analyzed: file={target.get('file_name')}, format={target.get('format')}, size={target.get('size_bytes')} bytes."
        )
        components = target.get("inferred_components") or []
        if components:
            notes.append(f"Target diagram inferred components: {', '.join(components)}.")

    if not notes:
        return section_content
    return section_content.rstrip() + "\n\nDiagram analysis evidence:\n- " + "\n- ".join(notes)

@app.post("/generate-sow", response_model=SowOutput)
async def generate_sow(
    request: Request,
    project_data: str | None = Form(None),
    current_architecture_image: UploadFile | None = File(None),
    target_architecture_image: UploadFile | None = File(None),
) -> SowOutput:
    """Generate SoW DOCX and Markdown files using deterministic section orchestration."""
    writer = WriterAgent()
    qa = QAAgent()

    try:
        content_type = (request.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            body = await request.json()
            payload_model = SowInput(**body)
        else:
            payload_raw = project_data
            if (payload_raw is None or not payload_raw.strip()) and hasattr(request, "form"):
                form = await request.form()
                payload_raw = str(form.get("project_data") or "")
            if not payload_raw:
                raise HTTPException(status_code=400, detail="project_data is required")
            payload_model = SowInput(**json.loads(payload_raw))

        context: dict[str, Any] = payload_model.model_dump()

        architecture_analysis: dict[str, Any] = {}
        if current_architecture_image and current_architecture_image.filename:
            current_bytes = await current_architecture_image.read()
            architecture_analysis["current"] = _analyze_uploaded_image(current_architecture_image.filename, current_bytes, "current")
            await current_architecture_image.seek(0)
        if target_architecture_image and target_architecture_image.filename:
            target_bytes = await target_architecture_image.read()
            architecture_analysis["target"] = _analyze_uploaded_image(target_architecture_image.filename, target_bytes, "target")
            await target_architecture_image.seek(0)
        if architecture_analysis:
            context["architecture_analysis"] = architecture_analysis

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

            section_content = _inject_diagram_analysis_context(section, section_content, context)
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
