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
| RQF-056K - Correccion definitiva de totales CFDI XML/PDF | CLOSED_COMMITTED | `1fa60c1ee`, plus `6e4112f48` | Synthetic CFDI fixture reproduces the $128 case without customer data; tests verify XML Total authority, SubTotal + net taxes, inconsistency rejection for $124, and retenciones netting. Real customer XML was intentionally not committed. |
| RQF-056L - Presupuestos visibility/editing | CLOSED_COMMITTED | `3b46acdfc`, plus `a1185e079` | Tests verify only superadmin mutates, Directores/Alicia/superadmin view, named operations users cannot view or mutate even with budget tokens, budget nav hiding, operations analytics policy, frozen monthly plan rejection, and POST direct 403 before mutation/form-read. |
| RQF-056M - Telegram proyecto y etapa | CLOSED_COMMITTED | `7e5734ec9` | Existing Telegram message builder is now covered for SOLICITUD and INFORME: Proyecto and Etapa/subproyecto render in message text, action hint remains present, and approval/rejection callback keyboard data remains unchanged. |
| RQF-056N - Reimbursement semantics employee != provider | CLOSED_COMMITTED | `db6010750` | Telegram, pending-payment rows, payment service, and document detail semantics now identify employee reimbursements separately from proveedores; document detail prefers employee beneficiary while preserving provider bank-account data. |
| RQF-056O - Tocino/facturacion status/errors | PARTIAL | `6bb02b772` | Need safe retry, status visible, Telegram relevant status notifications, minimal non-secret payload logging. |
| RQF-057A - Operaciones end-to-end wiring audit | NOT STARTED | none | Need map bot -> teams -> players -> documents -> tournaments -> calendars -> incidents; identify Supabase/local/dead/duplicate/partial; prioritized migration stages. |
| RQF-057B - Telegram notification recipients audit | CLOSED_COMMITTED | this commit | Production config and DB audit confirmed Odilon/Benjamin Telegram readiness and timing; Odilon approval -> Finance alert was wired into the approve branch without changing authorization semantics. Evidence: `docs/sprints/rqf-057b-telegram-notifications-audit.md`. |
| RQF-057C - Regional operator beneficiaries | CLOSED_COMMITTED | `e6ffb001a` | Authorized third-party users can select active Operadores Regionales in Anticipos and Informes; requester ownership/approval routing stays unchanged; Anticipos swaps to the operator bank account; Informes persist operator beneficiary on cuenta/document. Evidence: `docs/sprints/rqf-057c-regional-operator-beneficiaries.md`. |
| RQF-057D - Solicitudes search by proveedor | CLOSED_COMMITTED | pending commit | `/gastos-terceros` now shows a dedicated consultation filter bar above the solicitudes table with Referencia Operaciones, `Por Proveedor`, and Concepto. Test verifies `terceros-search-proveedor`, `data-proveedor`, normalization, and `matchProveedor`. Evidence: `docs/sprints/rqf-057d-provider-search.md`. |
| RQF-057E - No deducibles accounting rule | BACKLOG / customer delta | none | If a Solicitud de Transferencia or Informe line is not linked to factura/CFDI, route accounting impact to Gastos No Deducibles according to project-specific rule from ReglaNoDeducibles tab. |
| RQF-057F - SAT massive download and reconciliation | BACKLOG / customer delta | none | Confirm automatic SAT download still runs daily at 23:00 and 09:00; separate Emitidos/Recibidos; add SAT vs Solicitudes reconciliation view. |
| RQF-057G - Authorization strategy matrix | PARTIAL_LOCAL_PENDING_COMMIT | this commit | Foundation resolver created from customer matrix and Francisco role mapping; board registered in Control de accesos as configuracion.estrategias_autorizacion; profile board implemented; send-time advisory evidence is persisted and now visible in document detail; document detail also compares the suggested route against actual approvals as preview-only evidence; drafts now show a pre-send advisory preview using the same evidence builder; approvals now persist and surface non-blocking authorization-route soft warnings when actual approvals do not cover matrix roles; a read-only warnings dashboard is available for finance/admin review. Hard enforcement still pending. Evidence: docs/sprints/rqf-057g-authorization-strategy-matrix.md, docs/sprints/rqf-057g2-authorization-profile-board.md, docs/sprints/rqf-057g3-authorization-evidence.md, docs/sprints/rqf-057g4-authorization-evidence-detail.md, docs/sprints/rqf-057g5-authorization-route-preview.md, docs/sprints/rqf-057g6-authorization-pre-send-preview.md, docs/sprints/rqf-057g7-authorization-soft-warning.md, docs/sprints/rqf-057g8-authorization-warnings-dashboard.md. |

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
| `1fa60c1ee` | RQF-056K close CFDI quick expense total contract |
| `3b46acdfc` | RQF-056L close budget authorization route contract |
| `7e5734ec9` | RQF-056M close Telegram project phase contract |


## Customer Excel delta captured on 2026-07-30

These items are now part of the master plan, but are not claimed as closed by RQF-056G through RQF-056L:

1. Telegram notifications
   - Verify Odilon receives approval notifications for Anticipos and Informes de Gastos.
   - Verify Benjamin receives notifications for Solicitudes de Transferencia, Anticipos, and Informes de Gastos.
   - Document the exact event/timing that triggers each notification.
2. Anticipos and Informes for Operadores Regionales
   - Operations users authorized for third-party requests must also be able to request for Regional Operators.
   - This requires a separate beneficiary class from employees unless existing data proves operators are modeled as employees.
3. Solicitudes de Transferencia provider search
   - In the solicitudes-by-reference view, add provider search alongside Concepto and Referencia.
4. No deducibles rule
   - When a Solicitud or Informe line is not linked to a factura/CFDI, affect the project-specific Gastos No Deducibles account.
5. SAT massive download
   - Confirm scheduled automatic downloads at 23:00 and 09:00.
   - Split Emitidos and Recibidos.
   - Add SAT vs Solicitudes reconciliation view.
6. Estrategia de Autorizacion
   - Add board/configuration for authorization strategies according to the business matrix.
   - Applies to all Solicitudes de Transferencia, Anticipos, and Informes de Gastos.

## Current untracked artifacts intentionally left untracked

- `2.1. Cat?logo Contable.xls`
- `Estrategia de Autorizaci?n.xlsx`
- `branch-prune-manifest-20260730T015022Z.tsv`

## Next work order

1. Continue customer Excel delta in order: RQF-057E No deducibles accounting rule, RQF-057F SAT download/reconciliation. Keep RQF-057G Authorization strategy matrix paused at advisory/board scope unless the customer asks to resume it.
2. Keep RQF-056O Tocino/facturacion status/errors parked unless support pressure makes it urgent again.
3. Sprint close only after broader tests + push + PR + review gate + final merge.

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
