# SamChat current roadmap handoff

Status: ACTIVE_CONTEXT
Last updated: 2026-08-24
Branch at time of writing: `codex/rqf-054def-executive-workloop`

This file exists so the roadmap survives conversation compaction. Read this before continuing assistant/owner-pack/SOUL work.

## Product north star

SamChat is an operational assistant built with the Claude Code paradigm: it understands a business request, inspects available context, uses governed business tools, generates artifacts, proposes actions, and executes only with explicit authority.

It is not a dashboard with chat. Dashboards are auxiliary surfaces. The assistant is the primary operational interface.

## Current architectural guardrails

- Do not wire artifacts just to wire them.
- For each candidate artifact: inspect implementation, usage, tests, overlap, and fit against real user questions before exposing it.
- Read-only evidence and previews may move quickly.
- Writes remain behind preview, explicit human authority, idempotency, audit, and receipt boundaries.
- Memory/precedent informs decisions; it never grants authority.
- Do not expand scope into hardening or infrastructure unless it protects the current slice.
- Do not route a user question directly to a tool unless the turn can also prove that the tool result answers the actual business question.

## Main roadmap lanes

### Lane A - Assistant institutional intelligence

Goal: make the assistant answer operational questions using institutional artifacts and evidence-backed context.

Recently completed on this branch:

- `finance.closeout_diagnostics` tool and tests.
- `assistant_institutional_artifacts` registry tool and tests.
- `assistant.owner_entity_dossier_audit` artifact audit.
- `assistant.sports_platform_audit` artifact audit.

Important decisions:

- `sports.director_general_entity_dossier` is useful but must not be exposed raw.
- `sports.platform_snapshot` is useful but too broad; expose narrowed operational views later.
- `tournament.soul_snapshot` is wired, but coverage is incomplete because there should be one SOUL per tournament and currently coverage appears thin.

### Lane B - Owner pack / Director General requests

Goal: support the owner request for tournament/entity folders with operations, finance, national phase, marketing evidence, and missing-information tracking.

Current state:

- Owner pack live snapshot/status/inventory exist.
- Entity dossier audit exists.
- Platform snapshot audit exists.
- Still needs reliable SOUL coverage per tournament before promising complete owner folders.

Do not claim full owner-pack readiness until the SOUL data exists for each tournament.

### Lane C - SOUL Wizard

Goal: support the main Assistant roadmap by giving each tournament a structured SOUL context that the assistant, owner pack, operations dashboards, and future proposal flows can inspect.

Important hierarchy: SOUL Wizard is not the principal product roadmap. It is an enabling layer underneath the Assistant/Owner Pack roadmap. Do not let SOUL implementation displace the higher-level assistant objective.

See `docs/assistant/soul-wizard-roadmap.md` for the four-cut mini-roadmap.

Completed:

- `RQF-SOUL-WIZARD-001` - draft contract and validation.
- `RQF-SOUL-WIZARD-002` - admin UI stepper.
- `RQF-SOUL-WIZARD-003` - clone from existing tournament/SOUL snapshot.
- `RQF-SOUL-WIZARD-004` - activation preview/diff contract and UI.

Next:

- Return to the Assistant/Owner Pack roadmap.
- Choose whether the next assistant-facing slice consumes SOUL drafts, owner dossiers, or both.
- Do not implement real tournament creation from SOUL until an explicit authority path is opened.

### Lane D - Buzz / multiagent collaboration spike

Tracked separately as `RQF-SAMCHAT-BUZZ-001` and GitHub issue #148.

Status: roadmap-added experimental spike only.

Rules:

- Do not let Buzz block ordinary assistant roadmap work.
- No production writes.
- No real fiscal documents in the spike.
- Raw documents remain in deterministic evidence enclave.
- Buzz rooms are collaborative, not authoritative.

See:

- `docs/assistant/rqf-samchat-buzz-001-phase-0.md`
- `docs/assistant/rqf-assistant-009-quality-roadmap.md`

### Lane E - Finance/accounting/operations product backlog

This lane contains customer-facing operational modules already worked in prior sprint branches:

