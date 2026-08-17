# RQF-SAMCHAT-ASSISTANT-052W ? Specialist task registry hardening

## Story

As SamChat gains more specialist capabilities, task discovery must be governed by a registry rather than scattered hardcoded lists, so the assistant can expose and evaluate only supported, versioned preview tasks.

## Problem

Specialist seed benchmarks exist in one module, while natural-language preview routing rules live inside the preview surface. That works for a small prototype, but it makes the assistant brittle: adding or retiring a specialist task requires changing unrelated code, and there is no compact product-facing inventory of enabled specialist previews.

## Goal

Introduce a deterministic specialist task registry with task metadata, route hints, status, version, and validation. Preview routing and task-id enumeration should consume this registry.

## Non-goals

- No new specialist agents.
- No new business benchmarks.
- No provider calls.
- No live DB dependency.
- No writes or authority changes.

## Acceptance criteria

- Registry exposes all 10 current specialist seed tasks.
- Task IDs are unique and match seed benchmarks.
- Route hints are deterministic and fail closed on ambiguity.
- Disabled/deprecated tasks are not routable.
- Preview surface uses the registry for task ids and natural routing.
- Tests cover registry inventory, route matching, ambiguity, and seed consistency.
