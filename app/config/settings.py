"""Application configuration for the SoW generator."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OCISettings:
    """OCI Generative AI runtime settings aligned with backend defaults."""

    config_file: str
    profile: str
    endpoint: str
    model_id: str
    compartment_id: str
    temperature: float
    timeout_connect: float
    timeout_read: float
    multimodal_model_name: str

    @classmethod
    def from_env(cls) -> "OCISettings":
        """Load OCI settings from env, reusing backend-compatible names and defaults."""
        endpoint = (
            os.getenv("OCI_GENAI_ENDPOINT")
            or os.getenv("OCI_ENDPOINT")
            or "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com"
        )
        model_id = (
            os.getenv("OCI_MODEL_ID")
            or os.getenv("OCI_MODEL_ID_LLAMA")
            or "meta.llama-4-maverick-17b-128e-instruct-fp8"
        )
        compartment_id = os.getenv(
            "OCI_COMPARTMENT_ID",
            "ocid1.compartment.oc1..aaaaaaaaw5klhwyzaxvto4vzwnavevivn75nfuv4fdanlbjux4fuk6tv5geq",
        )

        return cls(
            config_file=os.getenv("OCI_CONFIG_FILE", os.path.expanduser("~/.oci/config")),
            profile=os.getenv("OCI_PROFILE", "DEFAULT"),
            endpoint=endpoint,
            model_id=model_id,
            compartment_id=compartment_id,
            temperature=float(os.getenv("OCI_TEMPERATURE", "0.2")),
            timeout_connect=float(os.getenv("OCI_TIMEOUT_CONNECT", "10")),
            timeout_read=float(os.getenv("OCI_TIMEOUT_READ", "120")),
            multimodal_model_name=os.getenv("OCI_MM_MODEL_NAME", "google.gemini-2.5-pro"),
        )
