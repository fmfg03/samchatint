# SamChat current roadmap handoff

Status: ACTIVE_CONTEXT
Last updated: 2026-08-31
Branch at time of reconciliation: `codex/feature-consolidation-20260830`

This file exists so the roadmap survives conversation compaction. Read this before continuing assistant/owner-pack/SOUL work.

## 2026-08-31 roadmap reconciliation

This reconciliation is the current entry point for assistant roadmap work.
It separates product direction, repository evidence, runtime evidence, and
remaining gaps so future slices do not infer production capability from a doc
heading, branch name, or release directory name alone.

Current active runtime checked during reconciliation:

- Service: `samchat-gastos.service`
- WorkingDirectory: `/srv/samchat/releases/gastos-prod-42bf8d6a-expense-report-controls`
- Health: `/healthz` and `/readyz` returned healthy.
- Important nuance: the active release name is expense-report-oriented, but its
  loaded source contains the assistant WorkFrame, work-turn trace,
  multi-candidate read-only, executive regression, and Owner Entity Folder
  Workspace modules.

Clean-room architecture boundary:

- `/root/claudeleaked` is an architecture reference only.
- Do not copy source, names, or implementation details from it into SamChat.
- Transferable patterns are the governed work loop, candidate tool selection,
  policy checks, context/memory selection, visible traces, sufficiency
  verification, preview/approval boundaries, and auditable execution receipts.
- A Claude-Code-like pattern is accepted only when it improves SamChat's real
  operational cycle over gastos, CFDI, Owner Pack, tournaments, SOUL, and
  authorization boundaries.

| Slice | Product capability | Repo evidence | Runtime evidence | Remaining gap | Authority posture |
| --- | --- | --- | --- | --- | --- |
| RQF-054A | WorkFrame classifier | `src/samchat/assistant/work_frame.py`; tests in `tests/unit/test_assistant_work_frame.py` | Present in active release source | Keep extending real business phrases as failures are found | Read-only |
| RQF-054B/C | Tool candidate adjudicator and sufficiency gate | `tool_adjudicator.py`, `response_sufficiency.py`, focused tests | Present in active release source | Continue mapping tools through semantic metadata instead of ad hoc keywords | Read-only |
| RQF-054D/E/F | Executive renderer, work-turn trace, semantic registry foundation | `work_turn_renderer.py`, `assistant_workspace_trace.py`, `tool_registry.py` | Present in active release source | Keep broadening source-backed rendered answers; do not render raw payloads | Read-only |
| RQF-054G | Multi-candidate read-only execution | `multi_candidate_readonly.py`; focused local verification passed 2026-08-31 | Present in active release source | Reconcile historical `IMPLEMENTED_LOCAL` labels with PR/release evidence before claiming formal closure | Read-only |
| RQF-054H | Executive regression suite for real questions | `executive_regression_suite.py`; focused local verification passed 2026-08-31 | Present in active release source as source module | Promote from local regression contract into demo/canary gate with captured live prompts | Read-only |
| RQF-053H/UI | Operational workspace UI first pass | Static snapshot under `artifacts/rqf-053h-assistant-ui-revamp/`; PR #151 follow-ups FU4/FU6/FU7/FU8/FU9 closed in repository artifacts/tests | Prior static deployment evidence exists; runtime assets must be rechecked before UI claims | Apply artifact to external active frontend, rebuild static assets, capture rollback receipt, and verify runtime bundle before polished UI claims | Read-only/proposal |
| SOUL Wizard 001-004 | Tournament SOUL draft, clone, activation preview | `soul_wizard.py`; mini-roadmap closed | Source present; coverage remains data-dependent | One reliable SOUL per tournament before complete Owner Pack claims | Preview-only, no operational writes |
| Owner Pack workspace | Read-only entity folder readiness/evidence workspace | `owner_pack_*`, `owner_entity_folder_workspace.py`, owner variable Q&A | Present in active release source | Improve tournament/entity data coverage; report missing fields instead of completing gaps | Read-only/proposal |

Next execution order:

1. Keep this roadmap reconciliation current whenever runtime/source status
   changes.
2. Convert RQF-054H into the acceptance gate for assistant demo hardening:
   captured real prompts, expected source class, forbidden bad answers,
   provider/latency/timeout evidence, and pass/fail canary results.
3. When UI deployment is approved, apply the reconciled Assistant artifact to
   the external active frontend, rebuild static assets, capture rollback
   evidence, and verify the runtime `/assistant` bundle.
4. Improve SOUL and Owner Pack coverage per tournament/entity before promising
   complete folders.
5. Only after the read-only loop is stable, design one narrow write-capable path
   with versioned preview, explicit human approval, idempotency, audit trail,
   execution receipt, and rollback evidence.

Current non-goal: no production write path is enabled by this roadmap
reconciliation. SamChat may investigate, render evidence, show gaps, and propose
actions, but durable effects remain blocked until a separately approved
authority slice exists.

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

