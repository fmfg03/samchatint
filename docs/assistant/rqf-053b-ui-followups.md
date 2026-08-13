# RQF-053B ? Assistant UI follow-ups

Status: PENDING_BACKLOG
Source: CodeRabbit review on PR #151
Scope: Assistant UI debt captured after merging the read-only step trace and source panel slice.

## Context

PR #151 shipped the backend `step_trace` / `source_panel` contract and deployed a frontend asset that renders `Pasos de trabajo` and `Fuentes usadas` in `/assistant`.

CI was green and the PR was merged. CodeRabbit left non-blocking comments that mostly target the external `Assistant.tsx` artifact / broader Assistant UI debt. These are intentionally tracked here rather than silently forgotten.

## Pending follow-ups

### RQF-053B-FU1 ? Provider-key intake hardening

Remove URL-based provider key intake from the Assistant UI. Do not read provider keys from search params, persist them to `localStorage`, or initialize session state from URL-derived credentials. Accept provider keys only through an explicit in-memory/session flow.

### RQF-053B-FU2 ? Request fallback/replay safety

Review Assistant fetch helpers so non-404 failures are rethrown instead of falling through to alternative candidates. Preserve authenticated error propagation and avoid replaying export POSTs after failures or aborts.

### RQF-053B-FU3 ? Export intent precision

Tighten `resolveExportIntent` so casual mentions of PDF/Excel/CSV in a question do not trigger export behavior. Require explicit export/download verbs or unambiguous bare-format commands.

### RQF-053B-FU4 ? Conversation history rendering

Load persisted conversation history from `GET /conversations/{conversation_id}/messages` and map records into `ChatMessage` so workspace cards, traces, sources, and previews render for historical assistant messages. Memoize derived workspace panels for long conversations.

### RQF-053B-FU5 ? Assistant mode validation

Validate `assistantMode` read from storage against supported values before using it. Fall back to `ahorro` if stale or tampered.

### RQF-053B-FU6 ? Executive dashboard error state

Distinguish failed executive dashboard loads from successfully loaded empty results. Render a visible error state before any empty-alerts message.

### RQF-053B-FU7 ? RAG ownership cleanup

Review whether Assistant still owns RAG administration handlers/state. If not, remove or relocate them to `/RAG` to reduce Assistant UI surface area.

### RQF-053B-FU8 ? Rollback notes for external frontend assets

Deployment artifacts for external `goal-fest-page` assets should include the previous asset hash and/or an explicit rollback command.

### RQF-053B-FU9 ? Boundary tests for trace/source builders

Add tests for `build_specialist_workspace_step_trace` and `build_specialist_workspace_source_panel` with `live_lookup_performed=False` and malformed/empty list-like values.

## Priority

Recommended handling:

1. FU1, FU2, FU3 ? security / replay / incorrect action triggers.
2. FU4 ? product continuity and workspace history.
3. FU5, FU6 ? correctness and operator trust.
4. FU8, FU9 ? factory hygiene.
5. FU7 ? UI cleanup, likely during the larger Assistant UI revamp.

## Boundary

These follow-ups should not block the completed RQF-053B read-only trace/source contract, but they should be addressed before presenting the Assistant UI as polished or before enabling any write-capable assistant path.
