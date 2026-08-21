# Assistant Artifact Boundary

Purpose: define what the assistant may do with artifacts without becoming the
artifact system or bypassing the owning domain's authority.

Related documents:

- `docs/product/artifact-taxonomy.md`
- `docs/product/runtime-artifact-index.md`
- `docs/assistant/product-canon.md`
- `docs/assistant/owner-ai-needs.md`

## Operating Rule

The assistant may read, cite, draft, preview, propose, and route users toward
artifact workflows. Durable artifacts require an owning route, model, export,
permission, or tool contract. The assistant must not convert a draft, preview,
handoff, or closeout into a business effect without explicit authority.

## Allowed Assistant Artifact Behaviors

The assistant may:

- read permissioned artifacts as context or evidence;
- cite artifact source, scope, recency, and authority limits;
- draft artifact text, report sections, proposal content, or missing-evidence
  checklists;
- generate a preview or proposed diff before a durable artifact change;
- offer an export only when a recent tool trace marks the source report as
  exportable;
- save a `runtime_saved_artifact` only through `assistant_save_artifact` when
  the tool contract, confirmation, and role policy permit it;
- route the user to the owning module when a first-class UI or export already
  exists.

## Prohibited Behaviors

The assistant must not:

- treat `artifacts/*.md` or closeout docs as live product state;
- create durable artifacts without an approved route, model, export, permission,
  or tool contract;
- treat a preview, draft, proposal, or agent handoff as an executed effect;
- publish or deliver sponsor/client-facing proof packages without a separate
  approved story/spec and human approval;
- replace the owning module's UI, export, or permission model;
- copy artifact content across modules to bypass permissions;
- claim that a planned artifact integration is live.

## Boundary By Artifact Class

| artifact class | assistant may read | assistant may draft | assistant may create durable object | boundary |
|---|---:|---:|---:|---|
| `evidence_closeout` | yes | only as documentation task | no | Historical evidence only; not live state. |
| `runtime_saved_artifact` | yes, when scoped | yes | yes, only via `assistant_save_artifact` | Conversation-scoped assistant artifact; not a cross-domain artifact center. |
| `report_export` | yes, through source trace/report | no direct artifact draft | no direct artifact creation | Export belongs to the owning module and its export route. |
| `expediente_snapshot` | yes, when permissioned | yes, as preview | only with approved snapshot contract | Requires owning case/operations route, model, permissions, and receipt. |
| `assistant_proposal_preview` | yes | yes | no effect by itself | Draft/proposal remains inert until explicit authority. |
| `agent_handoff_artifact` | yes, if exposed in trace | yes, inside agent workflow | no | Derived evidence only; no effect authority. |
| `sponsor_marketing_proof_package` | planned docs only | draft only | no | Planned until separate sponsor package story/spec is approved. |
| `budget_source_artifact` | yes | no direct mutation | no direct artifact creation | Budget authority starts in imported/reviewed budget versions and services. |

## Required Flow For Durable Artifacts

Any durable artifact creation or mutation must follow this flow:

1. Read/context: identify the source data, artifact class, owner surface, and
   permissions.
2. Draft: prepare non-durable content or structured proposal.
3. Preview/diff: show what would be created or changed.
4. Explicit authority: require the configured human approval, role, and tool or
   route authority.
5. Durable write/export: execute only through the owning route/model/tool.
6. Receipt/evidence: record what source was read, what preview was approved,
   what changed, and where the durable result lives.

## Explicit Non-Authorities

- `assistant_save_artifact` does not authorize finance, budget, tournament,
  sponsor, accounting, payment, or expediente writes.
- `assistant_artifacts` does not become a general artifact center.
- Report exports remain owned by their source modules.
- Sponsor packages remain planned until approved separately.
- Agent handoffs are internal derived evidence only.
- Closeout files are not live runtime state.

## Pending Runtime Decisions

- Whether `assistant_artifacts` should have first-class admin discovery.
- Whether report exports should be archived as managed artifacts.
- Whether expediente snapshots need a durable snapshot store.
- Whether sponsor proof packages require their own approval and delivery ledger.
- Which artifact classes should be searchable by non-admin roles.
