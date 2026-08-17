# RQF-ASSISTANT-ARTIFACT-AUDIT-001 ? Intelligent artifact audit

Status: DRAFT_COMMITTED_LOCAL_PENDING_REVIEW  
Scope: inventory and product-readiness audit only  
Runtime wiring: not performed  
World-effect closure: not claimed

## Claim boundary

This slice does not make SamChat more autonomous and does not expose new write paths. It records which intelligent artifacts already exist, how they overlap, which ones are safe to expose as read-only assistant context, and which ones should remain internal, legacy, or migration-only.

The product rule for this audit is:

> Existing code becomes assistant capability only when it has a clear source contract, authority boundary, user-facing purpose, and regression path.

## Executive finding

SamChat already contains multiple serious institutional-intelligence artifacts. The main risk is not lack of code; the risk is wiring raw projections, duplicate abstractions, or legacy Supabase surfaces into the assistant without product semantics.

The next assistant work should therefore proceed in this order:

1. Keep the institutional registry as the assistant's read-only map of available artifacts.
2. Wire only narrow, source-bound read tools before broader orchestration.
3. Consolidate duplicated evidence adapters before adding more live context paths.
4. Treat domain services as governed tools, not as direct conversational shortcuts.
5. Keep all writes behind preview, human approval, idempotency, and audit receipts.

## Artifact inventory

| Artifact family | Current state | Product disposition | Notes |
| --- | --- | --- | --- |
| `samchat.assistant.institutional_artifact_registry` | Wired read-only registry | Keep as canonical map | Already exposes artifact IDs, domains, contracts, authority level and next wiring steps. |
| `samchat.finance_platform.service` | Wired through finance strategy snapshot | Ready to keep/use | Strong read-only finance projection for cash, close, tax, payment run and finance brief. Should not duplicate lower-level gasto services. |
| `samchat.assistant.closeout_diagnostics` | Wired | Ready to keep/use | Correct pattern: answers a bounded question such as whether accounting can close and why. |
| `samchat.accounting_historical.service` | Available, not wired | Useful source service | Valuable for institutional accounting memory. Do not expose full import/COI machinery to chat. |
| `samchat.assistant.historical_accounting_precedent` | Wired read-only | Historical precedent query | Searches historical policy lines/headers for account candidates by concept/provider/project/account; returns evidence, not automatic account assignment. |
| `samchat.sports_platform.service` | Partial | Use only through narrowed wrappers | Rich operations projection, but too broad/raw for direct assistant exposure. Start with audit/status/action queue surfaces. |
| `samchat.assistant.sports_platform_audit` | Available, not wired | Ready as gate before wiring sports modules | Good product filter: classifies what is assistant-ready vs internal/demo. |
| `samchat.assistant.sports_operations_status` | Wired read-only | Narrowed operations wrapper | Assistant-safe summary over local tournament source, mission, incidents, roster risk, matchday state and action queue; accepts SOUL Wizard draft context and does not expose raw Sports Platform. |
| `samchat.sports_platform.director_general_dossier` | Partial | Useful for Owner Pack, not raw | Maps directly to owner's requested folders by entity. Needs live-source confidence and missing-field reporting. |
| `samchat.assistant.owner_pack_readiness` | Wired | Ready to keep/use | Current best example of read-only product semantics: status, evidence found, missing evidence and next questions. |
| `samchat.assistant.owner_pack_live_evidence` | Wired | Keep but later consolidate | Correct fail-closed local DB adapter. Should converge with other live evidence adapters later. |
| `samchat.assistant.owner_entity_dossier_live` | Wired read-only | Owner/entity folder live audit | Uses local tournament source plus DG dossier audit to report supported evidence, missing evidence and aggregate-only non-claims. |
| `samchat.assistant.soul_wizard` | Available contract / UI-oriented | Continue as tournament creation intake layer | Good foundation for Operations creating tournament SOULs step by step: phases, dates, activities, categories and evidence. |
| `samchat.assistant.tournament_goal_*` | Partial | Consolidate with SOUL Wizard before more UI | Covers cloning/planning/diff logic. Avoid two separate tournament creation stories. |
| `samchat.assistant.specialist_agents` and `specialist_orchestrator` | Eval-centric foundation | Keep for assistant architecture, not production writes | Good precedent -> verification -> proposal pattern after verifier invariants. Needs real operational cases and handoff UX. |
| `samchat.assistant.analyst_workbench` | Wired/canary-style | Keep as conversation quality layer | Useful for sufficiency, suggested routes, evidence diagnostics and no-overclaim guard. |
| `samchat.assistant.analyst_live_evidence` | Dormant/allowlisted live evidence | Keep, but consolidate later | Broad adapter for expenses, budgets, CFDI, vendors and documents. Avoid duplicating owner pack live evidence long-term. |
| `samchat.assistant.request_reports` | Wired read-only report path | Keep | Useful deterministic reporting path for requests. |
| `samchat.assistant.receipt_workflow_draft` | Draft/proposal layer | Keep as preview pattern | Useful for turning messy user inputs into inert proposed actions. |
| `samchat.assistant.document_action_planner` | Planner layer | Keep as candidate workflow router | Useful, but should stay behind explicit supported actions and authority checks. |
| `devnous.gastos.services.*` | Production domain engines | Treat as governed domain tools | These are not all assistant artifacts. Expose through typed read/preview/write contracts only. |
| `devnous.tournaments.core.intelligence_program` | Workspace generator pattern | Useful source pattern | Good match for owner/entity folders. Needs local DB path; do not rebuild around Supabase. |
| `devnous.tournaments.core.operations_module`, `finance_module`, `marketing_module` | Domain modules | Useful, needs overlap review | May duplicate sports platform projections; inspect before wiring. |
| `samchat.sports_platform.sponsor_media` | Workflow/snapshot for sponsor proof | Useful later | Belongs after Owner Pack base data and operations status are stable. |

