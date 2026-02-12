from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
import logging
from config.settings import app_config
from services.content_generator import ContentGeneratorService
from services.oci_rag_client import OCIRAGService
from services.document_service import DocumentService
from services.sow_workflow_service import (
    SOWWorkflowService,
    WorkflowStage,
    ValidationError as WorkflowValidationError,
    StateTransitionError,
)
from utils.response_formatter import ResponseFormatter
from fastapi.responses import PlainTextResponse
from typing import Optional
from pydantic import BaseModel
from fastapi.exceptions import RequestValidationError
import mimetypes
from typing import Dict, Any
from agents.architecture_vision_agent import ArchitectureVisionAgent
from services.architecture_context_builder import ArchitectureContextBuilder
from services.section_writer import SectionWriter
from services.architecture_guardrails import ArchitectureGuardrails
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === FastAPI Setup ===
app = FastAPI(
    title="OCI Document Generator",
    description="Generate technical documents using OCI Grok AI",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=app_config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def unicode_decode_protection_middleware(request: Request, call_next):
    if request.method in {"GET", "HEAD"} and request.url.path == "/":
        return JSONResponse(
            status_code=200,
            content={"message": "OCI Document Generator API", "version": "2.0.0", "status": "healthy"},
        )

    try:
        return await call_next(request)
    except UnicodeDecodeError as exc:
        logger.error("Unicode decode error while processing request %s: %s", request.url.path, exc)
        return JSONResponse(status_code=400, content={"detail": "Malformed multipart/form-data payload"})



class RAGChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class RAGChatResponse(BaseModel):
    answer: str
    session_id: Optional[str] = None
    citations: list[Dict[str, Any] | str] = []
    guardrail_result: Optional[Any] = None


class SOWCaseCreateRequest(BaseModel):
    client_name: str
    project_scope: str
    document_type: str
    industry: str
    region: str
    delivery_model: Optional[str] = None
    assumptions: Optional[list[str]] = None


class StagePayloadRequest(BaseModel):
    payload: Dict[str, Any] = {}


class RAGQualityPreviewRequest(BaseModel):
    intake: Dict[str, Any]
    sections: list[Dict[str, Any]]
    top_k: int = 5
    include_relaxed: bool = True


# Initialize services
content_generator = ContentGeneratorService()
document_service = DocumentService()
response_formatter = ResponseFormatter()
rag_service = OCIRAGService()
sow_workflow_service = SOWWorkflowService()
architecture_vision_agent = ArchitectureVisionAgent()
architecture_context_builder = ArchitectureContextBuilder()
section_writer = SectionWriter()


def _artifact_response(artifact):
    return {
        "stage": artifact.stage.value,
        "version": artifact.version,
        "created_at": artifact.created_at,
        "payload": artifact.payload,
    }


def _case_response(sow_case):
    return {
        "case_id": sow_case.case_id,
        "created_at": sow_case.created_at,
        "stage": sow_case.stage.value,
        "intake": sow_case.intake,
    }



def _sanitize_for_json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return f"<binary data, {len(value)} bytes>"
    if isinstance(value, dict):
        return {str(k): _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_for_json(item) for item in value]
    return value


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("Request validation failed on %s: %s", request.url.path, exc)
    sanitized_errors = []
    for raw_error in exc.errors():
        error_item = dict(raw_error)
        if "input" in error_item:
            error_item["input"] = _sanitize_for_json(error_item.get("input"))
        if "ctx" in error_item:
            error_item["ctx"] = _sanitize_for_json(error_item.get("ctx"))
        sanitized_errors.append(_sanitize_for_json(error_item))
    return JSONResponse(status_code=422, content={"detail": sanitized_errors})



def _inject_architecture_evidence_if_missing(section_name: str, section_text: str, architecture_context: Dict[str, Any]) -> str:
    text = section_text or ""
    upper = section_name.upper()

    def collect_names(state_key: str, category: str) -> list[str]:
        state = architecture_context.get(state_key, {})
        items = state.get(category, []) if isinstance(state, dict) else []
        out = []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("name"):
                    out.append(str(item.get("name")))
        return out

    if "CURRENT STATE" in upper:
        must_include = collect_names("current_state", "compute")[:3] + collect_names("current_state", "databases")[:3]
    elif "FUTURE STATE" in upper:
        must_include = collect_names("target_state", "compute")[:3] + collect_names("target_state", "databases")[:3]
    elif "TECHNOLOGY STACK" in upper:
        stack = architecture_context.get("technology_stack", {})
        must_include = (stack.get("infrastructure", []) if isinstance(stack, dict) else [])[:3] + (stack.get("database", []) if isinstance(stack, dict) else [])[:3]
    else:
        must_include = collect_names("target_state", "compute")[:3]

    missing = [name for name in must_include if isinstance(name, str) and name and name.lower() not in text.lower()]
    if not missing:
        return text

    evidence = "\n\nEvidence from extracted diagrams: " + ", ".join(missing)
    return text + evidence


# Default template with common placeholders
DEFAULT_TEMPLATE = """

## ISV Details
{{isv_detail}}

## Application Details
{{application_detail}}

## Architecture Deployment Overview
{{ARCH_DEP_OVERVIEW}}

## Implementation Details
{{IMP_DETAILS}}

## Diagram Description
{{DIAGRAM_DESCRIPTION}}

"""

# Helper functions for file validation
def validate_docx_file(file: UploadFile) -> bool:
    """Simple validation for DOCX files"""
    try:
        if not file or not file.filename:
            return False
        
        # Check file extension
        if not file.filename.lower().endswith(('.docx', '.doc')):
            logger.warning(f"File '{file.filename}' does not have .docx extension")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error validating DOCX file: {str(e)}")
        return False

def validate_image_file(file: UploadFile) -> bool:
    """Simple validation for image files"""
    try:
        if not file or not file.filename:
            return False
        
        # Check file extension
        valid_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
        if not file.filename.lower().endswith(valid_extensions):
            logger.warning(f"File '{file.filename}' does not have a valid image extension")
            return False
        
        # Check content type if available
        if hasattr(file, 'content_type') and file.content_type:
            if not file.content_type.startswith('image/'):
                logger.warning(f"File '{file.filename}' has non-image content type: {file.content_type}")
                return False
        
        return True
    except Exception as e:
        logger.error(f"Error validating image file: {str(e)}")
        return False

async def safe_extract_text_from_docx(file: UploadFile):
    """Safely extract text with proper error handling"""
    try:
        # Reset file pointer
        await file.seek(0)
        file_content = await file.read()
        await file.seek(0)
        
        if len(file_content) == 0:
            raise ValueError(f"File '{file.filename}' is empty")
        
        if len(file_content) < 1000:
            raise ValueError(f"File '{file.filename}' is too small to be a valid DOCX file ({len(file_content)} bytes)")
        
        # Try to extract text
        return await document_service.extract_text_from_docx(file)
        
    except Exception as e:
        logger.error(f"Error extracting text from DOCX: {str(e)}")
        raise

async def safe_process_diagram(diagram: UploadFile):
    """Safely process diagram with proper error handling"""
    try:
        # Reset file pointer
        await diagram.seek(0)
        diagram_content = await diagram.read()
        await diagram.seek(0)
        
        if len(diagram_content) == 0:
            raise ValueError(f"Diagram file '{diagram.filename}' is empty")
        
        # Check if it's a valid image by looking at file signature
        image_signatures = {
            b'\xff\xd8\xff': 'image/jpeg',
            b'\x89\x50\x4e\x47': 'image/png',
            b'\x47\x49\x46\x38': 'image/gif',
            b'\x42\x4d': 'image/bmp',
            b'\x52\x49\x46\x46': 'image/webp'
        }
        
        is_valid_image = False
        for signature in image_signatures.keys():
            if diagram_content.startswith(signature):
                is_valid_image = True
                break
        
        if not is_valid_image:
            raise ValueError(f"File '{diagram.filename}' is not a valid image file")
        
        logger.info(f"Processing valid diagram: {diagram.filename}, size: {len(diagram_content)} bytes")
        return await document_service.process_diagram(diagram)
        
    except Exception as e:
        logger.error(f"Error processing diagram: {str(e)}")
        raise

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"message": "OCI Document Generator API", "version": "2.0.0", "status": "healthy"}


