"""Tests for LLM configuration loading."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import llm
from app.services.llm import LLMConfig
from app.config.settings import OCISettings


def test_llm_config_accepts_oci_endpoint_alias(monkeypatch) -> None:
    """LLM config should accept OCI_ENDPOINT when OCI_GENAI_ENDPOINT is absent."""
    monkeypatch.delenv("OCI_GENAI_ENDPOINT", raising=False)
    monkeypatch.setenv("OCI_ENDPOINT", "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com")

    config = LLMConfig.from_env()

    assert config.endpoint == "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com"


def test_llm_config_uses_backend_compatible_defaults(monkeypatch) -> None:
    """LLM config should reuse backend defaults when vars are missing."""
    monkeypatch.delenv("OCI_GENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("OCI_ENDPOINT", raising=False)
    monkeypatch.delenv("OCI_MODEL_ID", raising=False)
    monkeypatch.delenv("OCI_MODEL_ID_LLAMA", raising=False)
    monkeypatch.delenv("OCI_COMPARTMENT_ID", raising=False)

    config = LLMConfig.from_env()

    assert config.endpoint == "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com"
    assert config.model_id == "google.gemini-2.5-pro"
    assert config.compartment_id.startswith("ocid1.compartment.oc1")


def test_llm_config_prefers_oci_model_id_llama_alias(monkeypatch) -> None:
    """LLM config should use OCI_MODEL_ID_LLAMA when OCI_MODEL_ID is absent."""
    monkeypatch.delenv("OCI_MODEL_ID", raising=False)
    monkeypatch.setenv("OCI_MODEL_ID_LLAMA", "meta.llama-custom")

    config = LLMConfig.from_env()

    assert config.model_id == "meta.llama-custom"


def test_oci_settings_defaults_multimodal_model_to_gemini_pro(monkeypatch) -> None:
    monkeypatch.delenv("OCI_MM_MODEL_NAME", raising=False)

    settings = OCISettings.from_env()

    assert settings.multimodal_model_name == "google.gemini-2.5-pro"


def test_call_llm_uses_valid_top_k(monkeypatch) -> None:
    captured = {}

    class _FakeClient:
        def chat(self, details):  # pragma: no cover - execution goes through patched retry
            return details

    def _fake_retry(fn, details, **kwargs):
        captured["details"] = details
        return SimpleNamespace()

    monkeypatch.setattr(llm, "_build_client", lambda config: _FakeClient())
    monkeypatch.setattr(llm, "_call_with_retry", _fake_retry)
    monkeypatch.setattr(llm, "_extract_text", lambda response: "ok")

    out = llm.call_llm("sys", "user")

    assert out == "ok"
    assert captured["details"].chat_request.top_k >= 1