- AMEX reconciliation.
- Bank reconciliation and cash flow.
- CxC/CxP.
- SAT massive download and CFDI matching.
- Accounting cleanup and prepolizas.
- Beneficiary/third-party requests.
- Authorization profiles/strategy.
- Materialities, CFDI totals, tips, ISH, no-deductible rules.

Do not lose this lane, but the current thread focus returned to assistant/owner/SOUL after several operational bug pauses.

### Lane F - Claude-Code-like assistant runtime contract

Status: RQF-054D/E/F_CLOSED_MERGED_DEPLOYED

Reference: `docs/assistant/rqf-054-claude-code-runtime-gap.md`

Why this exists:

- The assistant has many useful artifacts and tools, but it can still answer like a brittle keyword router.
- The observed failure was semantic: `evidence of payments made` was routed to `pending payments` because both share payment vocabulary.
- Claude Code's core value is not the terminal UI; it is the governed work loop: understand task, load context, select tools, execute under policy, verify sufficiency, render a human answer, and persist continuity.

Implementation order and current state:

1. RQF-054A - WorkFrame classifier. Status: merged/deployed in PR #220.
2. RQF-054B - Tool candidate adjudicator. Status: merged/deployed in PR #221.
3. RQF-054C - Sufficiency gate. Status: merged/deployed in PR #221. Active release before this slice: `/srv/samchat/releases/gastos-prod-40ebe85d-rqf054bc-sufficiency`.
4. RQF-054D - Unified executive renderer. Status: implemented locally on `codex/rqf-054def-executive-workloop`, merged/deployed in PR #222.
5. RQF-054E - Claude-Code-like turn trace. Status: implemented locally on `codex/rqf-054def-executive-workloop`, merged/deployed in PR #222.
6. RQF-054F - Regression set for executive questions. Status: partially advanced through semantic registry and focused tests; next slice should add broad multi-candidate read-only execution plus a stable executive regression set.

Critical invariant:

- SamChat must not merely choose a route. It must prove that the selected route answers the user's business question. If it cannot prove that, it must say what evidence is missing or ask the smallest useful follow-up.

## Current branch commits to remember

Recent commits on this branch include:

- `8d67f91 assistant: add soul wizard admin stepper`
- `a487e2c assistant: add soul wizard draft contract`
- `0faa904 assistant: audit sports platform artifact`
- `585e449 assistant: audit owner entity dossier artifact`
- `d4311cb assistant: expose institutional artifact registry tool`
- `5afebd2 assistant: add institutional artifact registry`
- `caf4cb8 assistant: add finance closeout diagnostics`

## Where to resume if compacted

1. Check `git status --short --branch`.
2. Read this file.
3. Read `docs/assistant/soul-wizard-roadmap.md`.
4. Treat the SOUL four-cut mini-roadmap as closed for read-only draft/review.
5. Resume the primary Assistant/Owner Pack roadmap unless the user explicitly changes focus.
6. Keep future SOUL work scoped: no production writes before preview, approval, idempotency, audit, and receipt boundaries.

## Remote working notes

The active repo has been worked on remotely at:

`/tmp/samchat-propina-edit-field`

