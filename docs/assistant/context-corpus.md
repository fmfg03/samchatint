# Assistant Context Corpus

Status: CANONICAL_DRAFT
Purpose: define the minimum versioned knowledge SamChat must retrieve before expanding the canary.

This corpus is the first education layer for the assistant. It is not model training. It is source-backed operating context made available through RAG, live SQL tools, and case memory.

## Context layers

### 1. Stable canon

Use versioned documents for rules that change slowly.

Initial sources:

- `docs/assistant/product-canon.md`
- `docs/assistant/owner-ai-needs.md`
- authorization strategy and approval matrix documentation;
- expense, advance, reimbursement, and payment-request rules;
- non-deductible expense policy;
- SAT/CFDI ingestion and reconciliation rules;
- QA and release acceptance protocol;
- factory methodology and sprint close evidence.

### 2. Live business context

Use governed SQL/tools for facts that change frequently.

Initial domains:

- employees, operators, providers, clients, and bank accounts;
- payment requests, advances, expense accounts, reimbursements, and document status;
- CFDI XML/PDF/materiality records;
- budgets, projects, phases, categories, versions, and no-deductible mappings;
- tournaments, teams, players, rosters, documents, calendars, incidents, and eligibility;
- Telegram notifications and approval outbox status.

### 3. Case memory

Use summarized, scoped memory for work-in-progress.

The assistant should preserve:

- user objective;
- selected project, tournament, phase, beneficiary, or provider;
- documents already processed;
- open findings and missing evidence;
- decisions made by the user;
- previews shown;
- approvals granted;
- execution receipts;
- limitations and follow-up tasks.

Case memory may help continuity, but it must not override live business data or authorization policy.

## Retrieval quality target

For any non-trivial answer, the assistant should be able to show which context layer supported it:

- `canon`: stable product or business rule;
- `sql`: live operational or financial data;
- `memory`: prior conversation or case state;
- `tool`: result of a canonical action;
- `none`: insufficient evidence, requiring clarification or abstention.

## Initial RAG ingestion set

The first assistant RAG index should include only curated files, not the entire repository.

Recommended first paths:

```text
docs/assistant
docs/operations
docs/business
docs/security
docs/sprints
artifacts/rqf-samchat-assistant-007d-health-stable-readonly-runtime-soak.md
artifacts/rqf-samchat-assistant-008-readonly-canary.md
```

Avoid indexing generated files, secrets, large binary files, private uploads, or raw customer documents unless they are deliberately converted into a sanitized knowledge artifact.

## Quality gates before canary expansion

- RAG index exists and contains curated assistant/business documents.
- Assistant responses cite or trace at least one relevant context source when answering policy/product questions.
- Live facts are fetched from SQL/tools, not stale memory.
- Prior decisions can be recovered from case memory.
- The assistant abstains when neither canon nor live data supports an answer.
- Retrieval does not leak data across users or unrelated scopes.
