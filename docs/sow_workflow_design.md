# SoW Generator v2 — Deterministic PLAN → RETRIEVE → ASSEMBLE → WRITE → REVIEW Design

## 1) User journey mapped to PLAN → RETRIEVE → ASSEMBLE → WRITE → REVIEW

### A. PLAN (mandatory first stage)
**User actions**
- User creates a new SoW request and enters: client profile, project scope, delivery model, assumptions, commercial constraints, region/jurisdiction, and desired tone.
- User selects SoW template type (e.g., implementation, managed services, advisory).

**System actions**
- Planner agent produces a **SoW Plan** object containing:
  - required sections/subsections,
  - section intents and evidence needs,
  - mandatory risk checks per section,
  - retrieval plan (section-by-section query specs).
- Plan is persisted and versioned before any retrieval is allowed.

**User-visible output**
- “Plan Board” showing section list, purpose, and rationale.
- User can edit scope parameters and re-run planning only (without drafting yet).

### B. RETRIEVE (strictly section-scoped)
**User actions**
- User confirms plan and optionally pins constraints (e.g., “low liability language”, “fixed-fee milestones”).

**System actions**
- Retriever executes **only plan-declared retrieval specs** per section.
- Query filters use metadata: `{section, clause_type, risk_level, industry, region, delivery_model}`.
- Returns top-k clause candidates with provenance, scores, and risk tags.

**User-visible output**
- “Evidence Drawer” per section with selected clauses and why each was selected.
- No drafting yet; user can approve/reject clauses.

### C. ASSEMBLE (grouping and ordering)
**User actions**
- User reviews clause sets and can reorder priorities or mark exclusions.

**System actions**
- Assembler groups clauses into section bundles:
  - primary clauses,
  - optional alternatives,
  - conflict flags (e.g., contradictory limitation of liability language).
- Produces section blueprints with ordered clause intent sequence.

**User-visible output**
- “Section Blueprint” view showing clause lineage and assembly decisions.

### D. WRITE (controlled drafting only)
**User actions**
- User starts draft generation for all sections or selected sections.

**System actions**
- Writer receives only:
  - section blueprint,
  - approved clause bundles,
  - style/tone policy,
  - prohibited commitments list.
- Writer generates new prose per section, citing source clause IDs used.
- Writer cannot call KB directly and cannot perform open-ended search.

**User-visible output**
- Draft section text with trace panel: which clause IDs influenced each paragraph.

### E. REVIEW (risk, consistency, tone gate)
**User actions**
- User requests formal review and receives actionable issues.

**System actions**
- Reviewer validates:
  - risk policy conformance,
  - internal consistency (dates, scope, SLAs, deliverables),
  - tone and obligation strength,
  - unsupported statements (text not grounded in assembled clauses/plan).
- Produces pass/fail status and remediation suggestions.

**User-visible output**
- “Review Report” with issue severity, location, policy reference, and one-click fix workflow.
- Final export is enabled only when critical checks pass or approved overrides are logged.

---

## 2) High-level architecture (services, AI roles, data flow)

### Core components
1. **Web App (Consultant UI)**
   - Workflow wizard, plan board, evidence drawer, blueprint viewer, draft editor, review dashboard.
2. **Workflow Orchestrator API**
   - Deterministic state machine enforcing stage order and transitions.
3. **Role Services (AI-backed)**
   - Planner Service
   - Retriever Service
   - Assembler Service
   - Writer Service
   - Reviewer Service
4. **Knowledge Access Service**
   - Metadata-filtered retrieval over Object Storage-backed KB index.
5. **Policy Engine**
   - Risk rubric, forbidden language, commitment thresholds, jurisdictional constraints.
6. **Document Store**
   - SoW request, stage artifacts, drafts, review reports, approvals, audit log.
7. **OCI GenAI Gateway**
   - Centralized inferencing client, model routing, prompt templates, token and trace logging.

### Data flow (deterministic)
1. UI submits intake → Orchestrator creates `SOW_CASE`.
2. Orchestrator invokes Planner → saves `PLAN_Vn`.
3. Orchestrator invokes Retriever per planned section → saves `RETRIEVAL_SET`.
4. Orchestrator invokes Assembler → saves `ASSEMBLY_BLUEPRINT`.
5. Orchestrator invokes Writer per section → saves `DRAFT_Vn`.
6. Orchestrator invokes Reviewer → saves `REVIEW_REPORT`.
7. UI surfaces results; user remediation loops are controlled stage regressions (e.g., REVIEW→WRITE only).