Typical verification command:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest <tests> -q
```

Use ASCII in scripted remote file writes when possible; prior PowerShell-to-SSH sessions can corrupt accented characters.


## 2026-08-24 Assistant 053 slices

### Slice 2A - Executive Answer Renderer

Status: CLOSED_MERGED_DEPLOYED

Evidence:

- PR #206 merged.
- Merge commit: `8cb26ca02937a221a0b9d22a22c4bbf3cb9a947d`.
- Active release after deployment: `/srv/samchat/releases/gastos-prod-8cb26ca02-assistant053c-exec-answers`.

Purpose:

- Prevent deterministic assistant tools from surfacing raw function-call JSON as the final user-facing answer.
- Prefer `conversation_answer.rendered_text` when present.
- Render structured read-only operational reports as executive Spanish answers with status, evidence, gaps, next questions, and authority boundary.

### Slice 2B - Owner Pack Readiness Dashboard / respuesta navegable

Status: CLOSED_MERGED_DEPLOYED

Objective:

- Provide a minimal navigable surface that shows what the Owner Pack has and what it lacks before claiming readiness.

Closure contract:

- Shows tournament context.
- Shows entity folder readiness.
- Shows national phase readiness.
- Shows marketing readiness.
- Shows coverage percentage or state.
- Shows missing items.
- Shows available sources.
- Shows next questions.

Guardrails:

- Read-only only.
- No folder creation, no durable writes, no notifications, no authority escalation.
- A dashboard may say the pack is incomplete; that is a valid executive answer.
- The dashboard must remain stable across scopes: all four sections appear even when one section was not evaluated in the current scope.

## 2026-08-17 note

SOUL Wizard 003/004 were completed after the original handoff. SOUL now has clone and activation-preview support, but remains read-only infrastructure underneath the main Assistant roadmap.

### Slice 3 - Owner Variable Q&A (RQF-053E)

Status: CLOSED_MERGED_DEPLOYED.

Closure contract:

- concrete Owner Pack variables answer through deterministic read-only logic;
- supported variables cite live evidence when present;
- recognized-but-missing variables say `No hay dato soportado`;
- no people, phones, dates, amounts, teams, categories, or visits are invented;
- readiness questions like `Que falta para la carpeta de Jalisco?` remain on the readiness dashboard path.

Focused evidence: `tests/unit/test_assistant_owner_variable_query.py`, `tests/unit/test_assistant_owner_variable_answer.py`, and `tests/unit/test_assistant_request_router_integration.py` pass locally as 39/39.


### Slice 4 - Entity Folder Workspace (RQF-053F)

Status: CLOSED_MERGED_DEPLOYED.

Objective:

- Convert the Owner Pack preview into a legible operational folder workspace.

Closure contract:

- Shows an explicit `Operaciones` drawer.
- Shows an explicit `Finanzas` drawer.
- Shows missing fields/faltantes that block complete claims.
- Shows available evidence.
- Shows non-claims.
- Shows suggested next questions.
- Keeps export/preview read-only: no folder creation, no export, no notifications, and no business-state mutation.

Implementation notes:

- `src/samchat/assistant/owner_entity_folder_workspace.py` now conservatively re-buckets already discovered evidence into `operations` and `finance` sections before diagnostic sections.
- `src/samchat/assistant/conversation_service.py` renders a human-readable workspace with Operaciones, Finanzas, Faltantes, Evidencia, No-claims, Preguntas sugeridas, and read-only authority boundary.
- The bucketing does not infer new facts; it only reorganizes facts/evidence/missing items already present in readiness and dossier artifacts.

Focused evidence:

- `.venv/bin/pytest -q tests/unit/test_assistant_owner_entity_folder_workspace.py` passes locally as 6/6.


### Slice 5 - Artifact connection review (RQF-053G)

Status: CLOSED_MERGED_DEPLOYED.

Objective:

- Retake the assistant-facing institutional artifact inventory with an explicit verdict per artifact.

Closure contract:

- Every artifact receives exactly one decision: conectar ahora, mantener interno, fusionar con otro, obsoleto, or necesita datos antes.
- Merge decisions name a merge target.
- Needs-data decisions name concrete data prerequisites.
- The review is read-only and does not wire new tools, trigger writes, publish folders, or grant authority.

Implementation notes:

- `src/samchat/assistant/institutional_artifact_registry.py` now includes `ARTIFACT_CONNECTION_DECISIONS`.
- `build_institutional_artifact_connection_review()` groups artifacts by decision and returns non-claims.
- `docs/assistant/rqf-053g-artifact-connection-review.md` records the human-readable verdict table.

Current verdict summary:

- conectar ahora: 13
- mantener interno: 1
- fusionar con otro: 4
- obsoleto: 0
- necesita datos antes: 3

Next after merge/deploy:

- Use the review to choose the next connection slice, likely consolidating raw Sports/DG artifacts into the existing Owner Pack workspace rather than adding another surface.


### Slice 6 - Assistant UI revamp, primera pasada (RQF-053H)

Status: IMPLEMENTED_STATIC_DEPLOYED.

Objective:

- Make the assistant feel like an operational workspace rather than a raw debug surface, without changing authority or enabling writes.

Closure contract:

- Conversation remains primary and raw tool traces stay collapsed.
- Existing source cards remain visible as `Fuentes usadas`.
- New `Artefactos` cards surface artifact/readiness/review payloads.
- New `Faltantes` panel surfaces missing fields, missing evidence, missing items, needs-data items, and next questions.
- New `Acciones propuestas` panel separates proposals from facts and marks them as requiring authority.
- Assistant responses backed by tools/previews show a read-only badge.

Implementation notes:

- The active frontend source remains external to this backend repo at `/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx`.
- A traceability snapshot is stored at `artifacts/rqf-053h-assistant-ui-revamp/Assistant.tsx`.
- Static assets were built and copied to `/srv/samchat/current/goal-fest-page/dist`.

Focused evidence:

- `npm run build` passed in the frontend project.
- Deployed bundle contains markers `Read-only`, `Faltantes`, `Acciones propuestas`, and `Artefactos`.
- `/healthz` and `/readyz` returned OK after static deployment.

Next slice:

- Continue with Slice 7: Persistent case memory, unless we first consolidate frontend source into the backend release packaging to eliminate this external-static-assets footgun.

## 2026-08-24 RQF-054A WorkFrame

Status: MERGED_AND_DEPLOYED

What changed:

- Added WorkFrame classification before assistant turn routing.
- WorkFrame records interpreted goal, audience, domain, task kind, required evidence, forbidden interpretations, temporal scope, and read-only authority boundary.
- WorkFrame is attached as the final tool trace so existing primary tool trace contracts remain intact.

Why it matters:

- `evidence of payments made` is no longer semantically equivalent to `pending payments`.
- Broad owner readiness, concrete owner variables, finance/accounting questions, SOUL coverage, and unknown requests now have a tested frame before tool selection.

Next:

- RQF-054B/C were merged/deployed in PR #221.
- RQF-054D/E plus semantic registry foundation are implemented locally on `codex/rqf-054def-executive-workloop`; focused verification is 93 passed. Next step is PR/CI/merge/deploy.


## 2026-08-24 RQF-054B/C Tool Adjudicator + Sufficiency Gate

Status: MERGED_AND_DEPLOYED

PR: #221
Active release after deployment: `/srv/samchat/releases/gastos-prod-40ebe85d-rqf054bc-sufficiency`

What changed:

- Tools are now judged as candidates against the WorkFrame before the final answer is trusted.
- The sufficiency gate can replace an answer with a bounded gap response if the tool/result does not answer the user's actual business question.
- The known bad path is covered: asking for evidence of payments already made cannot be answered by `receipts.pending_payment_overview`.
- Primary tool trace remains at index 0; governance traces are appended so existing UI contracts remain stable.

Focused verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_assistant_tool_adjudicator_sufficiency.py tests/unit/test_assistant_work_frame.py tests/unit/test_assistant_request_router_integration.py
# 44 passed
```

