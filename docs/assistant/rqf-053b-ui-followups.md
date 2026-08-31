# RQF-053B ? Assistant UI follow-ups

Status: CLOSED_TRACKED_FOLLOWUPS
Source: CodeRabbit review on PR #151
Scope: Assistant UI debt captured after merging the read-only step trace and source panel slice.

## Context

PR #151 shipped the backend `step_trace` / `source_panel` contract and deployed a frontend asset that renders `Pasos de trabajo` and `Fuentes usadas` in `/assistant`.

CI was green and the PR was merged. CodeRabbit left non-blocking comments that mostly target the external `Assistant.tsx` artifact / broader Assistant UI debt. These are intentionally tracked here rather than silently forgotten.

## Pending follow-ups

### RQF-053B-FU1 - Provider-key intake hardening

Status: CLOSED_BY_RQF_053C

Remove URL-based provider key intake from the Assistant UI. Do not read provider keys from search params, persist them to `localStorage`, or initialize session state from URL-derived credentials. Accept provider keys only through an explicit in-memory/session flow.

### RQF-053B-FU2 - Request fallback/replay safety

Status: CLOSED_BY_RQF_053C

Review Assistant fetch helpers so non-404 failures are rethrown instead of falling through to alternative candidates. Preserve authenticated error propagation and avoid replaying export POSTs after failures or aborts.

### RQF-053B-FU3 - Export intent precision

Status: CLOSED_BY_RQF_053C

Tighten `resolveExportIntent` so casual mentions of PDF/Excel/CSV in a question do not trigger export behavior. Require explicit export/download verbs or unambiguous bare-format commands.

### RQF-053B-FU4 ? Conversation history rendering

Status: CLOSED_BY_F41712CA2

Load persisted conversation history from `GET /conversations/{conversation_id}/messages` and map records into `ChatMessage` so workspace cards, traces, sources, and previews render for historical assistant messages. Memoize derived workspace panels for long conversations.

### RQF-053B-FU5 - Assistant mode validation

Status: CLOSED_BY_RQF_053C

Validate `assistantMode` read from storage against supported values before using it. Fall back to `ahorro` if stale or tampered.

### RQF-053B-FU6 ? Executive dashboard error state

Status: CLOSED_BY_66A726830

Distinguish failed executive dashboard loads from successfully loaded empty results. Render a visible error state before any empty-alerts message.

### RQF-053B-FU7 ? RAG ownership cleanup

Status: CLOSED_BY_3F20EA541

Review whether Assistant still owns RAG administration handlers/state. If not, remove or relocate them to `/RAG` to reduce Assistant UI surface area.

### RQF-053B-FU8 ? Rollback notes for external frontend assets

Status: CLOSED_BY_FBFA8D488

Deployment artifacts for external `goal-fest-page` assets should include the previous asset hash and/or an explicit rollback command.

### RQF-053B-FU9 ? Boundary tests for trace/source builders

Status: CLOSED_BY_4175D250F

Add tests for `build_specialist_workspace_step_trace` and `build_specialist_workspace_source_panel` with `live_lookup_performed=False` and malformed/empty list-like values.

## Priority

Recommended handling is complete for this tracker.

Closed in RQF-053C: FU1, FU2, FU3, FU5.
Closed in the 2026-08-31 consolidation branch:
FU4 (`f41712ca2`), FU6 (`66a726830`), FU7 (`3f20ea541`),
FU8 (`fbfa8d488`), FU9 (`4175d250f`).

## Boundary

These follow-ups no longer block the completed RQF-053B read-only trace/source contract in repository artifacts. Runtime claims still require applying the relevant artifact to the external frontend source, building static assets, and verifying the active `/assistant` bundle.