@app.get("/health")
async def health():
    """Health check endpoint alias for reverse-proxy prefixes."""
    return {"message": "OCI Document Generator API", "version": "2.0.0", "status": "healthy"}


@app.post("/sow-cases")
async def create_sow_case(request: SOWCaseCreateRequest):
    try:
        sow_case = sow_workflow_service.create_case(request.model_dump())
        return JSONResponse(content={"status": "success", "case": _case_response(sow_case)})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/sow-cases/{case_id}/plan")
async def run_plan(case_id: str, request: StagePayloadRequest):
    try:
        artifact = sow_workflow_service.run_plan(case_id, request.payload)
        return JSONResponse(content={"status": "success", "artifact": _artifact_response(artifact)})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/sow-cases/{case_id}/retrieve")
async def run_retrieve(case_id: str, request: StagePayloadRequest):
    try:
        artifact = sow_workflow_service.run_retrieve(case_id, request.payload)
        return JSONResponse(content={"status": "success", "artifact": _artifact_response(artifact)})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))




@app.post("/rag-quality/preview")
async def rag_quality_preview(request: RAGQualityPreviewRequest):
    try:
        sections = request.sections or []
        if not sections:
            raise WorkflowValidationError("sections must be non-empty")

        reports = []
        for section in sections:
            section_name = section.get("name")
            if not section_name:
                raise WorkflowValidationError("each section requires a name")

            filters = {"section": section_name, **(section.get("clause_filters") or {})}
            filters.setdefault("industry", request.intake.get("industry", "general"))
            filters.setdefault("region", request.intake.get("region", "global"))
            for attr in [
                "deployment_model",
                "data_isolation",
                "cloud_provider",
                "ai_modes",
                "data_flow",
                "compliance_requirements",
            ]:
                if attr not in filters and request.intake.get("structured_context"):
                    filters[attr] = request.intake["structured_context"].get(attr)

            reports.append(
                sow_workflow_service.knowledge_access_service.preview_section_quality(
                    section_name=section_name,
                    filters=filters,
                    intake=request.intake,
                    top_k=request.top_k,
                    include_relaxed=request.include_relaxed,
                )
            )

        quality_rollup = {
            "total_sections": len(reports),
            "strict_with_candidates": sum(1 for item in reports if item["strict"]["candidate_count"] > 0),
            "relaxed_with_candidates": sum(1 for item in reports if (item.get("relaxed") or {}).get("candidate_count", 0) > 0),
            "no_candidates": sum(
                1
                for item in reports
                if item["strict"]["candidate_count"] == 0
                and (item.get("relaxed") or {}).get("candidate_count", 0) == 0
            ),
        }

        return JSONResponse(content={"status": "success", "quality": quality_rollup, "reports": reports})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/sow-cases/{case_id}/assemble")
