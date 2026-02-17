"""Architecture vision agent backed by OCI multimodal LLM analysis."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol

from app.config.settings import OCISettings


logger = logging.getLogger(__name__)


DIAGRAM_ANALYSIS_PROMPT = """
You are an OCI cloud architecture diagram parser.
Analyze the provided image directly. Do not summarize. Do not explain.
Return STRICT JSON only. No markdown. No code fences. No extra text.

Required JSON schema:
{
  "diagram_summary": {
    "diagram_type": "string",
    "scope": "string",
    "primary_intent": "string"
  },
  "components": {
    "compute": ["string"],
    "kubernetes": ["string"],
    "databases": ["string"],
    "networking": ["string"],
    "load_balancers": ["string"],
    "security": ["string"],
    "storage": ["string"],
    "streaming": ["string"],
    "on_prem_connectivity": ["string"]
  },
  "relationships": [
    {
      "from": "string",
      "to": "string",
      "protocol_or_link": "string",
      "direction": "string"
    }
  ],
  "high_availability_pattern": {
    "multi_ad": "boolean",
    "multi_region": "boolean",
    "active_active": "boolean",
    "active_passive": "boolean",
    "dr_mechanism": "string"
  },
  "confidence_assessment": {
    "diagram_clarity": "high|medium|low",
    "component_identification_confidence": "high|medium|low",
    "overall_confidence": "high|medium|low",
    "reason": "string"
  }
}

