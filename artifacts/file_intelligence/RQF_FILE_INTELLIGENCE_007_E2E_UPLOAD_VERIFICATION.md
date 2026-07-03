# RQF File Intelligence 007 E2E Upload Verification

## Scope

- Verified seam: `src/samchat/assistant/upload_service.py`
- Focused test added: `tests/unit/test_assistant_upload_document_intake_integration.py`
- Product code edited in this verification step: no
- Assistant UI edited: no
- `goal-fest-page/src/pages/Assistant.tsx` edited: no
- `goal-fest-page/dist` edited or regenerated: no
- `src/samchat/assistant/router.py` edited: no

## Exact Fixtures Used

1. Accounting balance CSV:
   - File: `balanza.csv`
   - Headers: `Cuenta`, `Descripcion de la cuenta`, `Total de cargos`, `Total de abonos`, `Saldo final`
   - Rows: two synthetic accounts with matching debit/credit totals.

2. Roster CSV:
   - File: `roster.csv`
   - Headers: `Equipo`, `Categoria`, `Nombre`, `Apellido`, `CURP`
   - Rows: two synthetic players on team `Tigres`, category `Sub-17`, one malformed CURP.

3. CFDI XML:
   - File: `factura.xml`
   - Synthetic CFDI 4.0 style XML with UUID `123E4567-E89B-12D3-A456-426614174000`, issuer RFC `AAA010101AAA`, amount `45000.00`, date `2026-05-12T10:00:00`.

4. Payment proof text:
   - File: `spei.txt`
   - Synthetic SPEI text with amount `$45,000.00`, date `2026-05-13`, tracking key `SPEI123ABC`, beneficiary, and concept.

5. Generic text:
   - File: `generic.txt`
   - Text: `Notas generales sin workflow deterministico claro.`

## Observed DOCUMENT_INTAKE_RESULT Examples

Accounting balance:

```json
{
  "detected_document_type": "accounting_balance",
  "entities": {
    "account_count": 2,
    "imbalance": "0.00"
  },
  "missing_fields": ["company", "project", "period"],
  "proposed_actions": [
    {
      "canonical_action": "executive.accounting_report",
      "requires_confirmation": false,
      "risk_level": "read"
    }
  ]
}
```

Roster:

```json
{
  "detected_document_type": "roster",
  "entities": {
    "team_name": "Tigres",
    "category": "Sub-17",
    "player_count": 2
  },
  "missing_fields": ["tournament"],
  "proposed_actions": [
    {"canonical_action": "operations.tournament_soul_snapshot", "requires_confirmation": false},
    {"canonical_action": "operations.verify_player_document", "requires_confirmation": true, "write_blocked": true}
  ]
}
```

CFDI:

```json
{
  "detected_document_type": "cfdi_invoice",
  "entities": {
    "uuid": "123E4567-E89B-12D3-A456-426614174000",
    "issuer_rfc": "AAA010101AAA",
    "amount": "45000.00"
  },
  "missing_fields": ["expense_or_document_candidate"]
}
```

Payment proof:

```json
{
  "detected_document_type": "payment_proof",
  "entities": {
    "amount": "45,000.00",
    "bank_reference": "SPEI123ABC"
  },
  "missing_fields": ["document_or_expense_candidate"],
  "proposed_actions": [
    {"canonical_action": "receipts.pending_payment_overview", "requires_confirmation": false},
    {"canonical_action": "receipts.register_document_payment", "requires_confirmation": true, "write_blocked": true}
  ]
}
```

Unknown:

```json
{
  "detected_document_type": "unknown_or_generic",
  "missing_fields": ["target_workflow"],
  "proposed_actions": [],
  "questions_for_user": ["Indica a que workflow pertenece este documento."],
  "safety": {"blocked_reason": "unsupported_document_type"}
}
```

## Confirmation Gating Result

- The upload context begins with `DOCUMENT_INTAKE_RESULT JSON:` for all five synthetic upload cases.
- The JSON is parsed from the same string returned by `extract_text_from_media`, which is the existing message context passed to the Assistant runtime.
- Write-like proposals have `requires_confirmation=true`.
- With writes disabled, write-like proposals have `write_blocked=true`.
- Read preview proposals remain `requires_confirmation=false`.
- No proposal claims payment, CFDI linking, registration review, or accounting posting was executed.

## Unsupported/Write Action Fail-Closed Result

- The integration test verifies all proposed canonical action names exist in `action_router.supported_actions()`.
- Unknown/generic documents produce no executable write proposal.
- Existing planner tests verify unsupported action names are omitted when the supported action surface does not include them.

## Side-Effect Evidence

- Provider/OCR stubs in the upload integration test raise if called; they were not called.
- Tests do not create DB sessions.
- Tests do not call `execute_canonical_action`.
- Tests do not trigger webhooks, live APIs, OCR execution, provider calls, or write handlers.

## Validation Commands And Results

- `PYTHONPYCACHEPREFIX=/tmp/rqf-file-intelligence-007-pycache python3 -m py_compile src/samchat/assistant/upload_service.py`: passed.
- `./scripts/pytestw tests/unit/test_assistant_upload_document_intake_integration.py`: 5 passed.
- `./scripts/pytestw tests/unit/test_assistant_document_intake.py`: 6 passed.
- `./scripts/pytestw tests/unit/test_assistant_document_action_planner.py`: 6 passed.
- `./scripts/pytestw tests/unit/test_assistant_action_router_contracts.py`: 13 passed.
- `git diff --check` on owned files: passed.

## Files Changed

- `tests/unit/test_assistant_upload_document_intake_integration.py`
- `artifacts/file_intelligence/RQF_FILE_INTELLIGENCE_007_E2E_UPLOAD_VERIFICATION.md`

## Pre-existing Notes

- `tests/unit/test_assistant_spreadsheet_upload.py` was not run or fixed in this task. Prior verification showed it fails because the current dirty `samchat.assistant.router` module does not expose `pd`, while that legacy test monkeypatches `assistant_router.pd`.
- Current scoped status still shows pre-existing dirty/untracked files such as `src/samchat/assistant/router.py`, `src/samchat/assistant/db.py`, and frozen UI artifacts; this task did not edit them.

## Final Statement

The upload flow surfaces document-intake results to the Assistant context without executing writes.
