# RQF-054 — SamChat Claude-Code Runtime Gap

Status: RQF-054DEF_CLOSED_MERGED_DEPLOYED

## Executive summary

SamChat already has many of the hard pieces required for an operational
assistant: read-only tools, owner-pack artifacts, finance/accounting snapshots,
case memory, specialist agents, a quality gate, and a governed action boundary.

The current failure mode is not lack of tools. The failure mode is that a user
turn is still routed by a collection of domain detectors and aliases before a
single work loop proves that the chosen tool actually answers the question.

That is why one sentence can route correctly and the next sentence can fall back
to the wrong surface. For example:

- "Que evidencia tenemos de pagos o apoyos?" should inspect owner/operator
  payment evidence.
- "Que pagos estan pendientes?" should inspect pending payment workflow.
- They share words, but they are not the same job.

Claude Code's useful pattern is not its terminal UI. The useful pattern is the
turn contract:

1. Understand the job.
2. Load relevant memory/context.
3. Select tools as candidates, not as destiny.
4. Execute read-only tools under policy.
5. Verify that the result answers the user's actual question.
6. Render a human answer with sources, gaps, and next actions.
7. Refuse or ask a precise question when evidence is insufficient.
8. Persist resumable case state.

SamChat needs this same contract for business work.

## Source inspection notes

Reference inspected: `/root/claudeleaked`.

Useful architectural patterns observed:

- `src/query.ts`: central turn loop carrying messages, tool context,
  compaction state, stop hooks, and transition reason.
- `src/query/deps.ts`: injected dependencies for testability instead of
  hidden global calls.
- `src/query/config.ts`: immutable per-turn config snapshot.
- `src/services/tools/toolOrchestration.ts`: tool calls are partitioned by
  concurrency safety; read-only batches may run together while unsafe calls run
  serially.
- `src/services/tools/toolExecution.ts`: tool execution is wrapped with policy,
  progress, hooks, telemetry, errors, and result messages.
- `src/services/tools/StreamingToolExecutor.ts`: tool execution can begin while
  the model is still streaming, but results remain ordered and policy-bound.
- `src/memdir/findRelevantMemories.ts`: memory is selected based on query and
  manifest, not blindly stuffed into context.
- UI components such as `ToolUseLoader`, `GroupedToolUseContent`,
  `MessageResponse`, `AgentProgressLine`, `TaskListV2`,
  `ContextVisualization`, and `MemoryUsageIndicator`: tool progress, context,
  and evidence are first-class parts of the experience.

Legal/operational boundary:

- Do not copy leaked source into SamChat.
- It is acceptable to implement the same product architecture using our own
  code, names, contracts, and tests.

## SamChat current state

Relevant SamChat pieces already exist:

- `conversation_service.py`: central handler, but currently grown as a route
  chain rather than a single adjudicated work loop.
- `request_intent.py` and `request_router.py`: deterministic domain routing.
- `tool_registry.py`: tool metadata, surfaces, risk, roles, read/write split.
- `agent_runtime.py`: canary/runtime activation and read-only posture.
- `response_quality_gate.py`: catches raw JSON, loops, and unicode gibberish.
- `executive_answer_renderer.py`: converts some tool outputs into executive
  prose.
- `case_memory.py`: resumable case summaries.
- `specialist_orchestrator.py`: explicit read-only specialist workflow.
- Owner artifacts: readiness, variable Q&A, entity folder workspace, export
  preview, SOUL bridge, live evidence, and status reports.
- Finance artifacts: accounting Q&A, closeout diagnostics, platform snapshot,
  historical precedent, and accounting previews.

Gap:

The system lacks a mandatory `work_turn` contract that sits above these pieces
and decides whether a tool result is sufficient before the final answer is
allowed through.

## Required contract

Introduce a provider-agnostic `AssistantWorkTurn` layer.

### WorkFrame

Each user message should be converted to a `WorkFrame`:

```python
WorkFrame(
    user_message=str,
    interpreted_goal=str,
    audience="operator|finance|owner|admin|unknown",
    domain="owner|finance|operations|documents|mixed|unknown",
    task_kind="status|evidence|readiness|diagnostic|draft|action|unknown",
    explicit_entities=[...],
    temporal_scope={...},
    required_evidence=[...],
    forbidden_interpretations=[...],
    answer_contract={...},
)
```

Key rule: if a phrase can mean more than one business job, the `WorkFrame` must
keep multiple candidates alive until tool sufficiency is checked.

### ToolCandidate

Tools are candidates, not answers:

```python
ToolCandidate(
    tool_name=str,
    surface=str,
    read_only=bool,
    answers_question_claim=str,
    required_inputs=[...],
    missing_inputs=[...],
    confidence=float,
    rejection_reason=str|None,
)
```

The runtime may execute multiple read-only candidates if the question is broad,
but it must not present a candidate result unless it satisfies the user goal.

