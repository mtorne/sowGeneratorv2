# Swarm SoW Generator - Phase 1 MVP

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt
```

## Environment Variables

Set the following variables before running:

- `OCI_CONFIG_FILE`
- `OCI_PROFILE`
- `OCI_GENAI_ENDPOINT`
- `OCI_MODEL_ID`
- `OCI_COMPARTMENT_ID`
- `OCI_TEMPERATURE`

Optional:

- `OCI_TIMEOUT_CONNECT`
- `OCI_TIMEOUT_READ`

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API

- `GET /health`
- `POST /generate-sow`

### Example Request

```json
{
  "client": "Cegid",
  "project_name": "xrp Modernization",
  "cloud": "OCI",
  "scope": "Refactor monolith to microservices",
  "duration": "4 months"
}
```

### Example Response

```json
{
  "file": "output_xxxxx.docx"
}
```
