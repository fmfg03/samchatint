# RQF-SAMCHAT-ASSISTANT-009C ? Case Memory Summaries

Status: CLOSED_LOCAL
Date: 2026-07-30

## Objective

Add deterministic case memory summaries so the assistant can resume a work case with objective, scope, documents, findings, decisions, open questions, previews, approvals, execution receipts, and limitations.

## Implementation

- Added `src/samchat/assistant/case_memory.py`.
- Defined stable artifact type: `case_memory_summary`.
- Builds markdown and JSON metadata from existing `AssistantConversation`, `AssistantMessage`, and `AssistantRun` records.
- Persists summaries into existing `assistant_artifacts`; no new table or backend introduced.
- Integrated persisted case summaries into `_retrieve_memory_snippets` so summarized memory can be retrieved alongside prior raw chat.
- Case memory remains subordinate to live SQL/tools and policy; it is continuity context, not authority.

## Runtime boundary

- No write enablement.
- No allowlist expansion.
- No model fine-tuning.
- Existing canary remains read-only.
- Persist helper exists, but no automatic persistence was enabled in the live turn loop in this slice.

## Evidence

Dry build against a recent production conversation produced:

```text
conversation=cbac1c23-312b-405a-80ec-8fbda88064d5
messages=6
runs=3
objective=Read-only: ?qu? informaci?n debe mostrar un mensaje Telegram de aprobaci?n?
```

The generated markdown included objective, module scope, findings, decisions/open questions when detected, limitations, and source counts.

## Tests

```text
PYTHONPATH=src:. /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_case_memory.py   tests/unit/test_assistant_product_canon_contract.py   tests/unit/test_assistant_curated_rag_ingest.py   tests/unit/test_assistant_rag_search_quality.py -q

13 passed, 7 warnings
```

## Next

RQF-SAMCHAT-ASSISTANT-009D should improve tool routing quality and/or enable controlled case-memory persistence after successful turns, depending on the next canary target.
