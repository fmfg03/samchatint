# Tournament authority boundaries

SamChat contains similarly named objects that do not share one authority path.
Treating them as interchangeable is unsafe.

| Canonical class | Storage | Meaning | Allowed mutation path |
| --- | --- | --- | --- |
| `gastos_project` | local `tournaments` | Accounting and budget project used by gastos | Governed assistant proposal/application service for creation; explicitly authorized legacy administration only for pre-existing, non-governed rows |
| `cross_domain_link` | local `tournament_operations_links` | Optional link from a gastos project to operations | Authorized legacy administration only for non-governed rows |
| `operations_legacy_local` | local Copa Telmex team/player/OCR tables | Legacy operational capture | Typed operational services; never `db_write_universal` |
| `operations_tournament` | Supabase tournament and child tables | Operational competition domain | Typed operations endpoints/services; never `db_write_universal` |

## Governed target rule

Once an RQF tournament application receipt binds a local `gastos_project` row,
legacy edit, link, toggle, and delete handlers must reject that row. The immutable
AnalystCase version history and verified application receipt are the authority
source. Failure to inspect or verify that source fails closed.

## Creation rule

Legacy local-project creation endpoints are quarantined. New user- or
assistant-initiated `gastos_project` rows must enter through the governed proposal,
independent approval, application, receipt, and postcondition path.

The existing budget-catalog ingestion remains an explicit typed exception for
creating ungoverned catalog projects. It must call the same target-authority guard
before updating any matched row and therefore cannot alter an RQF-applied target.

Finance-training generation is a second explicit synthetic exception. Its rows
carry the deterministic `FINTRAIN ` prefix and training description; both cleanup
paths must call the target-authority guard before deletion, so a governed receipt
always wins over a stale or malicious training manifest.

## Universal writer rule

The universal database writer is not a tournament authority. Hard deny lists
cover every current table in the four classes above. Environment variables may
add restrictions but cannot remove the built-in restrictions.

## Explicit non-claim

Typed operational writers remain separate and may mutate operational tournament
children under their own domain contracts. This boundary does not claim that an
RQF local-project application creates or governs schedules, teams, players,
registrations, media, communications, or finance records.

The executable writer inventory is deliberately limited to the local
`gastos_project` and `cross_domain_link` tables. It does not claim repository-wide
closure of Supabase/operations writers; those belong to the separate operational
authority program.
