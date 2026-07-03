# RQF File Intelligence 001-006 Audit

## Existing Seams Inspected

- Live runtime: `copa_telmex_dashboard.py`
- Assistant media endpoint: `src/samchat/assistant/router.py`
- Upload extraction seam: `src/samchat/assistant/upload_service.py`
- Parsing helpers: `src/samchat/assistant/file_parsing.py`
- Upload context formatting: `src/samchat/assistant/upload_context.py`
- Canonical action seam: `src/samchat/assistant/action_router.py`

## Findings

- The assistant already accepts media uploads through `/conversations/{conversation_id}/media`.
- `upload_service.extract_text_from_media` already handles `image`, `voice`, `spreadsheet`, and `text`.
- Spreadsheets already distinguish roster-like sheets from balance/accounting-like tabular sheets.
- `action_router.py` already defines canonical read/write action names and separates supported read actions from supported write actions.
- The safest integration point is the upload extraction output: add deterministic intake metadata to the existing assistant message context without adding a new execution path.

## Implemented Architecture

- `document_classifier.py`: deterministic classifier using file name, parsed text, and spreadsheet headers/rows.
- `document_intake.py`: orchestration layer that extracts fields, validates missing data, selects candidate workflows, and returns a structured `DocumentIntakeResult`.
- `document_action_planner.py`: maps document classes to existing canonical action names.
- `document_confirmation.py`: stable proposed-action IDs, confirmation prompts, write/read action boundary, and fail-closed safety status.
- `upload_service.py`: prepends a compact `DOCUMENT_INTAKE_RESULT JSON` block to the existing upload context. It does not execute actions.

## Supported Initial Document Classes

- `accounting_balance`
- `roster`
- `player_registration`
- `document_validation`
- `tournament_ops`
- `cfdi_invoice`
- `invoice_document`
- `payment_proof`
- `unknown_or_generic`

## Safety Review

- No direct writes to core DB tables.
- No action adapters are invoked by the new intake layer.
- Write-like outcomes are represented as `proposed_actions` only.
- Proposed write actions require explicit confirmation.
- Unknown documents produce no executable write proposals.
- No provider calls are required by the deterministic classifier/planner tests.
- No OCR execution was added.
- No UI files were edited; frozen `goal-fest-page/src/pages/Assistant.tsx` and `goal-fest-page/dist` were left untouched.
