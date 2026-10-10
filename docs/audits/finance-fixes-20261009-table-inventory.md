# Finance fixes: information table and home inventory

Date: 2026-10-09. Evidence: isolated worktree code and synthetic browser fixtures;
no production records, deployment or authenticated business acceptance.

The existing `modernize_html_middleware` / `MODERN_UI_HEAD_INJECTION` covers
server-rendered HTML in the active `copa_telmex_dashboard.py` runtime. It now
reuses existing table wrappers, supplies bounded two-axis scrolling and sticky
headers, and gives each overflowing visible table an independent horizontal
control at the table/viewport bottom. The existing global `table overflow:hidden`
was overridden for information tables because browser verification demonstrated
it prevented sticky headings. Dynamic fetched historical-accounting tables are
initialized after insertion; removed tables release observers, window listeners
and their controls. Key/value detail tables without `thead` retain their layout.
Static FileResponse SPA bundles retain the existing middleware exclusion.

| Connected surface | Reference coverage | Table behavior coverage |
| --- | --- | --- |
| `/documentos/todos` | Moved actual Operations reference from column 13 to column 2; preserved SamChat reference and adjusted default sort index | Shared enhancement |
| `/documentos/control-presupuestal`, `/documentos/pendientes` | Already among first 3 columns, with selection and SamChat reference | Shared enhancement; existing decision columns retained |
| `/gastos-terceros`, `/informes-de-gastos` | Already among first 2–3 columns | Shared enhancement; existing filtering retained |
| `/documentos/pendientes-pago` | Added actual Documento reference in column 2 for solicitudes and informes | Shared enhancement |
| Informe detail linked solicitudes | Added each linked Documento reference in column 2 | Shared enhancement |
| `/admin/gastos/expenses` | Added column 2 and matching client CSV payload/header; one bulk lookup of only owning informe/solicitud IDs from the selected expense set | Shared enhancement |
| `/admin/gastos/cfdis/matching` pending and linked expense tables | Added column 2 from same batched owning-document lookup | Shared enhancement |
| `/admin/gastos/sin-cuenta-contable` | Added column 2; summary/detail/empty colspans aligned | Shared enhancement |
| `/admin/finanzas` payable, pending COI, fiscal blocker and cross-month rows | Added among first 2–3 columns; actual document/informe/solicitud reference added to existing projection | Shared enhancement |
| `/admin/finanzas/payment-run`, `/admin/finanzas/payment-history` | Already early; closure detail reference handled by coordinating builder | Shared enhancement |
| `/panel/operaciones-console` commitment rows and connected budget commitment view | Actual `d.referencia_operaciones` added to existing canonical commitment query/mapping; early column, no extra source/query path | Shared enhancement |
| Operational cashflow upcoming-documents preview | Added actual Documento reference in column 2 | Shared enhancement |
| `/admin/soporte/estado` approval/payment aging and incomplete-workflow notification rows | Added actual Documento reference in column 2; no new queries | Shared enhancement |
| `/admin/contabilidad/historica` fetched reconciliation/comparison/report tables | Existing source-specific accounting IDs retained | Dynamic enhancement |
| CxC `/admin/finanzas/cuentas-por-cobrar` | Income CFDI/link/collection identifiers retained; no owning operational Documento relation to invent | Shared enhancement reuses `.ar-table-wrap` |
| Cashflow monthly/forecast, finance action queue and unbalanced pólizas, canonical budget allocation grids | Aggregate rows or their own accounting/budget identities; no unique Operations reference | Shared enhancement; grids retain their column sizing |
| Artifacts, Sam Inbox, Direction/executive dossier, sports/tournament/team/player/registration tables | Native artifact/source/tournament/entity IDs retained; no unique per-row owning Documento contract | Shared enhancement on HTML tables; registration asset/card grids are not tables |
| SAT CFDI import/request tables, free CFDI tables, bank movements and reconciliation candidates | Native UUID/job/request/bank identities retained; do not infer document ownership from amount/date/name | Shared enhancement |
| Support tickets, permissions matrix, health, catalog/provider/user/payroll tables | Their own entities or aggregates, no Operations document reference | Shared enhancement |

Home now routes already-administrative roles (`admin`, `finanzas`, `superadmin`,
`super_admin`) to the existing `/panel` personalized administration panel.
Employee `/panel`, other-profile `/assistant`, and unauthenticated login
behavior remain intact. `/panel` retains `get_current_empleado`,
`visible_tools_for` and Direction-specific authorization; no authority expanded.

Acceptance evidence:

- `tests/browser/test_information_tables.py`: actual injected theme extracted
  from the live entrypoint; long/multiple tables, sticky heading after vertical
  scroll, independent controls, widths 1440/1024/768/390, resize, keyboard and
  native horizontal scrolling, simultaneous controls, fetched-table insertion
  and removal, and no JavaScript errors.
- `tests/unit/gastos/test_finance_frontend_fixes.py`: home role/login cases,
  reference/header/sort alignment, real projection inheritance, empty/batched
  restricted lookup, actual expense HTML and structured client CSV data.
- Screenshots from local synthetic fixtures: `/tmp/finance-tables-1440.png`,
  `/tmp/finance-tables-1024.png`, `/tmp/finance-tables-768.png`,
  `/tmp/finance-tables-390.png`.

Physical touch gesture behavior, authenticated role journeys and browser UAT
with real operational rows remain business-acceptance work. No dependency added.

Canon unchanged: these frontend/read-model presentation fixes expose existing
record lineage, restore shared table affordances and select an existing
personalized landing page. They create no business rule, persistence mutation,
permission, financial state, new integration or acceptance claim.
