# RQF-SAMCHAT-ASSISTANT-009B ? Curated RAG Ingestion

Status: CLOSED_LOCAL
Date: 2026-07-30

## Objective

Make the assistant product canon and customer sprint rules retrievable through the local assistant RAG layer without indexing the whole repository or generated/private files.

## Implementation

- Added `scripts/assistant_ingest_curated_rag.py`.
- Added a curated source list covering assistant canon, operations docs, security reference, sprint evidence, and assistant canary artifacts.
- Added denylist checks for secrets, private/upload/download folders, binary spreadsheets, images, PDFs, archives, sqlite/db files, virtualenvs, and git internals.
- Updated RAG search ranking to combine embedding similarity with lexical score so exact business/policy phrases are not buried by semantically-near but wrong documents.
- Ignored generated `data/assistant_rag_index.json` from Git; the index is reproducible and environment-local.

## Local ingestion evidence

Command:

```bash
PYTHONPATH=src:. ASSISTANT_RAG_BASE_DIR=/root/samchat   /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python   scripts/assistant_ingest_curated_rag.py --reset --max-files 200
```

Result:

```text
indexed_files=26
indexed_chunks=156
total_chunks=156
embedding_error=null
index_path=/root/samchat/data/assistant_rag_index.json
```

## Retrieval evidence

Query: `not a dashboard with chat primary work interface explicit authority`

Top result:

```text
/root/samchat/docs/assistant/product-canon.md
```

Query: `regla no deducibles factura proyecto`

Top result:

```text
/root/samchat/docs/sprints/rqf-057e-no-deducibles-rule.md
```

Query: `estrategia autorizacion perfiles monto`

Top result:

```text
/root/samchat/docs/sprints/rqf-057g-authorization-strategy-matrix.md
```

## Tests

```text
PYTHONPATH=src:. /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_product_canon_contract.py   tests/unit/test_assistant_curated_rag_ingest.py   tests/unit/test_assistant_rag_search_quality.py -q

9 passed, 4 warnings
```

## Boundary

- No write enablement.
- No allowlist expansion.
- No model fine-tuning.
- No generated RAG index committed.
- Production still needs deployment plus environment-local index generation before live canary evidence can include `doc_results`.
