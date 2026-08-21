# Runtime Artifact Index

Purpose: index artifact and export surfaces that are real in SamChat, separate
them from evidence closeouts and planned sponsor packages, and expose gaps
without changing runtime behavior. A4 adds a read-only admin discoverability
view; it does not create an artifact center, execute exports, or archive files.

Related taxonomy: `docs/product/artifact-taxonomy.md`.

## Index

| surface | artifact class | storage / model | route / tool | UI / discoverability | tests / evidence | authority | status | notes / gaps |
|---|---|---|---|---|---|---|---|---|
| Assistant saved artifacts | `runtime_saved_artifact` | `AssistantArtifact` model; `assistant_artifacts` table | `assistant_save_artifact` write tool | Conversation-scoped through assistant runtime; `/admin/artifacts` lists the class read-only, not content | `src/devnous/gastos/models.py`; schema guard entries; assistant tool surface; `src/samchat/artifacts/runtime_index.py` | Assistant tool contract and configured confirmation/role policy | live | Does not replace exports, expediente snapshots, sponsor packages, or budget source artifacts. Archive/delete policy pending. |
| Owner/operator workflow preview | `assistant_proposal_preview` | Conversation trace only; no durable owner-pack artifact store | `owner.operator_workflow.preview` via assistant conversation routing | Assistant conversation response and tool trace; institutional artifact registry | `owner_operator_workflow.py`; `conversation_service.py`; `test_assistant_owner_operator_workflow.py`; router integration tests | Preview-only; approval required before any durable write or delivery | live/preview-only | Does not create folders, export files, notify operators, archive artifacts, or write owner-pack state. |
| Owner Entity Folder Workspace | `assistant_proposal_preview` | Conversation/tool trace only; no folder archive or publication store | `assistant_owner_entity_folder_workspace` read tool | Assistant tool trace; institutional artifact registry | `owner_entity_folder_workspace.py`; `test_assistant_owner_entity_folder_workspace.py`; institutional registry tests | Read-only source inspection with preview-only authority | live/preview-only | Workspace cards are previews only; no folder write, export, publication, or artifact archive is authorized. |
| Assistant report export | `report_export` | Generated response from assistant run trace or supplied report payload | `POST /api/assistant/reports/export` | Assistant report/export flow; generated download | `export_assistant_report(...)`; request/report tests around export prompt and report exportability | Export allowed only when selected run/report data is exportable and scoped to current user conversation | live | Generated delivery, not artifact archive. |
| Finance Platform export | `report_export` | Generated XLSX from Finance Platform read snapshot/exporter | `GET /admin/finanzas/export.xlsx` | Finance Platform admin view link | `admin_finance_platform_export_xlsx(...)`; Finance Platform exporter | Finance Platform read model/exporter; admin finance permissions | live | Separate from Assistant artifacts and legacy accounting cash-flow. |
| Presupuestos review export | `report_export` | Generated XLSX budget review workbook | `GET /admin/presupuestos/export.xlsx` | Presupuestos admin pages | `admin_presupuestos_export_xlsx(...)`; budget exporter tests | Canonical Presupuestos routes and budget services | live | Exported workbook is delivery artifact; budget authority remains in budget versions/services. |
| Presupuestos concept catalog export | `report_export` | Generated XLSX concept catalog | `GET /admin/presupuestos/conceptos/export.xlsx` | Presupuestos concepts admin surface | `admin_presupuestos_export_concepts_xlsx(...)`; route contract tests | Canonical Presupuestos routes and catalog services | live | Not an assistant artifact. |
| Presupuestos income mirror export | `report_export` / `budget_source_artifact` | Generated XLSX income-budget mirror | `GET /admin/presupuestos/torneo/{tournament_key}/ingresos/export.xlsx` | Tournament budget income admin surface | `admin_presupuestos_export_income_xlsx(...)`; budget monthly plan tests | Budget income routes and services | live | Source/export supports AR expected income, but raw workbook is not live authority by itself. |
| Accounting journal/ledger/balance exports | `report_export` | Generated CSV/PDF/HTML/Excel domain exports | Routes under `/admin/contabilidad/diario/*`, `/mayor/*`, `/balanza/*`, `/cierres/*` | Accounting admin pages | User route handlers and accounting report tests where present | Accounting domain routes/services | live/domain-specific | Domain exports, not Assistant artifacts. |
| COI/DIOT/informe exports | `report_export` | Generated XLSX/TXT/domain files | `/gastos/{id}/exportar-coi.xlsx`, `/documentos/{id}/exportar-coi.xlsx`, `/documentos/{id}/exportar-diot.*`, `/informes-de-gastos/{id}/exportar-*` | Expense/document/report pages | User route handlers and domain tests where present | Expense/document/accounting domain rules | live/domain-specific | Downloadable deliverables tied to approved document/domain state. Not assistant saved artifacts. |
| Legacy accounting cash-flow export | `report_export` | Generated XLSX from legacy accounting cash-flow route | `GET /admin/contabilidad/cash-flow/export.xlsx` | Legacy accounting cash-flow page | `contabilidad_cash_flow_export_xlsx(...)` | Legacy accounting route authority only | legacy/reference | Not Finance Spine authority and not assistant finance source authority. |
| Closeout evidence artifacts | `evidence_closeout` | `artifacts/*.md`, `artifacts/*.json`, sprint docs | Git files only | Repository/docs search | Closeout docs and sprint evidence | Git/review evidence only | historical evidence | Not runtime product objects. Must not be treated as live feature state. |
| Specialist/agent handoff artifacts | `agent_handoff_artifact` | In-memory agent result, specialist harness output, benchmark/test artifact | Specialist orchestrator/harness APIs | Internal trace/test output | `specialist_agents.py`, `specialist_harness.py`, specialist tests | Derived-evidence boundary; no effects | coded internal | Not product runtime unless promoted by separate approved route/model/tool contract. |
| Sponsor proof packages | `sponsor_marketing_proof_package` | No approved runtime store yet; references in offer docs and closeout evidence | None approved | None approved | Offer docs mention package builders | Human-approved sponsor/marketing story/spec required | planned | Do not claim implemented. Needs storage, generation, permission, delivery, and approval contract. |

## Current Gaps

- `assistant_artifacts` has only class-level read-only admin discoverability;
  content browsing and lifecycle controls are not approved.
- `assistant_artifacts` archive/delete lifecycle is not defined.
- Generated exports are delivered files, not a managed artifact archive.
- Owner/operator workflow previews are live assistant surfaces, but they remain
  `assistant_proposal_preview` traces. They are not saved runtime artifacts,
  folder archives, exports, notifications, or owner-pack writes.
- Expedited or case snapshots do not yet have a single durable artifact index.
- Sponsor proof packages have no approved runtime model, route, export, or UI.
- Several domain exports are discoverable only from their owning pages, not from
  a cross-product artifact center.
- Legacy accounting cash-flow export remains available as legacy/reference but
  must not be used as Finance Spine source authority.
- `/admin/artifacts` is a read-only discoverability surface. It must not grow
  POST routes, export execution, archive/delete lifecycle, or sponsor package
  creation without a separate approved story/spec.

## Boundary Rules

- Assistant saved artifacts are conversation-scoped runtime artifacts.
- Owner/operator assistant previews are conversation-scoped proposal previews,
  not `assistant_artifacts` rows and not durable owner-pack objects.
- Report exports are owned by their domain modules.
- Closeout files are historical evidence.
- Budget source/export files can support budget workflows, but imported budget
  versions and approved budget services hold runtime authority.
- Planned sponsor packages require a separate approved implementation track.