## Duplicate and overlap risks

### Sports operations projections

`samchat.sports_platform.service`, `sports_platform_audit`, `director_general_dossier`, `owner_pack_readiness`, `owner_pack_live_evidence`, `tournament_goal_source`, and SOUL Wizard all orbit the same operational corpus.

Decision: do not expose all of them as separate assistant tools. Use this hierarchy:

1. `tournament.soul_snapshot` or local tournament source as corpus.
2. `sports_platform_audit` to decide which modules are safe.
3. `owner_pack_readiness` for Director General readiness questions.
4. `director_general_dossier` only through an Owner Pack wrapper.
5. SOUL Wizard for creation/intake, not for reporting.

### Finance and accounting projections

`finance_platform.service` overlaps with many `devnous.gastos.services.*` modules. The difference should stay clear:

- Finance platform = read-only executive/control projection.
- Gasto services = transactional domain engines.
- Accounting historical = precedent/memory layer.
- Accounting cleanup/export services = operational accounting workflows.

Decision: keep assistant-facing tools narrow: status, blockers, previews and precedent lookups. Do not make the assistant call arbitrary domain services directly.

### Evidence adapters

`analyst_live_evidence` and `owner_pack_live_evidence` are both local read adapters. They are correct for now because scopes differ, but they should later converge under one evidence adapter pattern with:

- explicit source path;
- row-level permission scope;
- fail-closed empty results;
- trace metadata;
- no hidden writes.

## Legacy and no-new-wiring surfaces

The following Supabase-related surfaces should not receive new assistant wiring because the product direction is to move away from Supabase and onto local controlled infrastructure:

- `samchat.tournaments_v2.supabase_client`
- `devnous.tournaments.core.supabase_sync`
- `devnous.copa_telmex.supabase_authority`

They can remain temporarily as migration compatibility or historical bridge code, but they should not become new assistant dependencies.

Also avoid reviving broad legacy narratives such as ?99+ agents? unless backed by current source, contracts and tests. The assistant should sell operational capability, not mythical agent counts.

## Recommended integration queue

### 1. Artifact registry UI/assistant explanation

Expose the registry as a way for the assistant to explain ?what tools and institutional projections I can safely use.? This is already wired read-only and should remain harmless.

### 2. Sports operations status wrapper

Initial read-only router exposure is implemented. Continue improving source coverage while keeping this a narrowed tool over `sports_platform_audit` / `sports_platform.service` that also accepts SOUL Wizard draft context, so planning and live operations status stay connected. It answers:

- what is ready;
- what is missing;
- what is risky;
- what next action is suggested;
- whether the SOUL Wizard draft is ready for operations review or still missing phases/dates/activities.

Do not expose raw sports platform snapshots. Current limitation: local source coverage still marks schedule, communications and rich dates as unavailable when they are not present in controlled local tables.

### 3. Owner entity dossier live wrapper

Initial read-only router exposure is implemented. It connects `director_general_dossier` to live local tournament sources only as a readiness/missing-fields report, with explicit aggregate-only non-claims when local data is not truly per entity. This is directly tied to the owner request for folders by entity.

### 4. Historical accounting precedent query

Initial read-only router exposure is implemented. It queries historical policy lines/headers for ?what account did we use before for cases like this?? and returns candidates with evidence paths, non-claims and safety summary. This supports accounting cleanup without inventing classification.

### 5. SOUL Wizard continuation

Continue the tournament creation wizard, but reconcile it with `tournament_goal_*` so there is one creation story:

source tournament -> clone draft -> phases/dates/activities -> validation -> preview -> approval-gated creation.

### 6. Specialist agents after evidence contracts

Use the specialist architecture for evals and multi-step reasoning, but only after the operational corpus and verifier handoff are stable. Specialist agents should propose; they should not mutate production.

## Acceptance checks for this audit

- The audit distinguishes wired, partial, available-not-wired, legacy and domain-engine code.
- Supabase paths are explicitly marked no-new-wiring.
- Duplicate/overlap risks are named before new integration work starts.
- The next integration queue preserves the original assistant roadmap: operational corpus, read-only evidence, preview, approval, execution.
- No runtime behavior or production write path changes in this slice.
