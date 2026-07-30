# RQF-056 / RQF-057A Client Excel Sprint Ledger

Branch: `sprint-rqf-056-beneficiary-ops`
Base: `18a8e2e617c92056ec3f4f82cc8538cafd8c2a63` (`origin/main`, after RQF-056F)
Workflow: commit per phase; push/PR/merge only at sprint close.
Plan master: customer Excel + ordered stages G through O, then RQF-057A operations audit.

## Sprint guardrails

- Do not merge this branch into `main` until the sprint close gate.
- Preserve existing authorization ownership:
  - Solicitudes and informes belong to the requester/solicitante.
  - Approval routes to the requester approver, not the beneficiary approver.
  - Bank account selection must belong to the selected beneficiary.
- Third-party employee beneficiary capability is limited to explicit allowlist or explicit profile permission.
- Budget visibility/mutation must remain restricted:
  - mutate: superadmin only.
  - view: superadmin, directors, Alicia.
  - Bibiana, Carlos, Roberto and other operations users do not see budgets unless policy changes explicitly.
- Employee reimbursements must not render the employee as a provider.
- Keep generated/customer artifacts untracked unless deliberately promoted.

## Ordered sprint stages and real status

| Stage | Status | Evidence / commits | Remaining closure gap |
| --- | --- | --- | --- |
| RQF-056G - Selector de beneficiario en Informes de Gastos | CLOSED_COMMITTED | `2a664d00e`, plus prerequisite selector/access commits | Route-level unit coverage verifies authorized selector, unauthorized self-lock, submit ownership, beneficiary propagation, and requester-approver preservation. Live browser/UAT still belongs to sprint QA gate. |
| RQF-056H - Selector de beneficiario en Anticipos with full authorized list | CLOSED_COMMITTED | `117617173`, plus prerequisite selector/access commits | Unit coverage verifies Juan Pablo display-name authorization, full active employee selector rendering, self-lock for unauthorized users, beneficiary-scoped bank account API, and 403 for unpermitted beneficiary account lookup. Live browser/UAT remains in sprint QA gate. |
| RQF-056I - Cancelar / eliminar borradores incompletos | CLOSED_COMMITTED | `4145edfc4`, plus `34c239a80` | Empty informe draft cancellation now allows owner, finance, or superadmin only; route tests verify finance cleanup, non-owner 403, linked solicitudes block without commit, and utility matrix blocks non-draft/non-empty reports. Solicitud draft cancellation remains workflow-governed by requester ownership. |
| RQF-056J - Materialidades verificables antes de guardar | CLOSED_COMMITTED | `36e2853d2` | Existing materialidades picker is now covered: one-at-a-time add, multi-file hidden submission, visible empty/list state, filename/size/mime, image thumbnail, PDF preview link, remove button, and separation from CFDI XML/PDF controls. |
| RQF-056K - Correccion definitiva de totales CFDI XML/PDF | CLOSED_COMMITTED | `6e4112f48`, pending K close commit | Synthetic CFDI fixture reproduces the $128 case without customer data; tests verify XML Total authority, SubTotal + net taxes, inconsistency rejection for $124, and retenciones netting. Real customer XML was intentionally not committed. |
| RQF-056L - Presupuestos visibility/editing | PARTIAL / strong unit coverage | `a1185e079` | Need verify directors definition and UI hiding/POST 403/frozen versions across full routes. |
| RQF-056M - Telegram proyecto y etapa | NOT CLOSED | Existing implementation observed; no close commit | Need tests proving Telegram includes proyecto/fase and buttons still work. |
| RQF-056N - Reimbursement semantics employee != provider | NOT CLOSED | Existing implementation/test observed | Need align documents + screens + Telegram, not just one helper/test. |
| RQF-056O - Tocino/facturacion status/errors | PARTIAL | `6bb02b772` | Need safe retry, status visible, Telegram relevant status notifications, minimal non-secret payload logging. |
| RQF-057A - Operaciones end-to-end wiring audit | NOT STARTED | none | Need map bot -> teams -> players -> documents -> tournaments -> calendars -> incidents; identify Supabase/local/dead/duplicate/partial; prioritized migration stages. |

## Commits currently on sprint branch

| Commit | Note |
| --- | --- |
| `4a0a970b5` | RQF-056G-H harden third-party beneficiary selectors |
| `34c239a80` | RQF-056I expose draft solicitud cancellation |
| `6e4112f48` | RQF-056K preserve CFDI XML total in quick expense autofill |
| `a1185e079` | RQF-056L tighten budget visibility and mutation policy |
| `6bb02b772` | RQF-056O classify Tocino submission failures |
| `38bfdeca6` | RQF-056P add permissioned employee beneficiary requests |
| `467a5e947` | RQF-056Q preserve sprint ledger |
| `21c1d6d25` | RQF-056R clarify employee beneficiary selection UI |
| `1223cb74f` | RQF-056S update sprint ledger after beneficiary UI cut |
| `afbc9b421` | RQF-056T surface employee beneficiary profile access |
| `1b5e279ba` | RQF-056U update ledger for profile access visibility |
| `b8328dcf5` | RQF-056V expose employee beneficiary access summary |
| `1d32f9206` | RQF-056W update ledger for access summary |
| `ae5e6c8d5` | RQF-056X correct sprint ledger against stage plan |
| `2a664d00e` | RQF-056G close informe beneficiary selector contract |
| `fa1497d0c` | RQF-056G update ledger after informe selector closure |
| `117617173` | RQF-056H close anticipo beneficiary selector contract |
| `4145edfc4` | RQF-056I close draft cancellation policy |
| `36e2853d2` | RQF-056J close materiality preview contract |

## Current untracked artifacts intentionally left untracked

- `2.1. Cat?logo Contable.xls`
- `Estrategia de Autorizaci?n.xlsx`
- `branch-prune-manifest-20260730T015022Z.tsv`

## Next work order

1. Continue RQF-056L budget visibility/mutation route verification: directors definition, UI hiding, POST 403, and frozen versions.
2. Continue RQF-056M Telegram proyecto/etapa after L.
3. Continue through I, J, K, L, M, N, O in order.
4. Start RQF-057A only after RQF-056O is actually closed.
5. Sprint close only after broader tests + push + PR + review gate + final merge.

## QA/release plan context to preserve

Before labeling SamChat as deliverable, freeze a release candidate and run formal QA over the exact production commit:

- repository sanity;
- reproducible install;
- complete tests/lint/type/security where available;
- migration tests from fresh and existing DB;
- E2E by role for requests, approvals, expenses, CFDI, OCR, operations, reports, assistant;
- failure tests for AI/OCR/external services/retries/restarts/backups;
- Plataforma Sports UAT with PASS / PASS with observation / blocking FAIL / change request / out-of-scope classification;
- soak period before tag/deploy acceptance.
