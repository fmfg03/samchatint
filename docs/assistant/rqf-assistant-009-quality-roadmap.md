# RQF-SAMCHAT-ASSISTANT-009 ? Assistant Quality and Context Roadmap

Status: OPEN
Canary posture: READ_ONLY
Writes: DISABLED

## Objective

Improve assistant quality by making SamChat walk its own product canon: inspect context, use governed tools, preserve case memory, generate evidence-backed artifacts, and require explicit authority for effects.

## Stage 009A ? Canon and context contract

Close when:

- product canon is versioned under `docs/assistant/product-canon.md`;
- context corpus rules are versioned under `docs/assistant/context-corpus.md`;
- tests protect the existence and key claims of those documents;
- no runtime behavior or authority boundary changes are introduced.

## Stage 009B ? Curated RAG ingestion

Close when:

- curated assistant/business docs are indexed;
- `/api/assistant/rag/status` reports non-zero chunks and sources;
- traces for product/rule questions include `doc_results`;
- generated/private/binary files are excluded.

## Stage 009C ? Case memory summaries

Close when:

- each meaningful assistant case has a compact summary artifact;
- summaries include objective, scope, documents, findings, decisions, open questions, and approvals;
- retrieval prefers summaries over raw noisy chat when available;
- summaries remain subordinate to live SQL and policy.

## Stage 009D ? Tool-routing quality

Close when:

- simple live-data questions hit deterministic SQL/tools before remote LLM reasoning;
- product/policy questions retrieve canon before answering;
- complex workflows receive planning and tool selection;
- the runtime avoids sending every request to the largest provider/toolset.

## Stage 009E ? Evaluation set

Close when:

- owner AI needs are versioned under `docs/assistant/owner-ai-needs.md`;
- at least 25 realistic prompts cover entity folders, national phase folders, expenses, CFDI, payments, authorizations, tournaments, operations, marketing evidence, and release QA;
- each prompt has expected context sources and forbidden behaviors;
- canary quality is reported with pass/fail evidence;
- provider timeouts are tracked as product-quality failures even when safely controlled.

## Stage 009F ? Business diff preview pattern

Close when:

- proposed business changes are displayed as before/after or create/update/delete previews;
- approvals reference the exact preview version;
- execution receipts bind to the approved preview;
- the pattern is reusable across expenses, payment requests, budgets, tournaments, and operations.


## Follow-up backlog: RQF-053B Assistant UI debt

Non-blocking review follow-ups from PR #151 are tracked in `docs/assistant/rqf-053b-ui-followups.md`. They cover provider-key intake hardening, request replay safety, export intent precision, conversation-history rendering, mode validation, dashboard error states, RAG ownership cleanup, deployment rollback notes, and boundary tests.

These items should be addressed before presenting the Assistant UI as polished or before enabling write-capable assistant paths.

## Non-goals for RQF-009

- No general write enablement.
- No allowlist expansion until quality improves.
- No model fine-tuning.
- No indexing of secrets or raw private uploads.