Rules:
- Extract only visually present evidence from the image.
- Do not invent components not present in the diagram.
- Use empty arrays for missing categories.
- If labels are unreadable, set low confidence and include reason.
""".strip()


class OCIClientProtocol(Protocol):
    def multimodal_completion(self, prompt: str, image_base64: str, mime_type: str, **kwargs: Any) -> str: ...


@dataclass(frozen=True)
class _ImageMetadata:
    width: int
    height: int
    fmt: str
    mime_type: str


class ArchitectureVisionAgent:
    """Extracts architecture evidence from uploaded diagrams using OCI multimodal LLM."""

    EXPECTED_KEYS = {
        "diagram_summary",
        "components",
        "relationships",
        "high_availability_pattern",
        "confidence_assessment",
    }

    def __init__(
        self,
        llm_client: OCIClientProtocol | None = None,
        *,
        model_name: str | None = None,
        timeout_seconds: float | None = None,
        low_confidence_retries: int = 1,
    ) -> None:
        settings = OCISettings.from_env()
        self.llm_client = llm_client
        self.model_name = model_name or settings.multimodal_model_name
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.timeout_read
        self.low_confidence_retries = max(0, low_confidence_retries)

    def analyze(self, file_name: str, content: bytes, diagram_role: str) -> dict[str, Any]:
        size_bytes = len(content)
        metadata = self._read_image_metadata(content=content, file_name=file_name)
        self._log_image_metadata(file_name=file_name, size_bytes=size_bytes, metadata=metadata)

        if metadata is None:
            return self._error_result(
                diagram_role=diagram_role,
                file_name=file_name,
                fmt="unknown",
                size_bytes=size_bytes,
                width=0,
                height=0,
                error_code="image_unreadable",
                error_message="Unable to read uploaded image.",
            )

        if self.llm_client is None:
            logger.error("architecture_vision.llm_client_missing role=%s file=%s", diagram_role, file_name)
            return self._error_result(
                diagram_role=diagram_role,
                file_name=file_name,
                fmt=metadata.fmt,
                size_bytes=size_bytes,
                width=metadata.width,
                height=metadata.height,
                error_code="llm_client_missing",
                error_message="Multimodal OCI client is not configured.",
            )

        image_base64 = base64.b64encode(content).decode("utf-8")
        prompt = self._build_prompt(diagram_role=diagram_role)

        attempts = self.low_confidence_retries + 1
        best_output: dict[str, Any] = {}
        best_confidence = "low"

        for attempt in range(1, attempts + 1):
            logger.info("architecture_vision.llm_request role=%s file=%s attempt=%s", diagram_role, file_name, attempt)
            try:
                raw_response = self._call_multimodal_with_timeout(
                    prompt=prompt,
                    image_base64=image_base64,
                    mime_type=metadata.mime_type,
                )
            except Exception as exc:
                logger.exception("architecture_vision.llm_call_failed role=%s file=%s", diagram_role, file_name)
                return self._error_result(
                    diagram_role=diagram_role,
                    file_name=file_name,
                    fmt=metadata.fmt,
                    size_bytes=size_bytes,
                    width=metadata.width,
                    height=metadata.height,
                    error_code="llm_call_failed",
                    error_message=str(exc),
                )

            structured_output = self._safe_parse_json(raw_response)
            if not structured_output:
                logger.error(
                    "architecture_vision.invalid_json role=%s file=%s attempt=%s",
                    diagram_role,
                    file_name,
                    attempt,
                )
                best_output = {
                    "confidence_assessment": {
                        "overall_confidence": "low",
                        "reason": "Invalid JSON returned by multimodal model.",
                    }
                }
                best_confidence = "low"
                if attempt < attempts:
                    logger.warning(
                        "architecture_vision.retry_after_invalid_json role=%s file=%s next_attempt=%s",
                        diagram_role,
                        file_name,
                        attempt + 1,
                    )
                    continue
                break

            missing = sorted(self.EXPECTED_KEYS - set(structured_output.keys()))
            if missing:
                logger.warning(
                    "architecture_vision.missing_expected_keys role=%s file=%s missing=%s",
                    diagram_role,
                    file_name,
                    ",".join(missing),
                )

            confidence_assessment = structured_output.get("confidence_assessment", {})
            overall_confidence = str(confidence_assessment.get("overall_confidence", "low")).lower()
            if overall_confidence not in {"low", "medium", "high"}:
                overall_confidence = "low"

            best_output = structured_output
            best_confidence = overall_confidence
            logger.info(
                "architecture_vision.llm_response role=%s file=%s attempt=%s confidence=%s",
                diagram_role,
                file_name,
                attempt,
                overall_confidence,
            )
            if overall_confidence != "low":
                break

        return {
            "diagram_role": diagram_role,
            "file_name": file_name,
            "format": metadata.fmt,
            "size_bytes": size_bytes,
            "image_resolution": {"width": metadata.width, "height": metadata.height},
            "architecture_extraction": best_output,
            "analysis_confidence": best_output.get("confidence_assessment", {}),
        }

    def _build_prompt(self, diagram_role: str) -> str:
        return f"{DIAGRAM_ANALYSIS_PROMPT}\n\ndiagram_role={diagram_role}"

    def _call_multimodal_with_timeout(self, prompt: str, image_base64: str, mime_type: str) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._call_multimodal, prompt, image_base64, mime_type)
            try:
                return future.result(timeout=self.timeout_seconds)
            except FuturesTimeoutError as exc:
                logger.error("architecture_vision.llm_timeout timeout_seconds=%s", self.timeout_seconds)
                raise TimeoutError(f"Multimodal request timed out after {self.timeout_seconds} seconds") from exc

    def _call_multimodal(self, prompt: str, image_base64: str, mime_type: str) -> str:
        assert self.llm_client is not None
        call_kwargs: dict[str, Any] = {}
        if self.model_name:
            call_kwargs["model_name"] = self.model_name
        try:
            return self.llm_client.multimodal_completion(prompt=prompt, image_base64=image_base64, mime_type=mime_type, **call_kwargs)
        except TypeError:
            return self.llm_client.multimodal_completion(prompt=prompt, image_base64=image_base64, mime_type=mime_type)

    def _read_image_metadata(self, content: bytes, file_name: str) -> _ImageMetadata | None:
        try:
            from PIL import Image, UnidentifiedImageError
        except ModuleNotFoundError:
            logger.error("architecture_vision.pillow_missing file=%s", file_name)
            return None

        try:
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                fmt = (image.format or "unknown").lower()
        except (UnidentifiedImageError, OSError, ValueError):
            logger.exception("architecture_vision.image_unreadable file=%s", file_name)
            return None

        guessed_mime = Image.MIME.get(fmt.upper()) if fmt != "unknown" else None
        mime_type = guessed_mime or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        return _ImageMetadata(width=width, height=height, fmt=fmt, mime_type=mime_type)

    def _log_image_metadata(self, file_name: str, size_bytes: int, metadata: _ImageMetadata | None) -> None:
        if metadata is None:
            logger.warning("architecture_vision.image_metadata_unavailable file=%s size_bytes=%s", file_name, size_bytes)
            return

        logger.info(
            "architecture_vision.image_metadata file=%s format=%s size_bytes=%s width=%s height=%s",
            file_name,
            metadata.fmt,
            size_bytes,
            metadata.width,
            metadata.height,
        )
        if metadata.width < 1000:
            logger.warning(
                "architecture_vision.image_clarity_warning file=%s width=%s message=%s",
                file_name,
                metadata.width,
                "Image width below 1000px may reduce OCR/component clarity.",
            )

    @staticmethod
    def _safe_parse_json(response_text: str) -> dict[str, Any] | None:
        text = (response_text or "").strip()
        candidates: list[tuple[str, str]] = []

        if text:
            candidates.append(("raw", text))
        else:
            logger.warning("architecture_vision.empty_llm_response")

        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
        if fence_match:
            candidates.append(("fenced_json", fence_match.group(1).strip()))

        opening_fence_stripped = ArchitectureVisionAgent._strip_markdown_fence_prefix(text)
        if opening_fence_stripped and opening_fence_stripped != text:
            candidates.append(("opening_fence_stripped", opening_fence_stripped))

        balanced = ArchitectureVisionAgent._extract_balanced_json_object(text)
        if balanced:
            candidates.append(("balanced_object", balanced))

        for idx, (source, candidate) in enumerate(candidates, start=1):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError as exc:
                preview = candidate[:180].replace("\n", "\\n")
                fingerprint = hashlib.sha256(candidate.encode("utf-8", errors="ignore")).hexdigest()[:12]
                logger.warning(
                    "architecture_vision.json_decode_error candidate_index=%s source=%s len=%s sha12=%s pos=%s line=%s col=%s message=%s preview=%s",
                    idx,
                    source,
                    len(candidate),
                    fingerprint,
                    exc.pos,
                    exc.lineno,
                    exc.colno,
                    exc.msg,
                    preview,
                )
        if candidates:
            logger.error("architecture_vision.json_parse_failed candidate_count=%s", len(candidates))
        return None

    @staticmethod
    def _strip_markdown_fence_prefix(text: str) -> str:
        stripped = text.lstrip()
        if not stripped.startswith("```"):
            return text

        newline_index = stripped.find("\n")
        if newline_index == -1:
            return text

        body = stripped[newline_index + 1 :].strip()
        if body.endswith("```"):
            body = body[: -len("```")].strip()
        return body

    @staticmethod
    def _extract_balanced_json_object(text: str) -> str | None:
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escaped = False

        for idx in range(start, len(text)):
            char = text[idx]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]

        return None

    def _error_result(
        self,
        *,
        diagram_role: str,
        file_name: str,
        fmt: str,
        size_bytes: int,
        width: int,
        height: int,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        return {
            "diagram_role": diagram_role,
            "file_name": file_name,
            "format": fmt,
            "size_bytes": size_bytes,
            "image_resolution": {"width": width, "height": height},
            "architecture_extraction": {
                "error": {
                    "code": error_code,
                    "message": error_message,
                },
                "confidence_assessment": {
                    "overall_confidence": "low",
                    "reason": error_message,
                },
            },
            "analysis_confidence": {
                "overall_confidence": "low",
                "reason": error_message,
            },
        }
