import oci
import base64
import mimetypes
from oci.generative_ai_inference.models import (
    ImageContent,
    TextContent,
    Message,
    GenericChatRequest,
    OnDemandServingMode,
    ChatDetails
)


# -------------------------------
# Configuration
# -------------------------------

# Path to your OCI config file
config = oci.config.from_file("~/.oci/config", "DEFAULT")

# Compartment OCID where Generative AI is enabled
compartment_id = "ocid1.compartment.oc1..aaaaaaaaw5klhwyzaxvto4vzwnavevivn75nfuv4fdanlbjux4fuk6tv5geq"

# Model to use
model_id = "xai.grok-4"


# Service endpoint
endpoint = "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com"

generative_ai_inference_client = oci.generative_ai_inference.GenerativeAiInferenceClient(config=config, service_endpoint=endpoint, retry_strategy=oci.retry.NoneRetryStrategy(), timeout=(10,240))
chat_detail = oci.generative_ai_inference.models.ChatDetails()

# content = oci.generative_ai_inference.models.TextContent()
# content.text = "Explain quantum computing in simple terms."
# message = oci.generative_ai_inference.models.Message()
# message.role = "USER"
# message.content = [content]

# === Encode image to base64 data URI ===

def create_base64_data_uri(image_path):
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        raise ValueError("Could not determine MIME type.")

    with open(image_path, "rb") as f:
        encoded_bytes = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded_bytes}"



image_data_uri = create_base64_data_uri("cloud_architecture2.png")

# === Create ImageUrl, ImageContent, and message ===
image_url = oci.generative_ai_inference.models.ImageUrl(
    url=image_data_uri
)

image_content = oci.generative_ai_inference.models.ImageContent(
    image_url=image_url
)




text_content = oci.generative_ai_inference.models.TextContent(
    text="As a OCI Cloud architect, that you have designed this cloud architecture, please provide a concise description, where the audience are technical people, with no necessary knowledge of  OCI or even cloud architectures. Describe in bullets services in the diagram, with high level description and use of it in the diagram. Write each service in bullets and grouped by area. Prettify the output and generate a markdown format. Avoid using emoticons ")

message = oci.generative_ai_inference.models.Message(
    role="USER",
    content=[image_content, text_content]
)






chat_request = oci.generative_ai_inference.models.GenericChatRequest()
chat_request.api_format = oci.generative_ai_inference.models.BaseChatRequest.API_FORMAT_GENERIC
chat_request.messages = [message]
chat_request.max_tokens =4096 
chat_request.temperature = 1
chat_request.top_p = 1
chat_request.top_k = 1

#chat_detail.serving_mode = oci.generative_ai_inference.models.OnDemandServingMode(model_id="ocid1.generativeaimodel.oc1.us-chicago-1.amaaaaaask7dceya3bsfz4ogiuv3yc7gcnlry7gi3zzx6tnikg6jltqszm2q")
#chat_detail.serving_mode = oci.generative_ai_inference.models.OnDemandServingMode(model_id="google.gemini-2.5-pro")
chat_detail.serving_mode = oci.generative_ai_inference.models.OnDemandServingMode(model_id="meta.llama-4-maverick-17b-128e-instruct-fp8")
chat_detail.chat_request = chat_request
chat_detail.compartment_id = compartment_id

chat_response = generative_ai_inference_client.chat(chat_detail)

# Print result
print("**************************Chat Result**************************")
print(vars(chat_response))



