# SamChat current roadmap handoff

Status: ACTIVE_CONTEXT
Last updated: 2026-08-24
Branch at time of writing: `codex/rqf-053d-owner-pack-dashboard`

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

Status: IMPLEMENTED_PENDING_PR

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
