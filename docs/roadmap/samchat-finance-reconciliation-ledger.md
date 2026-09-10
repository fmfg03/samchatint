# SamChat finance reconciliation ledger

Status: template for read-only inventory. No data repair is authorized by this
document.

## Classification

| Classification | Meaning | Required next step |
| --- | --- | --- |
| `AUTO_CANDIDATE` | deterministic repair appears possible | produce dry-run and seek apply authority |
| `FINANCE_DECISION` | business treatment changes a financial meaning | obtain Finance decision with evidence |
| `SOURCE_MISSING` | supporting source or proof is absent | retain exception; never infer a value |
| `OUT_OF_SCOPE` | not part of this closure | document handoff owner and boundary |
| `RESOLVED` | correction validated | attach receipt, reference and post-check |

## Inventory sequence

1. Documents, approvals, references and reimbursement budget inheritance.
2. Budget concepts, lines, movements, actuals and accounting mappings.
3. Issued/received CFDI, project and budget classification, and CxC collection.
4. Bank movements, Payment Run, payment proof and AMEX reconciliation.
5. Pólizas/prepólizas, COI exports and final operational boards.

## Required record per exception

| Field | Requirement |
| --- | --- |
| Stable reference | SamChat reference plus source/Operations reference when present |
| Domain and canonical owner | Name the workflow and responsible Finance role |
| Observed state | Source-backed, timestamped fact |
| Expected state | Rule, policy or accepted accounting treatment |
| Classification | One of the values above |
| Proposed action | Read-only query, dry-run, Finance decision, or no action |
| Mutation authority | Explicit approval identifier; blank until approved |
| Evidence | Query receipt, export, source document, payment/collection proof, or UAT case |
| Post-check | Exact count or invariant that proves the outcome |

## Control gates

- `G1 Inventory`: aggregate totals and exception references captured without writes.
- `G2 Decision`: Finance has classified non-deterministic cases.
- `G3 Dry-run`: deterministic plan reports targets, exclusions and reversibility.
- `G4 Apply`: separately authorized, idempotent where feasible, with backup.
- `G5 Verify`: no critical residuals, or each residual has a named owner and date.

## Explicit exclusions

- No inferred CFDI, project, budget concept, payment, collection or accounting
  value.
- No global bulk update from a UI label or report default.
- No claim that a candidate bank match proves payment or collection.
