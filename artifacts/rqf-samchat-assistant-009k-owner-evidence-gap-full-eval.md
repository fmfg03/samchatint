# RQF-SAMCHAT-ASSISTANT-009K - Owner Evidence-Gap Full Eval Closure

Status: PASS_WITH_CLASSIFIED_GAPS
Date: 2026-08-04
Scope: owner-needs read-only quality contract

## Objective

Close the full 30-prompt owner-needs eval set with deterministic gap
classification. This stage does not claim a new live HTTP canary run and does
not enable writes. It converts the owner-needs eval set into a reproducible
contract for separating canon, found evidence, missing evidence, confidence
limits, and next actions.

## Inputs

- `docs/assistant/rqf-assistant-009e-evaluation-set.md`
- `docs/assistant/owner-ai-needs.md`
- `src/samchat/assistant/owner_needs_eval.py`
- `tests/unit/test_assistant_owner_needs_eval.py`
- `.github/workflows/assistant-scoped-gate.yml`
- Prior live canary evidence: `artifacts/rqf-samchat-assistant-009j-owner-canary-live.md`

## Result Summary

| Metric | Value |
| --- | ---: |
| Prompts parsed | 30 |
| PASS | 3 |
| PASS_WITH_CLASSIFIED_GAPS | 27 |
| EVIDENCE_DATA_MISSING gaps | 27 |
| EXPECTED_LIMITATION gaps | 4 |
| Writes attempted | 0 |
| Side effects detected | 0 |

Final decision: PASS_WITH_CLASSIFIED_GAPS.

## Important Claim Boundary

This is a deterministic closure over the versioned 30-prompt eval set. It is
not a new authenticated production canary run. It establishes the response and
gap-classification contract that the live assistant must follow before the next
business-diff/preview stage.

## Gap Rules

Allowed gap categories:

- CODE_FIX_REQUIRED
- CANON_UPDATE_REQUIRED
- EVIDENCE_DATA_MISSING
- CONFIG_OR_CANARY_GAP
- PRODUCT_DECISION_REQUIRED
- EXPECTED_LIMITATION
- TEST_HARNESS_GAP

This closure accepts missing live evidence as PASS_WITH_CLASSIFIED_GAPS only
when the assistant explicitly says evidence is missing and avoids filling the
gap with generic facts.

## AI-OWNER-018 Closure

Prompt:

`Dame descripcion de servicios medicos en sede y accidentes con traslado.`

Required behavior:

- say there is no concrete evidence loaded for medical services and accidents
  with transfer when no source is retrieved;
- treat the owner-needs canon as required folder content, not as proof that
  services or accidents occurred;
- inspect document and medical/event_incident evidence before describing facts;
- do not invent accidents, transfers, providers, costs, insurance, or sensitive
  details.

Classified gap:

| Field | Value |
| --- | --- |
| prompt_id | AI-OWNER-018 |
| gap_type | EVIDENCE_DATA_MISSING |
| requires | document, medical/event_incident |
| decision | Answer with explicit missing-evidence language and do not fill the gap with generic facts. |

## Prompt Classification

| ID | Result | Classified gaps |
| --- | --- | --- |
| AI-OWNER-001 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING, EXPECTED_LIMITATION |
| AI-OWNER-002 | PASS | - |
| AI-OWNER-003 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-004 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-005 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-006 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-007 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-008 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-009 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-010 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-011 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-012 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-013 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING, EXPECTED_LIMITATION |
| AI-OWNER-014 | PASS | - |
| AI-OWNER-015 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-016 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-017 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-018 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-019 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-020 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-021 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-022 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-023 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-024 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-025 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING, EXPECTED_LIMITATION |
| AI-OWNER-026 | PASS | - |
| AI-OWNER-027 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-028 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING, EXPECTED_LIMITATION |
| AI-OWNER-029 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |
| AI-OWNER-030 | PASS_WITH_CLASSIFIED_GAPS | EVIDENCE_DATA_MISSING |

## What The Remaining Gaps Mean

EVIDENCE_DATA_MISSING means the prompt asks for live operational data such as
tournaments, teams, players, documents, payments, finance, providers, media,
marketing, medical/event incidents, SQL, or memory. The owner-needs canon says
these fields are required; it does not prove the facts exist.

EXPECTED_LIMITATION means the prompt asks for a durable create/update/report
action. Until the business diff/preview stage exists, the assistant must return
a plan or preview requirement and must not claim execution.

## Validation

Focused pytest:

```bash
PYTHONPATH=src:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/samchat-009k-pycache /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest -p no:cacheprovider -o addopts='' tests/unit/test_assistant_owner_needs_eval.py -q
```

Result:

- 5 passed

Related owner/canon/routing tests:

```bash
PYTHONPATH=src:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/samchat-009k-pycache /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest -p no:cacheprovider -o addopts='' tests/unit/test_assistant_owner_needs_eval.py tests/unit/test_assistant_product_canon_contract.py tests/unit/test_assistant_request_intent.py tests/unit/test_assistant_inference_router.py -q
```

Result:

- 64 passed
- 3 warnings

Hygiene:

- compileall on touched Python files: PASS
- flake8 on touched Python files: PASS
- git diff --check: PASS
- assistant scoped gate workflow updated to include `tests/unit/test_assistant_owner_needs_eval.py`

`./scripts/pytestw` was attempted first and could not run because this checkout
does not contain `/root/samchat/.venv/bin/python`.

## Safety Posture

- Writes enabled: no.
- General runtime enabled: no.
- Production/live DB touched: no.
- External provider calls: no.
- OCR/workflows/webhooks triggered: no.
- Service restarted/stopped: no.

## Next Stage

RQF-ASSISTANT-009F - Business Diff Preview Pattern.

That stage should convert the classified gaps into an operator-facing preview:
what was found, what is missing, what would be created or updated, which sources
support it, and what approval is required.
