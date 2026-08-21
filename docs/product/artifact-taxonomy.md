# Artifact Taxonomy

Purpose: define canonical artifact classes for SamChat so evidence closeouts,
runtime saved artifacts, exports, snapshots, assistant previews, agent handoffs,
sponsor packages, and budget source files are not treated as interchangeable.

## Operating Rule

An artifact is a product/runtime object only when it has an explicit runtime
surface such as a route, model, permission rule, export contract, UI entrypoint,
or approved assistant tool contract.

Markdown or JSON files under `artifacts/` can be evidence. They do not become
runtime product objects merely because they exist in the repository.

## Canonical Classes

| artifact class | purpose | storage | owner surface | authority / permissions | lifecycle | assistant read allowed | assistant create allowed | runtime product object |
|---|---|---|---|---|---|---|---|---|
| `evidence_closeout` | Record implementation evidence, validation, closeout, or rollout notes. | `artifacts/*.md`, `artifacts/*.json`, sprint docs, closeout docs. | Engineering / release process. | Git/review authority; not business runtime authority. | drafted -> reviewed -> historical. | Yes, if indexed or explicitly provided as evidence; must be labeled as historical. | No, unless the task is explicitly to draft documentation. | No. |
| `runtime_saved_artifact` | Store reusable assistant-created content associated with a conversation. | `assistant_artifacts` DB table. | Assistant runtime. | `assistant_save_artifact` tool contract; write path requires its configured confirmation and role policy. | draft -> confirmed save -> conversation-scoped reuse -> archived/deleted by future policy. | Yes, through assistant memory/RAG paths when scoped. | Yes, only through approved tool contract. | Yes. |
| `report_export` | Produce a portable representation of an already-supported report or dataset. | Generated file/response such as CSV, PDF, XLSX, or report export endpoint output. | Owning report module, for example assistant reports, finance reports, or budgets. | Export is allowed only when the source report is exportable and user permissions allow access. | generated -> delivered/downloaded -> optionally archived outside this taxonomy. | Yes, if the export metadata or source report is available. | No direct creation as an artifact; assistant may offer export only when source trace is exportable. | Yes, when backed by a route/export contract. |
| `expediente_snapshot` | Capture a case, tournament, or operational expediente state at a point in time. | Owning domain tables, route payloads, or future snapshot store. | Operations / tournament / case module. | Domain-specific read authority; durable snapshots need explicit route/model contract. | assembled -> reviewed -> frozen/exported -> superseded. | Yes, when permissioned and scoped to the case. | Draft only unless an approved snapshot write path exists. | Pending unless backed by a route/model/export contract. |
| `assistant_proposal_preview` | Present a proposed action, diff, report draft, or preview before authority. | Conversation trace, pending run, proposed action payload, or preview service response. | Assistant governed execution layer. | No effects by default; effect requires preview, authority, confirmation, receipt. | proposed -> revised -> confirmed/rejected -> receipt or discarded. | Yes. | Yes, as draft/preview only. | Runtime object only when tied to a concrete proposal/preview contract. |
| `agent_handoff_artifact` | Pass structured derived work between specialist agents. | In-memory task result, specialist harness output, or benchmark artifact. | Specialist agent/orchestrator layer. | Derived evidence only; no effect authority. | produced -> verified -> consumed by next agent -> archived in benchmark/trace if needed. | Yes, if exposed as trace/evidence. | Yes, within agent workflow only. | No, unless promoted by a separate approved runtime contract. |
| `sponsor_marketing_proof_package` | Package sponsor/client-facing proof, coverage, media, and delivery evidence. | Planned package store/export; current references live in offer docs and closeout evidence only. | Sponsor/marketing lane. | Requires human approval and a separate story/spec before runtime creation. | planned -> drafted -> reviewed -> approved -> delivered -> archived. | Read planned docs as product context only; do not claim runtime availability. | No, until a sponsor package builder is approved. | Planned. |
| `budget_source_artifact` | Provide source rows for budget import, snapshot comparison, or training seed. | Budget CSV/XLSX/source artifact path, budget import service, budget version metadata. | Presupuestos / Finance Spine. | Import authority belongs to budget services and approved admin routes. Source files are not live authority by themselves. | source file -> imported snapshot/version -> reviewed -> approved/frozen by budget workflow. | Yes, as source context or imported budget evidence. | No direct assistant creation unless future budget source workflow is approved. | Source artifact itself is not runtime authority; imported budget versions are runtime objects. |

## Non-Equivalence Rules

- `artifacts/*.md` and `artifacts/*.json` are evidence closeouts, not runtime
  product objects.
- `assistant_artifacts` does not replace report exports, expediente snapshots,
  sponsor packages, or budget source artifacts.
- Report exports are derived deliveries from an owning module. They are not
  assistant saved artifacts unless explicitly saved through the assistant tool
  contract.
- Expedited or case snapshots require their own route, model, export, and
  permission contract before they are durable runtime artifacts.
- Agent handoff artifacts are internal derived evidence. They do not authorize
  effects, filings, payments, budget changes, or client delivery.
- Sponsor/marketing proof packages remain planned until a separate approved
  story/spec defines storage, generation, permissions, delivery, and approval.
- Budget source artifacts can support imports or comparisons, but authority
  starts at the imported/reviewed budget version, not the raw source file.

## Assistant Boundary

The assistant may:

- read permissioned artifacts as evidence or context;
- cite artifact source, scope, and recency;
- draft proposals, previews, or report text;
- save runtime saved artifacts only through `assistant_save_artifact` when the
  tool contract permits it;
- offer exports only when a recent tool trace marks a report as exportable.

The assistant must not:

- treat closeout evidence as live product state;
- create durable artifacts without a route/model/tool authority contract;
- call sponsor proof packages implemented when they are only planned;
- turn a draft, preview, or agent handoff into a business effect;
- bypass module permissions by copying artifact content into another surface.

## Pending Decisions

- Whether `assistant_artifacts` needs a first-class admin UI.
- Which artifact types should be searchable by non-admin roles.
- Whether expediente snapshots need a dedicated durable store.
- Which sponsor/marketing proof packages are contractual deliverables versus
  optional add-ons.
- Whether report exports should be archived in a managed artifact store or stay
  as generated downloads.