async def run_assemble(case_id: str):
    try:
        artifact = sow_workflow_service.run_assemble(case_id)
        return JSONResponse(content={"status": "success", "artifact": _artifact_response(artifact)})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/sow-cases/{case_id}/write")
async def run_write(case_id: str, request: StagePayloadRequest):
    try:
        artifact = sow_workflow_service.run_write(case_id, request.payload)
        return JSONResponse(content={"status": "success", "artifact": _artifact_response(artifact)})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/sow-cases/{case_id}/review")
async def run_review(case_id: str, request: StagePayloadRequest):
    try:
        artifact = sow_workflow_service.run_review(case_id, request.payload)
        return JSONResponse(content={"status": "success", "artifact": _artifact_response(artifact)})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/sow-cases/{case_id}/approve")
async def approve_case(case_id: str):
    try:
        sow_case = sow_workflow_service.approve(case_id)
        return JSONResponse(content={"status": "success", "case": _case_response(sow_case)})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/sow-cases/{case_id}")
async def get_case(case_id: str):
    try:
        sow_case = sow_workflow_service.get_case(case_id)
        return JSONResponse(content={"status": "success", "case": _case_response(sow_case)})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/sow-cases/{case_id}/artifacts/{stage}")
async def get_artifact(case_id: str, stage: WorkflowStage):
    try:
        artifact = sow_workflow_service.get_latest_artifact(case_id, stage)
        return JSONResponse(content={"status": "success", "artifact": _artifact_response(artifact)})
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))



@app.get("/sow-cases/{case_id}/document.md")
async def get_case_document_markdown(case_id: str):
    try:
        content = sow_workflow_service.render_document_markdown(case_id)
        return PlainTextResponse(content=content)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/sow-cases/{case_id}/document.html")
async def get_case_document_html(case_id: str):
    try:
        content = sow_workflow_service.render_document_html(case_id)
        return HTMLResponse(content=content)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/generate/")