---

## 3) Backend orchestration logic enforcing each step

### State machine
`INIT -> PLAN_READY -> RETRIEVED -> ASSEMBLED -> DRAFTED -> REVIEWED -> APPROVED/REWORK`

### Transition rules
- `INIT -> PLAN_READY`: requires valid intake schema.
- `PLAN_READY -> RETRIEVED`: requires approved/locked plan version.
- `RETRIEVED -> ASSEMBLED`: requires minimum evidence coverage by section.
- `ASSEMBLED -> DRAFTED`: requires zero unresolved clause conflicts or explicit overrides.
- `DRAFTED -> REVIEWED`: requires all mandatory sections drafted.
- `REVIEWED -> APPROVED`: only if no critical policy violations.

### Enforcement patterns
- Stage-specific API tokens (capability-scoped service credentials).
- Idempotency keys for each stage run.
- Immutable stage artifacts; modifications create new versions.
- Hard denial of Writer invocation when `ASSEMBLY_BLUEPRINT` absent.

### Minimal API surface (example)
- `POST /sow-cases`
- `POST /sow-cases/{id}/plan`
- `POST /sow-cases/{id}/retrieve`
- `POST /sow-cases/{id}/assemble`
- `POST /sow-cases/{id}/write`
- `POST /sow-cases/{id}/review`
- `POST /sow-cases/{id}/approve`
- `GET /sow-cases/{id}/artifacts/{stage}`

Each endpoint validates prior stage completion and current artifact version.

---

## 4) AI role definitions

## Planner (structure & intent)
- **Purpose**: Build SoW skeleton and section intents from intake.
- **Does**: define section goals, required evidence, retrieval specs, and risk checkpoints.
- **Does not**: draft full section prose.
- **Output**: machine-readable plan JSON + human-readable rationale.

## Retriever (clause selection via KB)
- **Purpose**: fetch best clause candidates per section intent.
- **Does**: metadata-first retrieval, scoring, dedupe, provenance capture.
- **Does not**: rewrite clauses or synthesize final content.
- **Output**: ranked clause sets per section with trace metadata.

## Assembler (clause grouping & ordering)
- **Purpose**: convert retrieved clauses into coherent section blueprints.
- **Does**: cluster by sub-intent, sequence logically, flag contradictions.
- **Does not**: produce final polished section text.
- **Output**: ordered blueprint per section + conflict map.

## Writer (section drafting)
- **Purpose**: generate new section narrative from blueprint and approved clauses.
- **Does**: paraphrase/synthesize controlled inputs, preserve obligations accurately.
- **Does not**: call KB, introduce unsupported commitments, ignore risk policy.
- **Output**: draft section + source mapping at paragraph level.

## Reviewer (risk & consistency validation)
- **Purpose**: validate policy compliance and cross-document consistency.
- **Does**: run rule checks + model-assisted critique with explicit evidence.
- **Does not**: silently auto-fix critical issues.
- **Output**: structured findings (severity, location, rationale, fix suggestions).

---

## 5) Prompting strategy by AI role

## Planner prompt contract
- **Intent**: transform intake into explicit plan and retrieval requirements.
- **Inputs**: intake JSON, template taxonomy, risk policy matrix.
- **Outputs**:
  - `plan.sections[]` with `intent`, `must_include`, `retrieval_filters`, `risk_checks`.
  - `plan.validation_rules[]`.
- **Constraints in prompt**:
  - no drafting language,
  - produce deterministic JSON schema.

## Retriever prompt contract
- **Intent**: select relevant clause candidates per section.
- **Inputs**: section retrieval spec + allowed metadata filters + kb search results.
- **Outputs**: ranked list with `clause_id`, `score`, `fit_reason`, `risk_level`, `source_uri`.
- **Constraints**:
  - section scope only,
  - reject clauses missing required metadata.

## Assembler prompt contract
- **Intent**: build coherent section blueprint from candidates.
- **Inputs**: ranked clauses, section intent, conflict rules.
- **Outputs**: `blueprint.order[]`, `primary_clause_ids[]`, `alternatives[]`, `conflicts[]`.
- **Constraints**:
  - no prose drafting beyond short glue notes,
  - explicit conflict detection required.