## 2026-08-25 RQF-054G Multi-candidate read-only execution

Status: IMPLEMENTED_LOCAL
Branch: `codex/rqf-054g-multicandidate-readonly`

What changed:

- Added `src/samchat/assistant/multi_candidate_readonly.py` as a deterministic selector for multiple already-executed read-only candidate answers.
- Broad owner/finance/mixed WorkFrames can now evaluate Owner variable Q&A, Owner Pack readiness, and Finance/Accounting Q&A candidates before choosing the visible answer.
- Wrong-but-safe candidates are retained in trace but not trusted when the sufficiency gate rejects them.
- The known failure mode remains blocked: evidence of payments already made is not answered by a zero pending-payment summary.
- The path remains read-only: no writes, no authority expansion, no provider execution added.

Focused verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q   tests/unit/test_assistant_multi_candidate_readonly.py   tests/unit/test_assistant_tool_adjudicator_sufficiency.py   tests/unit/test_assistant_work_frame.py   tests/unit/test_assistant_executive_answer_renderer.py   tests/unit/test_assistant_finance_accounting_qa.py   tests/unit/test_assistant_owner_pack_readiness.py
# 32 passed

PYTHONPATH=src .venv/bin/python -m pytest -q   tests/unit/test_assistant_request_router_integration.py   tests/unit/test_assistant_finance_read_adapter.py   tests/unit/test_assistant_owner_pack_readiness_dashboard.py   tests/unit/test_assistant_owner_pack_export_preview.py   tests/unit/test_assistant_owner_pack_export_preview_router.py
# 46 passed
```

Wider assistant regression:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_assistant_multi_candidate_readonly.py tests/unit/test_assistant_tool_adjudicator_sufficiency.py tests/unit/test_assistant_work_frame.py tests/unit/test_assistant_request_router_integration.py tests/unit/test_assistant_executive_answer_renderer.py tests/unit/test_assistant_tool_registry.py tests/unit/test_assistant_finance_accounting_qa.py tests/unit/test_assistant_owner_pack_readiness.py tests/unit/test_assistant_owner_pack_readiness_dashboard.py tests/unit/test_assistant_owner_pack_export_preview.py tests/unit/test_assistant_owner_pack_export_preview_router.py tests/unit/test_assistant_finance_read_adapter.py
# 82 passed
```

Next:

- RQF-054H: executive regression suite of real questions with expected answer class, expected source class, and forbidden bad answers.
- Keep improving candidate coverage before enabling any write/authority path.

## 2026-08-25 RQF-054H Executive regression suite of real questions

Status: IMPLEMENTED_LOCAL
Branch: `codex/rqf-054g-multicandidate-readonly`

What changed:

- Added `src/samchat/assistant/executive_regression_suite.py` as a deterministic contract for real executive questions.
- Added cases for Owner Pack readiness, owner payment evidence, accounting loaded, Payment Run, close blockers, CFDI gaps, and teams-by-category variable Q&A.
- Each case records expected domain, task kind, answer class, source class, expected tools, forbidden tools, required answer terms, and forbidden answer terms.
- The suite verifies output quality, WorkFrame classification, response sufficiency, read-only boundary, and absence of write traces.
- The known bad outputs are now regression failures: raw tool payloads/debug JSON, unicode gibberish loops, and answering historical payment evidence with a pending-payment queue.

Focused verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_assistant_executive_regression_suite.py
# 7 passed
```

Next:

- Expand the suite with captured live prompts from Francisco/Juan Pablo after each demo bug.
- Use the suite as the acceptance gate for Owner Pack demo hardening and Finance/Accounting Q&A.

## 2026-08-31 RQF-054H gate/canary runner

Status: IMPLEMENTED_LOCAL_PENDING_PR

What changed:

- Added `scripts/run_assistant_executive_canary.py`.
- Default fixture mode evaluates all current RQF-054H executive cases without
  HTTP, providers, credentials, or business writes.
- Live mode is opt-in and requires cookie or bearer auth before calling
  `/api/assistant`; credential values are never printed.
- Each canary row records case id, prompt, pass/fail, failures, HTTP status,
  latency, timeout flag, provider/model if present, tool count, tools, pending
  confirmation, write detection, and authority posture.
- Provider timeout, pending confirmation, or write trace fails the case even if
  the response is otherwise controlled.

Verification:

```bash
./scripts/pytestw tests/unit/test_assistant_executive_canary.py tests/unit/test_assistant_executive_regression_suite.py -q
./.venv/bin/python scripts/run_assistant_executive_canary.py --fixture
```

Next:

- Run live mode only with an authenticated session and record the JSON result as
  canary evidence.
- Add Francisco/Juan Pablo demo prompts after each observed failure, then use
  this runner as the acceptance gate for assistant demo hardening.
