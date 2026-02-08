# OCI Document Generator

A FastAPI application that generates technical documents using OCI Grok AI with architecture diagram analysis capabilities.

## Project Structure

```
├── main.py                     # FastAPI application entry point
├── config/
│   └── settings.py            # Configuration management
├── prompts/
│   └── prompt_templates.py    # AI prompt templates
├── services/
│   ├── oci_client.py          # OCI Grok API client
│   ├── document_service.py    # Document processing utilities
│   └── content_generator.py   # Content generation orchestrator
├── utils/
│   └── response_formatter.py  # Response formatting utilities
├── requirements.txt           # Python dependencies
├── .env.example              # Environment variables template
└── README.md                 # This file
```

## Features

- **Modular Architecture**: Clean separation of concerns with dedicated services
- **External Prompt Management**: All AI prompts stored in separate configuration files
- **Comprehensive Logging**: Structured logging throughout the application
- **Error Handling**: Robust error handling with meaningful error messages
- **Flexible Configuration**: Environment-based configuration management
- **Multiple Output Formats**: JSON and HTML response formats
- **Architecture Diagram Analysis**: AI-powered diagram analysis using vision capabilities

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure OCI credentials (standard OCI CLI configuration)

3. Copy and configure environment variables:
```bash
cp .env.example .env
```

4. Run the application:
```bash
python main.py
```

## API Endpoints

### POST /generate/
Returns generated content as JSON

### POST /generate-html/
Returns generated content as formatted HTML

### GET /
Health check endpoint

### POST /chat-rag/
RAG chat endpoint backed by OCI Generative AI Agent Runtime.

Request body:
```json
{ "message": "your question", "session_id": "optional-session-id" }
```

## Usage

### JSON Response
```bash
curl -X POST "http://localhost:8000/generate/" \
  -F "file=@template.docx" \
  -F "customer=ACME Corp" \
  -F "application=ERP System" \
  -F "scope=Cloud Migration" \
  -F "diagram=@architecture.png"
```

### HTML Response
```bash
curl -X POST "http://localhost:8000/generate-html/" \
  -F "file=@template.docx" \
  -F "customer=ACME Corp" \
  -F "application=ERP System" \
  -F "scope=Cloud Migration" \
  -F "diagram=@architecture.png"
```

## Customization

### Adding New Prompts
Add new prompts to `prompts/prompt_templates.py`:

```python
SECTION_PROMPTS = {
    "your_new_section": """
    Your custom prompt template here.
    Use {customer}, {application}, {diagram_description} as placeholders.
    """
}
```

### Modifying Configuration
Update settings in `config/settings.py` or use environment variables.

### Custom Response Formatting
Modify response formatting in `utils/response_formatter.py`.

## Logging

The application uses structured logging. Logs include:
- Request processing status
- AI API calls
- Error conditions
- Performance metrics

## Error Handling

- Comprehensive exception handling
- Meaningful error messages
- Automatic cleanup of temporary files
- Graceful degradation for failed operations



### Nginx reverse proxy notes (`405 Not Allowed`)
If you deploy frontend + backend behind Nginx, map an API prefix to FastAPI so POST requests do not hit static-file locations. Example:

```nginx
server {
    listen 443 ssl;
    server_name sowgen.enrot.es;

    # Frontend static files
    root /var/www/sowGeneratorv2/frontend;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # Backend API (FastAPI/Uvicorn)
    location /api/ {
        proxy_pass http://127.0.0.1:8001/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

With this setup, frontend calls should use `/api/chat-rag/` (which this project now tries first).
