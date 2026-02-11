# SoW End-to-End Flow (Document → RAG → Draft)

This document explains the full flow we are considering to create a **Statement of Work (SoW)**, starting from source documents through chunking/ingestion and ending with final section drafting.

---

## 1) Source Document Preparation

### Inputs
- Existing SoW templates
- Legal/contract clauses
- Technical standards and architecture patterns
- Compliance and regional policy documents

### Goal
Create a curated knowledge base (KB) that is:
- Easy to retrieve from by metadata filters
- Auditable (source traceability)
- Safe for controlled generation

---

## 2) Document Chunking

### What happens
1. Raw documents are split into **semantic chunks** (small, meaningful text units).
2. Each chunk is persisted as a JSON object, typically shaped as:
   - `{ "text": "...", "metadata": {...} }`
   - or `{ "clause": "...", "metadata": {...} }`
3. Metadata is attached to each chunk, such as:
   - `section`, `clause_type`, `tags`, `risk_level`
   - `industry`, `region`, `deployment_model`, `architecture_pattern`

### Why chunking helps
- Improves retrieval precision
- Reduces context noise sent to the model
- Enables targeted filtering and fallback relaxation

---

## 3) KB Ingestion to OCI + Citation-Traceable Storage

### Storage pattern
- Chunks are stored in Object Storage with stable URIs (e.g., `oci://objectstorage/.../o/clauses/.../chunk-001.json`).
- OCI Agent Runtime indexes/references these chunk sources for retrieval.

### Why this matters
- Every retrieved result can be traced back to a concrete stored object.
- Citations become deterministic pointers to real source chunks.

---

## 4) Retrieval Stage (Where RAG is used)

RAG is used when we need grounded source clauses for a given SoW section.

### Section-scoped retrieval request
For each SoW section, the system sends a **structured retrieval query** (filters + section), not a free-form generation prompt.

### Deterministic citation-first adapter
Instead of trusting natural-language `answer` text from the agent, we use:
1. **Primary**: citation `source_text` from OCI Agent Runtime.
2. **Fallback**: if `source_text` is missing, fetch the cited Object Storage chunk by `source_uri` and read `text`/`clause` from JSON.

### Candidate normalization
Each retrieved chunk is normalized into a clause candidate with:
- stable `clause_id` (`chunkId` preferred, else hash of `source_uri`)
- `source_uri`, optional `document_id`, `title`
- **required non-empty `text`** (minimum length enforced)
- metadata/provenance

### Fallback strategy for low recall
If candidates are below minimum threshold, retry retrieval by relaxing filters in order:
1. `tags`
2. `industry`
3. `region`
4. `risk_level`

`section` is always kept to avoid cross-section contamination.

### Diagnostics logged
Per section attempt:
- filters used
- citations count
- candidates count
- how many came from citation `source_text` vs fetched object fallback

---

## 5) Draft Generation Stage (Where LLM is used)

LLM is used after retrieval to **compose** section outputs.

### Clause sections
- LLM receives retrieved candidate texts.
- Expected behavior: synthesize obligations/constraints/limitations grounded in retrieved clauses.
- Benefit: less hallucination because generation is tied to retrieved content.

### Technical sections
- LLM uses extracted intake context + allowed services.
- Retrieved clauses are secondary support context.

### Validation + retry
- Output is checked for required fields/min-content rules.
- If weak/empty, workflow can retry with retrieval fallback and/or generation retry policy.

---

## 6) Final SoW Assembly

Generated section outputs are assembled into the final SoW structure:
- standardized section schema
- traceable evidence path back to citations and source URIs
- deterministic workflow stages for auditability

---

## Where RAG vs LLM are Used (Quick View)

| Stage | Component | Role |
|---|---|---|
| Retrieval | **RAG / OCI Agent Runtime** | Find relevant, grounded clauses from KB |
| Candidate extraction | Deterministic adapter | Convert citations into usable clause payloads |
| Drafting | **LLM** | Compose readable SoW section content from retrieved evidence |
| Validation | Rule-based checks (+ optional LLM assist) | Enforce schema/quality and reduce TBD output |

---

## Why this architecture is beneficial

1. **Grounded outputs**: Retrieval is tied to KB citations, not fragile parsing of model prose.
2. **Lower hallucination risk**: Clause writing is evidence-backed.
3. **Deterministic behavior**: Structured candidate model + fallback logic + minimum text rules.
4. **Auditability**: Every clause can be traced to source URI/chunk.
5. **Better resilience**: If citation text is missing, object fetch fallback still recovers chunk content.
6. **Operational observability**: Attempt-level diagnostics make retrieval quality measurable and debuggable.

---

## Suggested future enhancements

- Add chunk quality scoring at ingestion time (length/clarity/metadata completeness).
- Add reranking model for better top-k precision before writing.
- Store retrieval diagnostics per run for trend monitoring and automatic threshold tuning.
- Add citation rendering in final SoW for reviewer transparency.
