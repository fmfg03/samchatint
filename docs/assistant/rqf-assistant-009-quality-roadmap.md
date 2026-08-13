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

## Experimental extension: RQF-SAMCHAT-BUZZ-001 - isolated multiagent expense-checking spike

Status: ROADMAP_ADDED
GitHub issue: #148
Posture: EXPERIMENTAL_SPIKE
Production writes: DISABLED
Real fiscal documents: OUT_OF_SCOPE

This line enters the general assistant roadmap as a controlled laboratory track, not as a production feature.
It evaluates whether expense-report checking improves when SamChat uses a temporary team of isolated specialist agents, and whether projecting that collaboration into Buzz provides enough operational value to justify a replaceable collaboration dependency.

### Placement in the roadmap

BUZZ-001 should begin only after the assistant workspace surface is stable enough to show context, evidence, diagnostics, proposed work, and authority boundaries to operators. It is adjacent to the Claude Code-style assistant roadmap, but it must not block ordinary read-only assistant improvements.

Recommended sequence:

1. Close the current workspace-card UI surface.
2. Freeze BUZZ-001 Phase 0 semantics and success contract.
3. Implement a walking skeleton using synthetic or pseudonymized `expense_report` cases.
4. Run CaseBench A/B with the current specialist flow versus isolated multiagent collaboration in-memory.
5. Add Buzz only as CaseBench C after the provider-neutral contract passes with in-memory collaboration.
6. Decide explicitly: `ADOPT_GEOMETRY_AND_BUZZ`, `ADOPT_GEOMETRY_ONLY`, or `REJECT_SPIKE`.

### Phase 0 decisions to carry into spec

- LLM agents receive derived evidence only; raw documents remain in a deterministic Raw-Evidence Enclave for OCR, parsing, checksum, validation, redaction, and extraction.
- Proposal verification is deterministic, not a fourth probabilistic agent.
- Hard containment gates dominate quality: any world effect, credential exposure, cross-case read, raw document persistence in Buzz, unsupported accepted claim, or evidence-binding failure fails the spike.
- B must beat A through measurable quality and operator-load improvements while respecting cost, latency, and reproducibility guardrails.
- C must justify Buzz through lineage, handoff, reconstruction, retryability, and operational coordination benefits; UI attractiveness alone is insufficient.
- Buzz room interactions are collaborative, not authoritative. Reactions, comments, mentions, and room activity never approve, execute, or mutate business records.
- Sandbox target for the spike is rootless pod isolation with gVisor/runsc, read-only root filesystem, tmpfs workspace, default-deny egress, and no direct routes to production mutation surfaces.
- Buzz retention is experimental and bounded: no raw documents, isolated deployment, maximum 30-day retention, and final deletion proven by destroying the experimental deployment and issuing a wipe receipt.

### Acceptance boundary

BUZZ-001 may produce architecture, synthetic-case benchmarks, receipts, and a recommendation. It may not enable production actions, create authority paths, publish raw documents to Buzz, or use real fiscal material in the spike.

## Non-goals for RQF-009

- No general write enablement.
- No allowlist expansion until quality improves.
- No model fine-tuning.
- No indexing of secrets or raw private uploads.
