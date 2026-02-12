"""Tests for LLM configuration loading."""

from __future__ import annotations

from app.services.llm import LLMConfig, LLMConfigurationError


def test_llm_config_accepts_oci_endpoint_alias(monkeypatch) -> None:
    """LLM config should accept OCI_ENDPOINT when OCI_GENAI_ENDPOINT is absent."""
    monkeypatch.delenv("OCI_GENAI_ENDPOINT", raising=False)
    monkeypatch.setenv("OCI_ENDPOINT", "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com")
    monkeypatch.setenv("OCI_MODEL_ID", "model-id")
    monkeypatch.setenv("OCI_COMPARTMENT_ID", "compartment-id")

    config = LLMConfig.from_env()

    assert config.endpoint == "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com"


def test_llm_config_missing_required_variables(monkeypatch) -> None:
    """LLM config should raise a readable error when required vars are missing."""
    monkeypatch.delenv("OCI_GENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("OCI_ENDPOINT", raising=False)
    monkeypatch.delenv("OCI_MODEL_ID", raising=False)
    monkeypatch.delenv("OCI_COMPARTMENT_ID", raising=False)

    try:
        LLMConfig.from_env()
    except LLMConfigurationError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected LLMConfigurationError")

    assert "OCI_GENAI_ENDPOINT" in message
    assert "OCI_MODEL_ID" in message
    assert "OCI_COMPARTMENT_ID" in message
