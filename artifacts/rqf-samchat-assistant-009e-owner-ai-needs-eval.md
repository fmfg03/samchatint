# RQF-SAMCHAT-ASSISTANT-009E - Owner AI Needs Evaluation Baseline

Status: CLOSED_LOCAL_PENDING_COMMIT
Scope: assistant product quality, owner-needs canon, curated retrieval, and evaluation prompts.

## Objective

Convert the Plataforma Sports owner's "Cosas que necesito que me de la IA" into a versioned SamChat assistant requirement and evaluation baseline.

The owner requirement is now treated as a product contract: SamChat must be able to assemble evidence-backed tournament folders per participating entity and national-phase folders spanning operations, finance, and marketing.

## Material changes

- Added `docs/assistant/owner-ai-needs.md` as canonical input.
- Added Spanish retrieval anchors for the exact owner vocabulary: carpeta por entidad, operaciones, finanzas, fase nacional, hoteles, camas-noche, alimentos, mercadotecnia, fotografias, etc.
- Added `docs/assistant/rqf-assistant-009e-evaluation-set.md` with 30 owner-needs prompts.
- Updated the assistant roadmap so Stage 009E requires at least 25 owner-needs prompts.
- Updated the curated RAG corpus to index source/canon files explicitly instead of indexing all of `docs/assistant`.
- Prevented curated RAG ingestion from accepting directories and evaluation-set files.
- Improved RAG lexical scoring so exact user/business terms are not diluted by long documents or buried under semantically-near but wrong chunks.

## Retrieval evidence

Curated ingestion was rebuilt from `/root/samchat`:

- indexed files: 27
- indexed chunks: 162
- embedding error: null
- index path: `/root/samchat/data/assistant_rag_index.json`

Key queries after reindexing:

| Query | Top source | Score | Lexical | Vector |
| --- | --- | ---: | ---: | ---: |
| `carpeta por entidad torneo operaciones finanzas` | `docs/assistant/owner-ai-needs.md` | 1.2250 | 0.3500 | 0.5304 |
| `fase nacional hoteles camas noche alimentos marketing fotografias` | `docs/assistant/owner-ai-needs.md` | 1.0398 | 0.2625 | 0.5900 |

The evaluation set is intentionally not part of the curated RAG source list. It measures the assistant; it must not become answer context.

## Tests

Command:

```bash
PYTHONPATH=src:. /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_case_memory.py   tests/unit/test_assistant_inference_router.py   tests/unit/test_assistant_product_canon_contract.py   tests/unit/test_assistant_curated_rag_ingest.py   tests/unit/test_assistant_rag_search_quality.py   -q
```

Result:

- 54 passed
- 11 warnings

## Claim boundary

Established:

SamChat now has a versioned owner-needs canon and evaluation baseline for tournament/entity/national-phase folders, and curated retrieval prioritizes that canon for representative Spanish owner prompts.

Not yet established:

- The assistant can complete every folder end-to-end from live production data.
- Durable folder creation/update has a unified preview/diff and approval UX.
- All necessary operations, finance, media, marketing, and tournament tools are fully wired for these folder artifacts.
- The 30-prompt evaluation set has been executed through the live canary endpoint.

## Next expected slice

Run a canary evaluation against at least 10 prompts from `docs/assistant/rqf-assistant-009e-evaluation-set.md`, capturing provider/model, latency, retrieval sources, tool count, whether missing fields are named, and pass/fail against forbidden behaviors.