Next after PR/merge/deploy:

- Expand candidate registry using `tool_registry.py` metadata instead of only hard-coded invariants. Done locally in RQF-054D/E/F foundation.
- Add multi-candidate read-only execution for broad executive questions. Pending next slice.
- Keep writes disabled until authority path design is closed.


## 2026-08-24 RQF-054D/E/F foundation

Status: CLOSED_MERGED_DEPLOYED

PR: #222
Merge commit: `53dc8ee6da3c9023a2b505f0da75400b847701f4`
Active release after deployment: `/srv/samchat/releases/gastos-prod-53dc8ee6-rqf054def-workloop`

What changed:

- Added semantic assistant tool registry metadata to reduce brittle keyword routing.
- The tool adjudicator now accepts/rejects candidates against WorkFrame domain and task-kind using registry metadata.
- Added a unified WorkTurn renderer that renders sufficiency gaps, blocks raw tool payloads, appends the read-only authority boundary, and preserves controlled deterministic surfaces.
- Added `assistant.work_turn_trace` so the UI can show the Claude-Code-like loop as steps rather than debug traces.
- Kept the primary business tool trace first and `assistant.work_frame` final for compatibility.

Focused verification:

- Py compile passed for changed assistant modules.
- Focused assistant suite passed: 93/93.

Next:

- Implement multi-candidate read-only execution and an executive regression pack for owner/finance/accounting questions.
