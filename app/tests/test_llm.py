"""Tests for LLM configuration loading."""

from __future__ import annotations

from app.services.llm import LLMConfig


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
    assert config.model_id == "meta.llama-4-maverick-17b-128e-instruct-fp8"
    assert config.compartment_id.startswith("ocid1.compartment.oc1")


def test_llm_config_prefers_oci_model_id_llama_alias(monkeypatch) -> None:
    """LLM config should use OCI_MODEL_ID_LLAMA when OCI_MODEL_ID is absent."""
    monkeypatch.delenv("OCI_MODEL_ID", raising=False)
    monkeypatch.setenv("OCI_MODEL_ID_LLAMA", "meta.llama-custom")

    config = LLMConfig.from_env()

    assert config.model_id == "meta.llama-custom"