### SufficiencyVerdict

After a tool returns, the runtime evaluates:

```python
SufficiencyVerdict(
    tool_name=str,
    answers_user_question=bool,
    supported_claims=[...],
    unsupported_claims=[...],
    missing_evidence=[...],
    next_questions=[...],
    safe_to_render=bool,
)
```

This is the missing guardrail. It would have blocked the wrong answer:

- User: "Que evidencia tenemos de pagos hechos en agosto?"
- Wrong tool: pending payments overview.
- Sufficiency: `answers_user_question=False` because pending payments are not
  evidence of payments already made.
- Final answer: either route to owner/operator payment evidence or say that the
  required payment evidence source is not connected.

### ExecutiveAnswer

The final answer must be rendered from the frame and sufficiency result:

```python
ExecutiveAnswer(
    headline=str,
    direct_answer=str,
    evidence=[...],
    gaps=[...],
    next_questions=[...],
    module_links=[...],
    authority_boundary="read_only",
)
```

No raw tool payloads, no JSON function calls, no hidden unsupported claims.

## Immediate implementation slices

### RQF-054A — WorkFrame classifier

## 2026-08-24 implementation note

RQF-054A is implemented on branch `codex/rqf-054-claude-code-runtime-gap`:

- `src/samchat/assistant/work_frame.py` adds a read-only WorkFrame classifier.
- `conversation_service.py` appends `assistant.work_frame` as a trailing trace without displacing the primary tool trace.
- Unit and integration coverage protects payment-evidence vs pending-payment semantics, owner readiness, finance/accounting status, SOUL data coverage, and unknown-request clarification.

Local focused verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_assistant_work_frame.py tests/unit/test_assistant_request_router_integration.py
# 39 passed
```

Next slice: RQF-054B Tool candidate adjudicator.

## 2026-08-24 implementation note - RQF-054B/C

RQF-054B/C is implemented on branch `codex/rqf-054bc-tool-adjudicator-sufficiency`:

- `src/samchat/assistant/tool_adjudicator.py` adds deterministic tool-candidate adjudication against the WorkFrame.
- `src/samchat/assistant/response_sufficiency.py` adds a post-tool sufficiency gate.
- `conversation_service.py` now appends governance traces without displacing the primary tool trace.
- Known bad semantic path is fail-closed: historical payment evidence questions cannot be answered with pending-payment queue summaries.
- Valid legacy surfaces remain allowed while the full tool registry mapping is completed.

Focused verification:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_assistant_tool_adjudicator_sufficiency.py tests/unit/test_assistant_work_frame.py tests/unit/test_assistant_request_router_integration.py
# 44 passed
```

Next slice: expand the candidate registry from hard-coded semantic invariants into the existing `tool_registry.py` metadata, then add multi-candidate read-only execution for broad questions.



## 2026-08-24 implementation note - RQF-054D/E/F foundation

RQF-054D/E plus the semantic-registry foundation for RQF-054F are implemented on branch `codex/rqf-054def-executive-workloop`:

- `tool_registry.py` now exposes a semantic assistant tool registry with domain, task-kind, evidence-output, rejected-interpretation, and read-only metadata.
- `tool_adjudicator.py` uses the semantic registry to accept or reject candidates against the WorkFrame instead of relying only on ad-hoc hard-coded mappings.
- `work_turn_renderer.py` adds a unified WorkTurn renderer that appends a read-only authority boundary, blocks raw tool payloads, preserves controlled deterministic surfaces, and renders sufficiency gaps when evidence is not enough.
- `assistant_workspace_trace.py` adds `assistant.work_turn_trace`, a UI-facing Claude-Code-like work loop trace: understand frame, adjudicate candidate, verify sufficiency, render executive answer, hold read-only boundary.
- `conversation_service.py` appends governance traces without displacing the primary business tool trace and keeps `assistant.work_frame` as the final trace.

Focused verification:

```bash
PYTHONPATH=src .venv/bin/python -m py_compile \
  src/samchat/assistant/tool_registry.py \
  src/samchat/assistant/tool_adjudicator.py \
  src/samchat/assistant/assistant_workspace_trace.py \
  src/samchat/assistant/work_turn_renderer.py \
  src/samchat/assistant/conversation_service.py

PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/unit/test_assistant_tool_adjudicator_sufficiency.py \
  tests/unit/test_assistant_executive_answer_renderer.py \
  tests/unit/test_assistant_work_frame.py \
  tests/unit/test_assistant_request_router_integration.py \
  tests/unit/test_assistant_document_live_wiring.py \
  tests/unit/test_assistant_document_runtime_smoke.py \
  tests/unit/test_assistant_analyst_routing_integration.py
# 93 passed
```

Next slice: multi-candidate read-only execution for broad executive questions plus a stable executive-regression set.
