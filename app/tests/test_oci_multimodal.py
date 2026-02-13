from __future__ import annotations

from types import SimpleNamespace

from app.config.settings import OCISettings
from app.services.oci_multimodal import OCIClient


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.data = SimpleNamespace(
            chat_response=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=[SimpleNamespace(text=text)]),
                    )
                ]
            )
        )


class _FakeGenAIClient:
    def __init__(self) -> None:
        self.last_details = None

    def chat(self, details):
        self.last_details = details
        return _FakeResponse('{"ok": true}')


def _settings() -> OCISettings:
    return OCISettings(
        config_file="~/.oci/config",
        profile="DEFAULT",
        endpoint="https://example.com",
        model_id="meta.llama-4-maverick-17b-128e-instruct-fp8",
        compartment_id="ocid1.compartment.oc1..test",
        temperature=0.2,
        timeout_connect=10,
        timeout_read=120,
        multimodal_model_name="google.gemini-2.5-pro",
    )


def test_multimodal_completion_uses_valid_top_k_and_default_model() -> None:
    fake_client = _FakeGenAIClient()
    client = OCIClient.__new__(OCIClient)
    client.settings = _settings()
    client._client = fake_client

    response = client.multimodal_completion(prompt="analyze", image_base64="abcd", mime_type="image/png")

    assert response == '{"ok": true}'
    assert fake_client.last_details.chat_request.top_k == 1
    assert fake_client.last_details.serving_mode.model_id == "google.gemini-2.5-pro"


def test_multimodal_completion_allows_model_override() -> None:
    fake_client = _FakeGenAIClient()
    client = OCIClient.__new__(OCIClient)
    client.settings = _settings()
    client._client = fake_client

    client.multimodal_completion(
        prompt="analyze",
        image_base64="abcd",
        mime_type="image/png",
        model_name="google.gemini-1.5-pro",
    )

    assert fake_client.last_details.serving_mode.model_id == "google.gemini-1.5-pro"
