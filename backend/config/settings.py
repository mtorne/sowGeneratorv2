import os
from dataclasses import dataclass

@dataclass
class OCIConfig:
    compartment_id: str = os.getenv("OCI_COMPARTMENT_ID", "ocid1.compartment.oc1..aaaaaaaaw5klhwyzaxvto4vzwnavevivn75nfuv4fdanlbjux4fuk6tv5geq")
    model_id: str = "xai.grok-4"
    model_id_llama: str = "meta.llama-4-maverick-17b-128e-instruct-fp8"
    #model_id_llama = "meta.llama-3.1-70b-instruct"
    model_id_vision = "meta.llama-3.2-90b-vision-instruct"
    model_id_cohere = "cohere.command-r-plus-08-2024"    
    endpoint: str = os.getenv("OCI_GENAI_ENDPOINT", "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com")
    agent_endpoint: str = os.getenv("OCI_AGENT_ENDPOINT", "https://agent-runtime.generativeai.eu-frankfurt-1.oci.oraclecloud.com")
    agent_endpoint_id: str = os.getenv("OCI_AGENT_ENDPOINT_ID", "ocid1.genaiagentendpoint.oc1.eu-frankfurt-1.amaaaaaao7vto7ia42ib6b3xnopor3ynh3fsr7ui3p37bw3swel7ohg6n23q")
    max_tokens: int = 20000
    max_tokens_llama: int = 4096
    temperature: float = 0.7
    top_p: float = 1.0
    top_k: int = 0
    top_k_llama: int = -1
    timeout_connect: int = 10
    timeout_read: int = 240

@dataclass
class AppConfig:
    cors_origins: list = None
    temp_file_cleanup: bool = True
    
    def __post_init__(self):
        if self.cors_origins is None:
            self.cors_origins = ["*"]

# Initialize configurations
oci_config = OCIConfig()
app_config = AppConfig()
