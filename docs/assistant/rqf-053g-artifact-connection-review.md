# RQF-053G - Artifact connection review

Status: IMPLEMENTED_LOCAL

Objective: retake the institutional artifact inventory with an explicit connection verdict per assistant-facing artifact.

This slice is a decision layer, not a wiring slice. It does not expose new tools, execute runtime artifacts, create folders, notify users, mutate business records, or grant write authority.

## Verdict labels

| Code | Label | Meaning |
| --- | --- | --- |
| `connect_now` | conectar ahora | Safe to use/expose under the artifact current read-only or preview-only authority boundary. |
| `keep_internal` | mantener interno | Useful for QA, audit, regression, or internal reasoning; not a user-facing runtime surface. |
| `merge_with_another` | fusionar con otro | Keep the implementation as source or support, but consolidate its user-facing behavior into another artifact. |
| `obsolete` | obsoleto | No longer useful as active source or surface. |
| `needs_data_first` | necesita datos antes | The artifact concept is valid, but data coverage/source quality must be proven before runtime claims. |

## Artifact verdicts

| Artifact | Verdict | Target / prerequisite | Rationale |
| --- | --- | --- | --- |
| `finance.platform_snapshot` | conectar ahora | - | Already wired as executive finance snapshot for cash, payments, COI, DIOT and CFDI. |
| `finance.closeout_diagnostics` | conectar ahora | - | Already answers closeout blockers from controlled accounting/tax sources. |
| `sports.platform_snapshot` | fusionar con otro | `assistant.sports_operations_status` | Raw operations projection is too broad for direct assistant exposure. |
| `assistant.sports_operations_status` | conectar ahora | - | Narrow read-only wrapper for operational status, priorities and risk. |
| `assistant.sports_platform_audit` | fusionar con otro | `assistant.sports_operations_status` | Its audit function should stay in regression/source review, while runtime goes through the status wrapper. |
| `sports.director_general_entity_dossier` | fusionar con otro | `assistant.owner_entity_folder_workspace` | Raw DG dossier should feed the owner workspace, not be exposed directly. |
| `assistant.owner_entity_dossier_audit` | mantener interno | - | Quality-control layer for dossier coverage and non-claims. |
| `assistant.owner_entity_dossier_live` | conectar ahora | - | Live read-only entity dossier wrapper for evidence and missing fields. |
| `tournament.soul_snapshot` | necesita datos antes | SOUL per tournament; phases, dates, activities; entities/teams coverage | Canonical concept, but current coverage is not yet complete enough to claim all tournament folders. |
| `assistant.owner_pack_readiness` | conectar ahora | - | Minimal navigable Owner Pack readiness surface. |
| `assistant.soul_wizard_contract` | conectar ahora | - | Read-only/preview-only wizard contract for tournament drafts. |
| `assistant.soul_wizard_owner_pack_bridge` | conectar ahora | - | Bridges wizard phases/dates/activities into Owner Pack context without writes. |
| `assistant.owner_variable_query` | conectar ahora | - | Resolves owner variables with supported/missing/conflicting evidence states. |
| `assistant.owner_variable_answer` | conectar ahora | - | Renders variable reports into executive Spanish without adding facts. |
| `assistant.owner_entity_folder_workspace` | conectar ahora | - | Legible owner-facing workspace: operations, finance, faltantes, evidence, non-claims and next questions. |
| `assistant.owner_operator_workflow` | fusionar con otro | `assistant.owner_pack_readiness` | Useful preview, but overlaps with readiness, variable answer and entity folder workspace. |
| `accounting.historical_snapshot` | necesita datos antes | COI backups; quality flags; company/year coverage | Historical memory is valuable, but raw source coverage and quality must be validated first. |
| `assistant.historical_accounting_precedent` | conectar ahora | - | Safe read-only precedent query; informs but does not assign authority. |
| `budget.snapshot` | conectar ahora | - | Canonical budget source for actuals, alerts, forecast and comparisons. |
| `sam_inbox.payload` | necesita datos antes | deduplication; actor/role visibility; live source checks | Unified inbox is promising but must not mix permissions or duplicate queues. |
| `expense.accounting_preview` | conectar ahora | - | Preview-only accounting view for expenses before posting. |

## Summary

- conectar ahora: 13
- mantener interno: 1
- fusionar con otro: 4
- obsoleto: 0
- necesita datos antes: 3

## Non-claims

- No new runtime tools were connected by this slice.
- `connect_now` does not authorize writes. It only means the artifact is suitable for assistant use under its existing authority level.
- `needs_data_first` means SamChat must report missing evidence instead of inferring facts.
- This review covers the assistant-facing institutional artifact registry, not every export endpoint or low-level module in the product.

## Evidence

- `src/samchat/assistant/institutional_artifact_registry.py` defines `ARTIFACT_CONNECTION_DECISIONS`.
- `build_institutional_artifact_connection_review()` emits the read-only grouped report.
- Unit tests enforce that every artifact has a verdict and that merge/data-prerequisite constraints are explicit.
