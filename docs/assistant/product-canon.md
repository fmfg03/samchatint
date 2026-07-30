# SamChat Assistant Product Canon

Status: CANONICAL_DRAFT
Owner: Fabrica / Plataforma Sports
Scope: SamChat assistant product definition and quality target

## Canonical definition

SamChat is an operational assistant built with the Claude Code paradigm: it understands a request, inspects available context, uses business tools, generates artifacts, proposes actions, and executes only with explicit authority.

SamChat is not a dashboard with chat. The dashboard is an auxiliary surface. The assistant is the primary work interface.

## Working model

The Claude Code analogy does not mean copying a terminal. It means reproducing the work model in Plataforma Sports operations.

| Claude Code | SamChat |
| --- | --- |
| Reads a repository | Reads case files, CFDI, budgets, teams, players, and documents |
| Searches files and code | Searches operations, movements, records, and evidence |
| Uses tools | Queries and executes canonical SamChat actions |
| Builds a plan | Decomposes a business request into steps |
| Modifies files | Prepares requests, checks, records, reports, and draft documents |
| Runs tests | Validates rules, totals, documents, permissions, and consistency |
| Shows the diff | Explains proposed changes and why they are safe |
| Requests authorization | Requires approval before real-world effects |
| Preserves context | Resumes the case with documents, decisions, versions, and evidence |
| Produces commits | Produces traceable, versioned, auditable business outcomes |

## Product promise

SamChat should let a user state an operational objective in natural language, provide documents if needed, and receive a prepared business result with evidence and approval gates.

Example target interaction:

> Here are five receipts. Prepare my expense account, identify which ones have CFDI, tell me what is missing, and leave the payment request ready.

Expected assistant behavior:

1. Create or resume a persistent case.
2. Read and classify each file.
3. Extract amounts, taxes, issuer, receiver, date, concepts, and references.
4. Link XML, PDF, image, receipt, and material evidence when they describe the same expense.
5. Detect duplicates, missing files, mismatches, and suspicious inconsistencies.
6. Consult policies, project, budget line, authorization strategy, and fiscal rules.
7. Build a draft expense account or payment request.
8. Show findings, assumptions, missing context, and proposed corrections.
9. Present a preview equivalent to a business diff.
10. Execute only after explicit authorization.
11. Persist evidence of what was read, proposed, approved, and executed.

## Product hierarchy

SamChat should be closed and tested in this order:

1. Operational assistant: conversation, files, tools, cases, plans, and artifacts.
2. Authority control: preview, approval, roles, limits, idempotency, and audit trail.
3. Core workflows: expenses, CFDI checks, payment requests, advances, and reimbursements.
4. Reliability: persistence, recovery, traceability, regression tests, and failure handling.
5. Dashboard: supervision, administration, exception handling, and operational visibility.
6. Additional domains: registrations, sponsorship, budgets, tournaments, and analytics.

## Non-negotiable principles

- The assistant may propose; authority remains with people and configured business rules.
- The assistant must separate read-only investigation from write-capable execution.
- Every write-capable action requires explicit preview and approval.
- Business data must be read from live governed sources when freshness matters.
- Stable rules and product canon must be versioned and retrievable.
- Memory must not silently override live data, policy, or authorization rules.
- Evidence should be attached to conclusions whenever the assistant makes an operational claim.
- A module is not complete because its screen works; it is complete when the assistant can use it inside a reliable end-to-end business outcome.

## Current closure boundary

The current assistant canary demonstrates a read-only runtime boundary and basic context retrieval. It does not yet prove a full Claude Code-style business cycle.

The next quality work must therefore focus on:

- canonical context ingestion;
- persistent case memory;
- visible planning;
