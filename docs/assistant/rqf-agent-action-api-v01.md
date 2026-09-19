# RQF Agent Action API v0.1: disabled contract perimeter

Status: implemented internal perimeter; no registered action is enabled.

## Scope

The v0.1 package provides typed input schemas, identifiable policy and structured
precondition contracts, a fail-closed registry, identity and typed policy
envelopes, receipt/idempotency contracts, and negative tests. Caller payloads are
recursively rejected when they contain identity, tenant, role, or faculty fields;
only the trusted adapter may provide a `ResolvedPrincipal`. It does not mount an
HTTP route, alter UI behavior, invoke Jev, route natural language, or invoke a
domain handler.

All four definitions have `enabled=False` and
`disabled_reason=CANONICAL_SCOPE_UNPROVEN`:

| Public action | Existing canonical handler | Why it cannot be enabled |
| --- | --- | --- |
| `expense.get_status` | `expense.full_workflow_snapshot` | The adapter loads an expense by ID with no trusted principal or visibility check. |
| `budget.get_availability` | `budgets.snapshot` | The snapshot has no principal or authorized budget/tournament scope input. |
| `expense.diagnose_blocker` | `expense.full_workflow_snapshot` | It inherits the unscoped expense lookup. |
| `transfer.create_draft` | `expenses.create_solicitud_terceros` | `empleado_id` may come from payload or `AssistantContext`; actor/faculty is not proven from the authenticated session. |

Each definition declares its input field names and value types, a stable
`policy_id` plus `policy_version`, and machine-readable preconditions. The
perimeter receives a dispatcher only as an injected future dependency; its
disabled guard returns a denial receipt before that dispatcher can be called.

## Local validation versus domain gates

Before the disabled guard, the perimeter deterministically validates required
inputs and declared scalar types. It reports `INPUT_SCHEMA_INVALID` for absent,
null, blank required strings, invalid declared types, or undeclared fields when
the schema rejects additional fields. All four registered schemas are strict;
they reject arbitrary fields and non-mapping payloads. A malformed trusted
principal is denied as a local precondition rather than raising an exception.
This is shape validation only: it does not infer business rules such as positive
amount, existing provider, valid date, or authorized tournament.

The local preconditions are `trusted_principal`, `correlation_id`,
`payload_identity_free`, and (for drafts) `idempotency_key`. A failed local
precondition reports `PRECONDITION_UNSATISFIED` while retaining the concrete
precondition code in `evaluated_preconditions`; this replaces the prior
per-condition error label without losing its diagnostic evidence. Sensitive and
binary input remains redacted in every receipt.

`canonical_scope_bound` is deliberately not evaluated or marked satisfied here.
It remains a domain-owner gate, and `enabled=False` plus
`CANONICAL_SCOPE_UNPROVEN` continues to block every action after local checks.

## Domain-owner enablement inventory

### Gastos: expense status and blocker diagnosis

The owner must replace raw `expense_id` lookup with a function accepting a
trusted `ResolvedPrincipal` and a server-resolved row scope. It must deny
out-of-scope records before returning any state, document, CFDI, bank movement,
or accounting preview. The verifier must bind the result to the same expense
and principal scope.

### Presupuestos: availability

The owner must accept the trusted principal plus authorized tournament/version
scope, filter all source rows by that scope, and return a narrowly labeled
availability projection. A tournament ID supplied by the caller is a requested
target, never proof of access.

### Gastos: third-party transfer draft

The owner must source actor solely from the authenticated session, resolve its
effective faculty for the target tournament/beneficiary, and bind the created
draft to that actor and scope. The domain must enforce idempotency for the
document creation and verify the result is `borrador`; it must not transition
to submitted, approved, scheduled, paid, or accounted.

## UI migration rule

No UI route is migrated in this slice. A later UI migration is valid only when
its existing route delegates to the same enabled action contract; a parallel
mutating route is not an Agent Action API migration.

## Canon impact

Canon unchanged. This remains an internal fail-closed perimeter: no action is
enabled, no runtime route exists, and the validation produces no business effect
or handler invocation.