## Writer prompt contract
- **Intent**: produce final section draft from blueprint.
- **Inputs**: blueprint + approved clauses + style profile + forbidden commitments.
- **Outputs**: drafted section text + paragraph-to-clause mapping.
- **Constraints**:
  - no external retrieval,
  - any claim must map to clause IDs or intake facts,
  - if insufficient evidence, emit `NEEDS_INPUT` markers.

## Reviewer prompt contract
- **Intent**: detect risk, consistency, and tone problems.
- **Inputs**: full draft, policy rules, plan, blueprint, source mappings.
- **Outputs**: findings array with `severity`, `type`, `location`, `evidence`, `recommendation`.
- **Constraints**:
  - no silent rewrite,
  - must explain every critical flag with policy reference.

---

## 6) Knowledge Base access rules and retrieval constraints

- Retrieval allowed only in RETRIEVE stage (and controlled re-retrieve from review remediation).
- Query must include `section` and at least one additional metadata filter.
- Risk-aware filtering: section may specify maximum allowed risk level.
- Enforce top-k per section/sub-intent to avoid noisy context.
- Hard provenance requirement: each clause must carry object URI and chunk ID.
- Deduplication by semantic hash and source lineage.
- Region/jurisdiction filter mandatory for legal/obligation sections.
- Writer receives only assembled clause payload, never raw KB search API handle.

---

## 7) Guardrails against hallucinations and over-commitment

- **Grounding gate**: paragraph-level source mapping required; unmapped text is blocked or flagged.
- **Commitment classifier**: detect absolute verbs (“guarantee”, “ensure without exception”).
- **Risk policy checks**: compare generated obligations vs allowed risk profile.
- **Numeric consistency checks**: milestones, payment terms, SLA thresholds cross-validated.
- **Forbidden phrase list**: jurisdiction-specific disallowed terms.
- **Confidence routing**: if evidence coverage < threshold, route to user clarification instead of generation.
- **Deterministic fallbacks**: if model output invalid JSON, use constrained re-prompt templates and retry budget.

---

## 8) UX elements for visibility and explainability

- Stepper with hard gates: PLAN, RETRIEVE, ASSEMBLE, WRITE, REVIEW.
- Artifact panels per stage (plan JSON, retrieved clauses, blueprints, drafts, findings).
- “Why this clause?” explanation chips (metadata match + score rationale).
- Draft trace mode: click paragraph to see source clauses and policy checks.
- Review heatmap: severity markers across sections.
- Override workflow requiring reason + approver identity for critical issues.
- Version timeline for all stage artifacts and model runs.

---

## 9) Error handling and recovery by stage

## PLAN
- **Errors**: incomplete intake, invalid template selection.
- **Recovery**: schema-level validation messages; partial save; guided field completion.

## RETRIEVE
- **Errors**: insufficient matching clauses, metadata mismatch, KB timeout.
- **Recovery**: broaden filters in controlled order, fallback to nearest allowed risk band, retry with backoff, escalate to user for missing constraints.

## ASSEMBLE
- **Errors**: clause conflicts, coverage gaps.
- **Recovery**: conflict resolution UI with alternatives; force explicit override for unresolved conflicts.

## WRITE
- **Errors**: unsupported claims, missing evidence, invalid output format.
- **Recovery**: automatic constrained rewrite with stricter prompt; insert `NEEDS_INPUT`; block finalization until resolved.

## REVIEW
- **Errors**: policy engine unavailable, ambiguous findings.
- **Recovery**: fail closed for critical policy checks; rerun with deterministic rules engine; mark model-only findings as advisory until confirmed.

---

## 10) Extensibility for new document types and industries

- Introduce `DocumentTypeConfig`:
  - section taxonomy,
  - required metadata filters,
  - risk rule sets,
  - tone profiles,
  - review checklist.
- Keep orchestration flow unchanged; swap configs per type (MSA, proposal, change order, SoW).
- Industry packs (public sector, healthcare, BFSI) define:
  - domain-specific clause tags,
  - compliance constraints,
  - prohibited/required language.
- Prompt templates are role-stable but config-parameterized.
- Add new KB indexes per industry while preserving shared retrieval contract.
- Regression suite validates same stage gates across document types.

---

## Implementation notes (practical defaults)

- Use OCI GenAI via one gateway service with template versioning and response schemas.
- Persist every stage artifact with checksum and lineage pointers for auditability.
- Prefer synchronous stage completion for PLAN/ASSEMBLE, async jobs for RETRIEVE/WRITE/REVIEW with progress events.
- Capture token-level cost and latency per stage for operational tuning.