async def generate_json(
    customer: str = Form(..., description="Customer name"),
    application: str = Form(..., description="Application name"),
    scope: str = Form(..., description="Project scope"),
    impdetails: str = Form(..., description="Implementation Details"),
    llm_provider: str = Form("openai"),  # <--- NEW FIELD, default to "openai"
    vision_provider: str = Form("meta.llama-3.2-90b-vision-instruct"), # <--- NEW FIELD
    file: UploadFile = File(None, description="Optional DOCX template file"),
    diagram: UploadFile = File(None, description="Optional architecture diagram")
):
    """Generate document content and return as JSON"""
    temp_file_path = None
    
    try:
        logger.info(f"Starting document generation for {customer} - {application}")
        
        # Use uploaded document or default template
        if file and file.filename:
            try:
                logger.info(f"Using uploaded template: {file.filename}")
                full_text, temp_file_path = await safe_extract_text_from_docx(file)
            except Exception as e:
                logger.warning(f"Cannot use uploaded file, falling back to default template: {str(e)}")
                full_text = DEFAULT_TEMPLATE
        else:
            logger.info("Using default template")
            full_text = DEFAULT_TEMPLATE
        
        placeholders = document_service.extract_placeholders(full_text)
        logger.info(f"Found {len(placeholders)} placeholders: {placeholders}")
        
        # Process diagram if provided
        diagram_data_uri = None
        if diagram and diagram.filename:
            try:
                logger.info(f"Processing diagram: {diagram.filename}")
                diagram_data_uri = await safe_process_diagram(diagram)
                logger.info("Diagram processed successfully")
            except Exception as e:
                logger.warning(f"Cannot process diagram: {str(e)}")
        
        # Generate content
        replacements = await content_generator.generate_content(
            full_text, customer, application, scope, impdetails, diagram_data_uri,llm_provider=llm_provider, vision_provider=vision_provider    
        )
        
        # Replace placeholders in text
        final_text = document_service.replace_placeholders_in_text(full_text, replacements)
        
        logger.info("Document generation completed successfully")
        
        return JSONResponse(content={
            "status": "success",
            "customer": customer,
            "application": application,
            "scope": scope,
            "template_source": "uploaded" if file else "default",
            "template_filename": file.filename if file else "default_template.md",
            "placeholders_found": placeholders,
            "generated_content": final_text,
            "replacements": replacements,
            "diagram_analyzed": diagram_data_uri is not None,
            "metadata": {
                "placeholders_count": len(placeholders),
                "content_length": len(final_text),
                "has_diagram": diagram is not None,
                "used_default_template": file is None
            }
        })
        
    except Exception as e:
        logger.error(f"Error in document generation: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e),
                "customer": customer,
                "application": application
            }
        )
    finally:
        if temp_file_path and app_config.temp_file_cleanup:
            document_service.cleanup_temp_file(temp_file_path)

