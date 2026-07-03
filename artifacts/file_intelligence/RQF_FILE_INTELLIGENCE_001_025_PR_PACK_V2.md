# RQF File Intelligence 001-025 PR Pack V2

## Purpose

This V2 pack replaces the malformed V1 patch. It is rebuilt against a committed assistant-runtime base and does not reuse the hand-written V1 router hunk.

## Base

- Base commit: `6b4b7b49dc6b86c4212bf408b62d4cfa38d8c4ea`
- Base branch: `origin/rqf-samchat-assistant-runtime-clean`
- Reason: this base contains the committed live assistant runtime files needed for `/assistant` file-intelligence wiring.

## Pack Files

- `artifacts/file_intelligence/rqf_file_intelligence_001_025_patch_v2.diff`
- `artifacts/file_intelligence/rqf_file_intelligence_001_025_owned_files_v2.list`
- `artifacts/file_intelligence/rqf_file_intelligence_001_025_checksums_v2.sha256`
- `artifacts/file_intelligence/RQF_FILE_INTELLIGENCE_001_025_PR_PACK_V2.md`

## Included Scope

- Deterministic document classifier/intake/action planner.
- Confirmation gate and conversation command parser.
- Upload-service document-intake context emission.
- Live conversation-service deterministic short-circuiting.
- Minimal `router.py:create_message` wiring for read-only action-router executor injection.
- Focused unit tests for classifier, intake, planner, confirmation, upload integration, live wiring, and runtime smoke/provider isolation.
- File-intelligence audit and closeout artifacts.

## Excluded Scope

- Frozen Assistant UI source/dist.
- `goal-fest-page/src/pages/Assistant.tsx`
- `goal-fest-page/dist`
- `artifacts/ui`
- unrelated dirty worktree changes
- malformed V1 patch/checksum/list artifacts

## Router Handling

`router.py` is included by generating a real diff from the clean base after applying only the minimal `create_message` wiring:

- define `document_action_router_executor`
- reject non-read canonical actions
- call `execute_canonical_action(...)` only through `action_router`
- pass `document_action_router_executor` to `run_message_turn_with_pending(...)`

No broad router refactor is included.

## Safety

- Writes remain disabled unless feature flags and confirmation policy allow them.
- Write-like file-derived actions return blocked with `writes_disabled` when writes are disabled.
- No direct DB write path is added.
- No auth/session semantics are changed.
- No OCR commit path, webhook, provider, or external API behavior is changed.
- Runtime smoke tests include provider sentinels that fail if provider code is reached.

## Validation Intent

The V2 patch is designed to pass:

- `git apply --check` against `6b4b7b49dc6b86c4212bf408b62d4cfa38d8c4ea`
- focused py_compile for modified assistant modules
- focused document-intelligence unit tests
- action-router contract tests
- `git diff --check`

## Recommendation

After clean apply and validation, the next product tranche should enable one controlled read-only preview path for balanza or CFDI. Do not enable write execution yet.
