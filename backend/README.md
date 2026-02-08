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
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

With this setup, frontend calls should use `/api/chat-rag/` (which this project now tries first).


### Nginx variant for `/composer/` frontend prefix
If your frontend is exposed under `/composer/` (as in your current Nginx config), either:

1. Add a global `/api/` proxy block, or
2. Add `/composer/api/` proxy block and call `POST /composer/api/chat-rag/`.

Example:

```nginx
location /composer/api/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /api/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

The chat frontend now tries `/composer/api/chat-rag/`, then `/api/chat-rag/`, then `/chat-rag/`.


### Troubleshooting `502 Bad Gateway` and `405 Not Allowed`
For the errors you reported:
- `POST https://sowgen.enrot.es/api/chat-rag/` -> `502`
- `POST https://sowgen.enrot.es/chat-rag/` -> `405`
- `POST https://sowgen.enrot.es/composer/api/chat-rag/` -> `501 Unsupported method`

Typical causes and checks:


1. **`501 Unsupported method` on `/composer/api/chat-rag/`**
   This usually means the request reached a static Python `http.server` upstream (frontend) instead of FastAPI.
   Point `/composer/api/` (or `/api/`) to the FastAPI port and keep static frontend on a different location.

2. **`405` on `/chat-rag/`**
   This is usually the static/frontend location in Nginx handling the request (not FastAPI).
   Use `/api/chat-rag/` or `/composer/api/chat-rag/` with a proxy location to backend.

3. **`502` on `/api/chat-rag/`**
   Nginx cannot reach the upstream FastAPI process (wrong port, process down, or bind mismatch).
   This repository's `scripts/start.sh` runs backend on **8000**, so if you use that script, proxy to `127.0.0.1:8000`.

4. **Verify backend process/port**
   ```bash
   ss -ltnp | rg ':(8000|8001)\b'
   curl -i http://127.0.0.1:8000/chat-rag/ -X POST -H 'content-type: application/json' -d '{"message":"ping"}'
   curl -i http://127.0.0.1:8001/chat-rag/ -X POST -H 'content-type: application/json' -d '{"message":"ping"}'
   ```
   One of those ports should answer from FastAPI (even with app-level error, it should not be Nginx HTML).

5. **Match Nginx upstream to real backend port**
   If backend is on `8000`, use:
   ```nginx
   location /api/ {
       proxy_pass http://127.0.0.1:8000/;
       proxy_http_version 1.1;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
   }
   ```

6. **Validate and reload Nginx**
   ```bash
   nginx -t
   systemctl reload nginx
   ```

7. **Check Nginx error logs while calling endpoint**
   ```bash
   tail -f /var/log/nginx/error.log
   ```
