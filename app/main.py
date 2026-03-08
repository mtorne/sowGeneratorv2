"""FastAPI entrypoint for SoW generation."""

from __future__ import annotations

import asyncio
import logging
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.agents.architecture_vision import ArchitectureVisionAgent
from app.agents.metadata_inference import MetadataInferenceAgent
from app.agents.qa import QAAgent
from app.agents.structure_controller import StructureController
from app.agents.writer import WriterAgent
from app.services.doc_builder import DocumentBuilder
from app.services.oci_multimodal import OCIClient
from app.services.rag_service import SectionAwareRAGService, SectionChunk

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


@app.on_event("startup")
async def _log_startup_version() -> None:
    """Log the running git commit and active feature flags on every startup.

    This makes it immediately visible in the log which code version is
    deployed — avoids confusion when changes have been made locally but
    the server has not been restarted / pulled yet.
    """
    import subprocess
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        commit = "unknown"

    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        branch = "unknown"

    # Import feature-flag constants so the log shows exactly what is active.
    from app.services.doc_builder import (
        _FULL_CLEAR_SECTIONS,
        _SINGLE_SENTENCE_SECTIONS,
        _HIERARCHICAL_BULLET_SECTIONS,
        _CUSTOMER_PREFIX_SUFFIXES,
    )

    logger.info(
        "startup.version commit=%s branch=%s app_version=%s",
        commit, branch, app.version,
    )
    logger.info(
        "startup.features "
        "full_clear_sections=%s "
        "single_sentence_sections=%s "
        "hierarchical_sections=%s "
        "customer_suffix_count=%d "
        "agreement_between_fix=%s "
        "fldSimple_pass2_fix=True",
        sorted(_FULL_CLEAR_SECTIONS),
        sorted(_SINGLE_SENTENCE_SECTIONS),
        sorted(_HIERARCHICAL_BULLET_SECTIONS),
        len(_CUSTOMER_PREFIX_SUFFIXES),
        "agreement between " in _CUSTOMER_PREFIX_SUFFIXES,
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

# Maximum number of concurrent OCI RAG calls during parallel fan-out.
# Keeps concurrency below OCI rate-limit thresholds while still delivering
# a significant speedup over fully sequential retrieval.
_RAG_CONCURRENCY = int(os.getenv("RAG_CONCURRENCY", "4"))


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


def _build_multimodal_client() -> OCIClient | None:
    try:
        return OCIClient()
    except Exception:
        logger.exception("Failed to initialize OCI multimodal client")
        return None


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


def _load_fallback_context(project_root: Path, section: str) -> SectionChunk | None:
    """Load a static fallback template as a synthetic SectionChunk.

    Returns None if no fallback file exists for the given section so callers
    can degrade gracefully.  Fallback files live under
    ``app/templates/fallback_sections/<section_slug>.md``.
    """
    slug = section.lower().replace(" ", "_") + ".md"
    fallback_path = project_root / "templates" / "fallback_sections" / slug
    if not fallback_path.exists():
        return None
    text = fallback_path.read_text(encoding="utf-8").strip()
    logger.info("workflow.fallback_loaded section=%s file=%s", section, slug)
    return SectionChunk(section=section, text=text)


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


def _diagram_analysis_notes(section: str, context: dict[str, Any]) -> str:
    """Build a short prose summary of diagram analysis for QA/audit logging only.

    This is intentionally NOT appended to section_content that goes into the
    DOCX — the metadata must stay out of the final document.  Call this only
    when you need a human-readable summary for logging or the QA pass.
    """
    architecture = context.get("architecture_analysis") if isinstance(context.get("architecture_analysis"), dict) else {}
    current = architecture.get("current") if isinstance(architecture.get("current"), dict) else None
    target = architecture.get("target") if isinstance(architecture.get("target"), dict) else None

    notes: list[str] = []
    upper = section.upper()
    if "CURRENT STATE ARCHITECTURE" in upper and current:
        components = current.get("inferred_components") or []
        if components:
            notes.append(f"Current diagram inferred components: {', '.join(components)}.")
        notes.append(f"Current diagram confidence: {current.get('analysis_confidence', 'low')}.")
    if "FUTURE STATE ARCHITECTURE" in upper and target:
        components = target.get("inferred_components") or []
        if components:
            notes.append(f"Target diagram inferred components: {', '.join(components)}.")
        notes.append(f"Target diagram confidence: {target.get('analysis_confidence', 'low')}.")
    return " ".join(notes)


async def _run_sow_pipeline(
    context: dict[str, Any],
    current_architecture_images: list[UploadFile],
    target_architecture_images: list[UploadFile],
    project_root: Path,
) -> tuple[list[tuple[str, str]], str, dict[str, list[tuple[str, bytes]]]]:
    """Core SoW generation pipeline shared by all generation endpoints.

    Args:
        context: Project context dict (client, project_name, cloud, scope, …).
            **Mutated in-place** to add ``architecture_analysis`` and
            ``inferred_metadata`` keys during execution.
        current_architecture_images: Uploaded current-state diagram files.
        target_architecture_images: Uploaded target-state diagram files.
        project_root: Absolute path to the ``app/`` directory.

    Returns:
        A 3-tuple of:
        * ``drafted_sections`` – list of (section_name, content) pairs in
          canonical order, ready for DocBuilder injection.
        * ``reviewed`` – assembled + QA-reviewed plain-text document.
        * ``diagram_image_bytes`` – mapping from ``"current"`` / ``"target"``
          to a list of ``(filename, bytes)`` tuples for DOCX image embedding.
    """
    writer = WriterAgent()
    qa = QAAgent()
    architecture_vision = ArchitectureVisionAgent(llm_client=_build_multimodal_client())

    architecture_analysis: dict[str, Any] = {}
    diagram_image_bytes: dict[str, list[tuple[str, bytes]]] = {}
    _vision_inputs: list[tuple] = []
    current_valid = [f for f in (current_architecture_images or []) if f.filename]
    target_valid = [f for f in (target_architecture_images or []) if f.filename]
    if current_valid:
        _vision_inputs.append((current_valid, "current"))
    if target_valid:
        _vision_inputs.append((target_valid, "target"))

    if _vision_inputs:
        _t0_vision = time.monotonic()
        logger.info("workflow.vision_parallel_start diagrams=%d", len(_vision_inputs))

        async def _run_vision(
            upload_files: list, role: str
        ) -> tuple[str, dict[str, Any], list[tuple[str, bytes]]]:
            file_data: list[tuple[str, bytes]] = []
            for uf in upload_files:
                file_bytes = await uf.read()
                await uf.seek(0)
                file_data.append((uf.filename or f"image_{len(file_data)}.png", file_bytes))
            result = await asyncio.to_thread(
                architecture_vision.analyze_many,
                file_data,
                role,
            )
            return role, result, file_data

        vision_results = await asyncio.gather(
            *[_run_vision(ufs, role) for ufs, role in _vision_inputs]
        )
        logger.info(
            "workflow.vision_parallel_complete elapsed=%.1fs diagrams=%d",
            time.monotonic() - _t0_vision,
            len(_vision_inputs),
        )

        for role, result, file_data in vision_results:
            arch_error = result.get("architecture_extraction", {}).get("error")
            if arch_error:
                logger.warning(
                    "ArchitectureVisionAgent (%s) failed — diagram context skipped: %s",
                    role,
                    arch_error.get("message", arch_error),
                )
            else:
                architecture_analysis[role] = result
            # Always retain all image bytes for DOCX placeholder embedding,
            # even when vision analysis fails.
            if file_data:
                diagram_image_bytes[role] = file_data

    if architecture_analysis:
        context["architecture_analysis"] = architecture_analysis

    # ── Metadata inference ──────────────────────────────────────────────
    # LLM call to extract structured customer/project/architecture metadata
    # used to fill Company Profile, App Details, DB Tier, App Tier, and BOM
    # tables in the DOCX.  Runs synchronously (fast, single LLM call).
    logger.info("Swarm flow step: MetadataInferenceAgent")
    _t0_meta = time.monotonic()
    metadata_inference = MetadataInferenceAgent()
    inferred_metadata = await asyncio.to_thread(metadata_inference.infer, context)
    logger.info(
        "workflow.metadata_inference_complete elapsed=%.1fs keys=%s bom=%d",
        time.monotonic() - _t0_meta,
        list(inferred_metadata.keys()),
        len(inferred_metadata.get("oci_bom") or []),
    )
    context["inferred_metadata"] = inferred_metadata

    logger.info("Swarm flow step: ArchitectureContextBuilder")

    structure = StructureController(template_root=project_root / "templates")
    rag_service = SectionAwareRAGService.from_env()

    strict_rag_indexing = os.getenv("RAG_STRICT_INDEXING", "false").casefold() == "true"
    logger.info("workflow.rag_start strict=%s", strict_rag_indexing)
    if strict_rag_indexing:
        indexed_count = rag_service.refresh_from_env()
        logger.info("workflow.rag_count indexed_count=%s", indexed_count)
        if indexed_count == 0:
            raise ValueError("CRITICAL: No documents indexed - cannot generate with RAG")
        diagnostic_ok = rag_service.diagnose_vector_store()
        if not diagnostic_ok:
            raise ValueError("CRITICAL: Vector store empty after indexing")
    else:
        rag_service.clear_cache()
        logger.info("workflow.rag_cache_cleared strict=false skipping count and diagnostic")

    # Phase 2: Fan-out RAG retrieval for all dynamic sections in parallel.
    # OCI KB calls are blocking I/O; asyncio.to_thread runs each in the default
    # thread-pool executor so the event loop stays responsive.  A semaphore caps
    # concurrency at RAG_CONCURRENCY (default 4) to stay within OCI rate limits.
    dynamic_sections = [s for s in structure.sections() if not structure.is_static(s)]
    _t0_rag = time.monotonic()
    logger.info(
        "workflow.rag_parallel_start sections=%d concurrency=%d",
        len(dynamic_sections),
        _RAG_CONCURRENCY,
    )

    _rag_sem = asyncio.Semaphore(_RAG_CONCURRENCY)

    async def _fetch_rag(sec: str) -> tuple[str, list]:
        async with _rag_sem:
            return sec, await asyncio.to_thread(
                rag_service.retrieve_section_context,
                section=sec,
                project_data=context,
            )

    rag_map: dict[str, list] = dict(
        await asyncio.gather(*[_fetch_rag(s) for s in dynamic_sections])
    )
    logger.info(
        "workflow.rag_parallel_complete elapsed=%.1fs sections=%d",
        time.monotonic() - _t0_rag,
        len(dynamic_sections),
    )

    # Extract target diagram components once; passed to WriterAgent for the
    # ARCHITECTURE COMPONENTS section so the LLM uses only real services.
    _target_arch = (
        context.get("architecture_analysis", {})
        .get("target", {})
        .get("architecture_extraction", {})
    )
    _diagram_components: dict | None = _target_arch.get("components") or None

    # Assemble sections in canonical order using pre-fetched RAG context.
    logger.info("Swarm flow step: StructureController")
    drafted_sections: list[tuple[str, str]] = []
    for section in structure.sections():
        if structure.is_static(section):
            logger.info("Swarm flow step: section=%s static template injection", section)
            section_content = structure.inject_template(section)
        else:
            rag_context = rag_map[section]
            logger.info(
                "Swarm flow step: section=%s retrieve_by_section returned %d chunks",
                section,
                len(rag_context),
            )
            # Pass diagram components only for the ARCHITECTURE COMPONENTS section.
            _section_diagram_components = (
                _diagram_components if section == "ARCHITECTURE COMPONENTS" else None
            )
            if len(rag_context) == 0:
                if strict_rag_indexing:
                    logger.error("section=%s ZERO_CHUNKS - Cannot generate accurately", section)
                    section_content = "[ERROR: No relevant documents found - cannot generate this section]"
                else:
                    fallback = _load_fallback_context(project_root, section)
                    if fallback:
                        logger.warning(
                            "section=%s ZERO_CHUNKS - using static fallback as synthetic RAG example",
                            section,
                        )
                        rag_context = [fallback]
                    else:
                        logger.warning(
                            "section=%s ZERO_CHUNKS - generating from context only (no RAG examples)",
                            section,
                        )
                    section_content = writer.write_section(
                        section_name=section,
                        context=context,
                        rag_context=rag_context,
                        disallowed_services=_disallowed_services(context),
                        diagram_components=_section_diagram_components,
                    )
            else:
                disallowed = _disallowed_services(context)
                section_content = writer.write_section(
                    section_name=section,
                    context=context,
                    rag_context=rag_context,
                    disallowed_services=disallowed,
                    diagram_components=_section_diagram_components,
                )

            disallowed = _disallowed_services(context)
            if disallowed:
                mentioned = _mentioned_services(section_content)
                invalid_services = [svc for svc in mentioned if svc in set(disallowed)]
                if invalid_services:
                    logger.warning(
                        "section=%s contains disallowed services despite prompt constraint: %s",
                        section,
                        ", ".join(sorted(invalid_services)),
                    )

        # Log diagram analysis notes for diagnostics — but do NOT append
        # them to section_content; metadata must not appear in the DOCX.
        diag_notes = _diagram_analysis_notes(section, context)
        if diag_notes:
            logger.debug("section=%s diagram_analysis_notes=%s", section, diag_notes)
        drafted_sections.append((section, section_content))

    assembled = _assemble_document(drafted_sections)
    logger.info("Swarm flow step: QAAgent (light validation)")
    reviewed = qa.review_document(assembled)

    return drafted_sections, reviewed, diagram_image_bytes


@app.post("/generate-sow", response_model=SowOutput)
async def generate_sow(
    request: Request,
    project_data: str | None = Form(None),
    current_architecture_images: list[UploadFile] = File(default=[]),
    target_architecture_images: list[UploadFile] = File(default=[]),
    include_architect_review: str | None = Form(None),
) -> SowOutput:
    """Generate SoW DOCX and Markdown files using deterministic section orchestration."""

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
        project_root = Path(__file__).resolve().parent
        _include_review = (include_architect_review or "").lower() in ("true", "1", "yes", "on")

        drafted_sections, reviewed, diagram_image_bytes = await _run_sow_pipeline(
            context, current_architecture_images, target_architecture_images, project_root
        )

        logger.info("Swarm flow step: DocBuilder")
        builder = DocumentBuilder(
            template_path=project_root / "templates" / "sow_template.docx",
            customer_name=context.get("client", ""),
            project_name=context.get("project_name", ""),
        )
        file_name = builder.build(
            sections=drafted_sections,
            output_dir=project_root,
            diagram_images=diagram_image_bytes or None,
            project_context=context,
            include_architect_review=_include_review,
        )
        markdown_name = builder.build_markdown(full_document=reviewed, output_dir=project_root)
        return SowOutput(file=file_name, markdown_file=markdown_name)
    except Exception as exc:
        logger.exception("SoW generation failed")
        raise HTTPException(status_code=500, detail="Failed to generate SoW") from exc


@app.post("/generate-markdown/")
async def generate_markdown(
    customer: str = Form(...),
    application: str = Form(...),
    scope: str = Form(default=""),
    impdetails: str = Form(default=""),
    llm_provider: str = Form(default=""),
    vision_provider: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    current_diagram: list[UploadFile] = File(default=[]),
    target_diagram: list[UploadFile] = File(default=[]),
    include_architect_review: str | None = Form(None),
) -> PlainTextResponse:
    """Frontend-facing endpoint that accepts legacy form fields and returns markdown.

    This endpoint mirrors the old ``backend/main.py`` ``/generate-markdown/`` route
    so the existing frontend can continue using it without changes.  In addition to
    returning the markdown body as ``text/plain``, it also saves both a DOCX and a
    Markdown file under ``app/`` so the user can download them via ``/files/{name}``.

    Form fields
    -----------
    customer : str
        Customer / client name (maps to ``context["client"]``).
    application : str
        Application / project name.
    scope : str
        High-level scope description.
    impdetails : str
        Optional implementation details appended to scope.
    llm_provider / vision_provider : str
        Accepted but ignored — provider selection is handled via environment config.
    file : UploadFile | None
        Optional DOCX template upload (ignored; the server uses its own template).
    current_diagram : list[UploadFile]
        One or more current-state architecture diagram images.
    target_diagram : list[UploadFile]
        One or more target-state architecture diagram images.
    include_architect_review : str | None
        Pass ``"true"`` / ``"1"`` / ``"yes"`` to include the Architect Review section
        in the generated document.  Defaults to excluded (customer-facing mode).
    """
    try:
        # Build a SoW context dict from the legacy form fields.
        full_scope = scope.strip()
        if impdetails.strip():
            full_scope = f"{full_scope}\n\nImplementation Details:\n{impdetails.strip()}".strip()
        if not full_scope:
            full_scope = "To be defined."

        context: dict[str, Any] = {
            "client": customer,
            "project_name": application,
            "cloud": "OCI",
            "scope": full_scope,
            "duration": "PENDING TO REVIEW",
            "industry": None,
            "services": [],
        }

        project_root = Path(__file__).resolve().parent
        _include_review = (include_architect_review or "").lower() in ("true", "1", "yes", "on")

        drafted_sections, reviewed, diagram_image_bytes = await _run_sow_pipeline(
            context, current_diagram, target_diagram, project_root
        )

        logger.info("Swarm flow step: DocBuilder (markdown endpoint)")
        builder = DocumentBuilder(
            template_path=project_root / "templates" / "sow_template.docx",
            customer_name=customer,
            project_name=application,
        )
        builder.build(
            sections=drafted_sections,
            output_dir=project_root,
            diagram_images=diagram_image_bytes or None,
            project_context=context,
            include_architect_review=_include_review,
        )
        builder.build_markdown(full_document=reviewed, output_dir=project_root)

        return PlainTextResponse(content=reviewed)
    except Exception as exc:
        logger.exception("SoW generation (markdown endpoint) failed")
        raise HTTPException(status_code=500, detail="Failed to generate SoW") from exc