@app.post("/generate-html/")
async def generate_html(
    customer: str = Form(..., description="Customer name"),
    application: str = Form(..., description="Application name"),
    scope: str = Form(..., description="Project scope"),
    impdetails: str = Form("", description="Implementation Details"),
    llm_provider: str = Form("openai"),  # <--- NEW FIELD, default to "openai"
    vision_provider: str = Form("meta.llama-3.2-90b-vision-instruct"), # <--- NEW FIELD
    file: UploadFile = File(None, description="Optional DOCX template file"),
    diagram: UploadFile = File(None, description="Optional architecture diagram")
):
    """Generate document content and return as formatted HTML for easy copy-paste"""
    temp_file_path = None
    used_default_template = False
    file_validation_error = None
    diagram_error = None

    try:
        logger.info(f"Starting HTML document generation for {customer} - {application} with implementation details {impdetails}")

        # Determine which template to use with better error handling
        full_text = None
        
        # Check if a file was uploaded and has content
        if file and file.filename:
            try:
                logger.info(f"Attempting to use uploaded template: {file.filename}")
                if validate_docx_file(file):
                    full_text, temp_file_path = await safe_extract_text_from_docx(file)
                    logger.info(f"Successfully extracted text from uploaded template")
                else:
                    file_validation_error = f"File '{file.filename}' failed validation"
                    logger.warning(file_validation_error)
                    
            except ValueError as e:
                file_validation_error = str(e)
                logger.warning(f"Cannot use uploaded file: {file_validation_error}")
            except Exception as e:
                file_validation_error = f"Unexpected error processing file: {str(e)}"
                logger.error(file_validation_error)
        
        # Fall back to default template if file processing failed
        if full_text is None:
            if file and file.filename:
                logger.info(f"Falling back to default template due to file issue: {file_validation_error}")
            else:
                logger.info("Using default template (no file uploaded)")
            full_text = DEFAULT_TEMPLATE
            used_default_template = True

        placeholders = document_service.extract_placeholders(full_text)
        logger.info(f"Found {len(placeholders)} placeholders: {placeholders}")

        # Process diagram if provided with error handling
        diagram_data_uri = None
        if diagram and diagram.filename:
            try:
                logger.info(f"Processing diagram: {diagram.filename}")
                if validate_image_file(diagram):
                    diagram_data_uri = await safe_process_diagram(diagram)
                    logger.info("Diagram processed successfully")
                else:
                    diagram_error = f"Diagram '{diagram.filename}' failed validation"
                    logger.warning(diagram_error)
                    
            except ValueError as e:
                diagram_error = str(e)
                logger.warning(f"Cannot process diagram: {diagram_error}")
            except Exception as e:
                diagram_error = f"Unexpected error processing diagram: {str(e)}"
                logger.error(diagram_error)

        # Generate content
        replacements = await content_generator.generate_content(
            full_text, customer, application, scope, impdetails, diagram_data_uri, llm_provider=llm_provider, vision_provider=vision_provider  
        )

        # Replace placeholders in text
        final_text = document_service.replace_placeholders_in_text(full_text, replacements)

        # Convert markdown to HTML for better display and copy-paste
        import markdown
        
        # Convert the final document to HTML
        html_content = markdown.markdown(final_text, extensions=['tables', 'fenced_code', 'nl2br'])
        
        # Determine template source description
        template_source_desc = "Default Template"
        if not used_default_template and file:
            template_source_desc = f"Uploaded File ({file.filename})"
        elif used_default_template and file:
            template_source_desc = f"Default Template (uploaded file had issues)"

        # Build complete HTML response
        complete_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Generated Document - {customer} - {application}</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    max-width: 900px;
                    margin: 20px auto;
                    padding: 20px;
                    line-height: 1.6;
                    background-color: #f8f9fa;
                }}
                .header {{ 
                    background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
                    color: white;
                    padding: 20px;
                    border-radius: 10px;
                    margin-bottom: 20px;
                    text-align: center;
                }}
                .content {{ 
                    background: white;
                    padding: 30px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    margin-bottom: 20px;
                }}
                .metadata {{
                    background: #e3f2fd;
                    padding: 15px;
                    border-radius: 8px;
                    border-left: 4px solid #2196f3;
                    margin-bottom: 20px;
                    font-size: 0.9em;
                }}
                .warning {{
                    background: #fff3e0;
                    padding: 15px;
                    border-radius: 8px;
                    border-left: 4px solid #ff9800;
                    margin-bottom: 20px;
                    font-size: 0.9em;
                }}
                .copy-btn {{
                    background: linear-gradient(135deg, #28a745 0%, #20c997 100%);
                    color: white;
                    border: none;
                    padding: 10px 20px;
                    border-radius: 5px;
                    cursor: pointer;
                    font-size: 14px;
                    margin: 10px 5px;
                }}
                .copy-btn:hover {{
                    transform: translateY(-1px);
                    box-shadow: 0 4px 8px rgba(0,0,0,0.2);
                }}
                h1, h2, h3 {{ color: #333; }}
                h1 {{ border-bottom: 3px solid #2c3e50; padding-bottom: 10px; }}
                h2 {{ border-bottom: 1px solid #dee2e6; padding-bottom: 8px; }}
                table {{ 
                    border-collapse: collapse; 
                    width: 100%; 
                    margin: 15px 0;
                }}
                table, th, td {{ 
                    border: 1px solid #dee2e6; 
                }}
                th, td {{ 
                    padding: 12px; 
                    text-align: left; 
                }}
                th {{ 
                    background-color: #f8f9fa; 
                    font-weight: 600;
                }}
                code {{
                    background-color: #f8f9fa;
                    padding: 2px 6px;
                    border-radius: 3px;
                    font-family: 'Courier New', monospace;
                }}
                pre {{
                    background-color: #f8f9fa;
                    padding: 15px;
                    border-radius: 5px;
                    overflow-x: auto;
                    border-left: 4px solid #2c3e50;
                }}
                ul, ol {{ margin: 15px 0; }}
                li {{ margin: 5px 0; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>📄 Generated Document</h1>
                <p><strong>Customer:</strong> {customer} | <strong>Application:</strong> {application}</p>
            </div>
            
            <div class="metadata">
                <strong>📋 Document Information:</strong><br>
                • Template Source: {template_source_desc}<br>
                • Placeholders Processed: {len(placeholders)}<br>
                • Diagram Included: {'✅ Yes (' + diagram.filename + ')' if diagram_data_uri else '❌ No'}<br>
                • Content Length: {len(final_text):,} characters
            </div>
            
            {"<div class='warning'><strong>⚠️ Processing Notes:</strong><br>" + "<br>".join(filter(None, [f"• Template: {file_validation_error}" if file_validation_error else None, f"• Diagram: {diagram_error}" if diagram_error else None])) + "</div>" if file_validation_error or diagram_error else ""}
            
            <div style="text-align: center; margin: 20px 0;">
                <button class="copy-btn" onclick="copyToClipboard('content')">📋 Copy Document Content</button>
                <button class="copy-btn" onclick="copyToClipboard('html')">📋 Copy as HTML</button>
                <button class="copy-btn" onclick="printDocument()">🖨️ Print Document</button>
            </div>
            
            <div class="content" id="content">
                {html_content}
            </div>
            
            <script>
                function copyToClipboard(type) {{
                    let content;
                    if (type === 'content') {{
                        content = document.getElementById('content').innerText;
                    }} else if (type === 'html') {{
                        content = document.getElementById('content').innerHTML;
                    }}
                    
                    navigator.clipboard.writeText(content).then(function() {{
                        const btn = event.target;
                        const originalText = btn.innerHTML;
                        btn.innerHTML = '✅ Copied!';
                        btn.style.background = '#28a745';
                        setTimeout(() => {{
                            btn.innerHTML = originalText;
                            btn.style.background = 'linear-gradient(135deg, #28a745 0%, #20c997 100%)';
                        }}, 2000);
                    }}).catch(function(err) {{
                        alert('Error copying to clipboard: ' + err);
                    }});
                }}
                
                function printDocument() {{
                    window.print();
                }}
                
                const style = document.createElement('style');
                style.innerHTML = `
                    @media print {{
                        body {{ background: white; }}
                        .header, .metadata, .warning, button {{ display: none !important; }}
                        .content {{ 
                            box-shadow: none; 
                            padding: 0;
                            background: white;
                        }}
                    }}
                `;
                document.head.appendChild(style);
            </script>
        </body>
        </html>
        """

        logger.info("HTML document generation completed successfully")
        return HTMLResponse(content=complete_html)

    except Exception as e:
        logger.error(f"Error in HTML document generation: {str(e)}")
        error_html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Error</title></head>
        <body>
            <h2 style="color: red;">❌ Error Processing Document</h2>
            <p><strong>Customer:</strong> {customer}</p>
            <p><strong>Application:</strong> {application}</p>
            <p><strong>Error:</strong> {str(e)}</p>
            <p><strong>File Issues:</strong> {file_validation_error or 'None'}</p>
            <p><strong>Diagram Issues:</strong> {diagram_error or 'None'}</p>
        </body>
        </html>
        """
        return HTMLResponse(content=error_html, status_code=500)
    finally:
        if temp_file_path and app_config.temp_file_cleanup:
            document_service.cleanup_temp_file(temp_file_path)

@app.post("/generate-markdown/")
async def generate_markdown(
    customer: str = Form(..., description="Customer name"),
    application: str = Form(..., description="Application name"),
    scope: str = Form(..., description="Project scope"),
    impdetails: str = Form(..., description="Implementation Details"),
    llm_provider: str = Form("openai"),  # <--- NEW FIELD, default to "openai"
    vision_provider: str = Form("meta.llama-3.2-90b-vision-instruct"), # <--- NEW FIELD
    file: UploadFile = File(None, description="Optional DOCX template file"),
    diagram: UploadFile = File(None, description="Optional architecture diagram")
):
    """Generate document content and return as formatted Markdown"""
    temp_file_path = None
    used_default_template = False
    file_validation_error = None

    try:
        logger.info(f"Starting Markdown document generation for {customer} - {application}")

        # Determine which template to use with better error handling
        full_text = None
        
        # Check if a file was uploaded and has content
        if file and file.filename:
            try:
                logger.info(f"Attempting to use uploaded template: {file.filename}")
                if validate_docx_file(file):
                    full_text, temp_file_path = await safe_extract_text_from_docx(file)
                    logger.info(f"Successfully extracted text from uploaded template")
                else:
                    file_validation_error = f"File '{file.filename}' failed validation"
                    logger.warning(file_validation_error)
                    
            except ValueError as e:
                file_validation_error = str(e)
                logger.warning(f"Cannot use uploaded file: {file_validation_error}")
            except Exception as e:
                file_validation_error = f"Unexpected error processing file: {str(e)}"
                logger.error(file_validation_error)
        
        # Fall back to default template if file processing failed
        if full_text is None:
            if file and file.filename:
                logger.info(f"Falling back to default template due to file issue: {file_validation_error}")
            else:
                logger.info("Using default template (no file uploaded)")
            full_text = DEFAULT_TEMPLATE
            used_default_template = True

        placeholders = document_service.extract_placeholders(full_text)
        logger.info(f"Found {len(placeholders)} placeholders: {placeholders}")

        # Process diagram if provided with error handling
        diagram_data_uri = None
        diagram_error = None
        if diagram and diagram.filename:
            try:
                logger.info(f"Processing diagram: {diagram.filename}")
                if validate_image_file(diagram):
                    diagram_data_uri = await safe_process_diagram(diagram)
                    logger.info("Diagram processed successfully")
                else:
                    diagram_error = f"Diagram '{diagram.filename}' failed validation"
                    logger.warning(diagram_error)
                    
            except ValueError as e:
                diagram_error = str(e)
                logger.warning(f"Cannot process diagram: {diagram_error}")
            except Exception as e:
                diagram_error = f"Unexpected error processing diagram: {str(e)}"
                logger.error(diagram_error)

        # Generate content
        replacements = await content_generator.generate_content(
            full_text, customer, application, scope, impdetails, diagram_data_uri, llm_provider=llm_provider, vision_provider=vision_provider 
        )

        # Replace placeholders in text  
        final_text = document_service.replace_placeholders_in_text(full_text, replacements)

        # Build comprehensive Markdown output
        template_source = "Default Template"
        if not used_default_template and file:
            template_source = f"Uploaded File ({file.filename})"
        elif used_default_template and file:
            template_source = f"Default Template (uploaded file had issues: {file_validation_error})"

        markdown_lines = [
            f"# Generated Document for **{customer} - {application}**",
            "",
            f"**Scope:** {scope}",
            f"**Generated:** {str(__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}",
            f"**Template Source:** {template_source}",
            ""
        ]
        
        # Add processing notes if there were issues
        if file_validation_error or diagram_error:
            markdown_lines.extend([
                "## ⚠️ Processing Notes.",
                ""
            ])
            if file_validation_error:
                markdown_lines.append(f"- **Template File Issue:** {file_validation_error}")
            if diagram_error:
                markdown_lines.append(f"- **Diagram Issue:** {diagram_error}")
            markdown_lines.append("")
        
        markdown_lines.extend([
            "---",
            "",
            "## 📄 Final Document",
            "",
            final_text,
            "",
            "---",
            "",
            "## 🔍 Generation Details",
            "",
            f"- **Placeholders Found:** {len(placeholders)}",
            f"- **Diagram Processed:** {'Yes (' + diagram.filename + ')' if diagram_data_uri else ('No - ' + diagram_error if diagram_error else 'No')}",
            f"- **Content Length:** {len(final_text):,} characters",
            f"- **Used Default Template:** {'Yes' if used_default_template else 'No'}",
            "",
            "### Placeholders Processed:",
            ""
        ])

        # Add individual placeholder details
        for placeholder in placeholders:
            replacement = replacements.get(placeholder, "_(not replaced)_")
            word_count = len(replacement.split())
            markdown_lines.extend([
                f"- **{placeholder}:** {word_count} words",
                ""
            ])

        # Join into a single Markdown string
        markdown_content = "\n".join(markdown_lines)

        logger.info("Markdown document generation completed successfully")
        return PlainTextResponse(content=markdown_content)

    except Exception as e:
        logger.error(f"Error in Markdown document generation: {str(e)}")
        error_md = f"""# ❌ Error Processing Document

**Customer:** {customer}
**Application:** {application}
**Error:** {str(e)}

## File Information
- **Template File:** {file.filename if file and file.filename else 'None'}
- **Diagram File:** {diagram.filename if diagram and diagram.filename else 'None'}
- **Used Default Template:** {used_default_template}
- **File Validation Error:** {file_validation_error or 'None'}

## Troubleshooting
1. **Check file validity:** Ensure DOCX files are not corrupted
2. **File size:** Make sure files are not empty (minimum 1KB for DOCX)
3. **File format:** Use .docx format for templates, image formats for diagrams
4. **Network:** Verify connectivity to AI services
5. **Logs:** Check server logs for detailed error information

## Default Template
If your custom template fails, the system will use a default template with standard placeholders.
"""
        return PlainTextResponse(content=error_md, status_code=500)
    finally:
        if temp_file_path and app_config.temp_file_cleanup:
            document_service.cleanup_temp_file(temp_file_path)


@app.post("/generate-sow")
async def generate_sow(
    project_data: Optional[str] = Form(None, description="JSON payload for project context"),
    projectData: Optional[str] = Form(None, description="Alias for project_data"),
    llm_provider: str = Form("meta.llama-3.1-70b-instruct"),
    current_architecture_image: UploadFile = File(None, description="Optional current architecture diagram"),
    target_architecture_image: UploadFile = File(None, description="Optional target architecture diagram"),
):
    """Generate deterministic SoW sections with optional multimodal architecture extraction."""
    incoming_project_data = project_data if project_data is not None else projectData
    if incoming_project_data is None or not str(incoming_project_data).strip():
        raise HTTPException(status_code=400, detail="project_data is required as multipart form field")

    try:
        parsed_project_data = json.loads(incoming_project_data)
        if not isinstance(parsed_project_data, dict):
            raise ValueError("project_data must deserialize to a JSON object")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid project_data payload: {exc}") from exc

    current_extracted: Dict[str, Any] = {}
    target_extracted: Dict[str, Any] = {}

    if current_architecture_image and current_architecture_image.filename:
        try:
            current_uri = await safe_process_diagram(current_architecture_image)
            current_extracted = architecture_vision_agent.extract_architecture_from_image(current_uri, "current")
        except Exception as exc:
            logger.warning("Current architecture image parsing failed: %s", exc)

    if target_architecture_image and target_architecture_image.filename:
        try:
            target_uri = await safe_process_diagram(target_architecture_image)
            target_extracted = architecture_vision_agent.extract_architecture_from_image(target_uri, "target")
        except Exception as exc:
            logger.warning("Target architecture image parsing failed: %s", exc)

    architecture_context = architecture_context_builder.build(
        project_data=parsed_project_data,
        current_architecture_extracted=current_extracted,
        target_architecture_extracted=target_extracted,
    )

    architecture_sections = [
        "CURRENT STATE ARCHITECTURE",
        "FUTURE STATE ARCHITECTURE",
        "IMPLEMENTATION DETAILS",
        "ARCHITECTURE DEPLOYMENT OVERVIEW",
        "CURRENTLY USED TECHNOLOGY STACK",
        "OCI SERVICE SIZING",
    ]

    generated_sections: Dict[str, str] = {}
    guardrail_findings: Dict[str, Any] = {}

    for section_name in architecture_sections:
        rag_context = section_writer.retrieve_rag_context(section_name, parsed_project_data)
        section_text = section_writer.write_section(
            section_name=section_name,
            project_data=parsed_project_data,
            architecture_context=architecture_context,
            rag_context=rag_context,
            llm_provider=llm_provider,
        )
        section_text = _inject_architecture_evidence_if_missing(section_name, section_text, architecture_context)
        generated_sections[section_name] = section_text
        issues = ArchitectureGuardrails.validate(section_name, section_text, architecture_context)
        if issues:
            guardrail_findings[section_name] = issues

    markdown = "\n\n".join([f"## {name}\n{body}" for name, body in generated_sections.items()])

    return JSONResponse(
        content={
            "status": "success",
            "sections": generated_sections,
            "document_markdown": markdown,
            "architecture_extracted": {
                "current": current_extracted,
                "target": target_extracted,
            },
            "architecture_context": architecture_context,
            "guardrail_findings": guardrail_findings,
        }
    )


@app.post("/chat-rag/", response_model=RAGChatResponse)
async def chat_rag(payload: RAGChatRequest):
    """Chat endpoint backed by OCI Agent Runtime (RAG endpoint)."""
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    try:
        result = rag_service.chat(message=message, session_id=payload.session_id)
        return RAGChatResponse(**result)
    except Exception as exc:
        logger.error("RAG chat failed: %s", str(exc))
        raise HTTPException(status_code=500, detail=f"RAG chat failed: {str(exc)}") from exc

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
