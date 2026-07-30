# RQF-SAMCHAT-ASSISTANT-009J - Live Owner-Needs Canary

Status: CANARY_RAN_PARTIAL_PASS
Date: 2026-07-30
Release: `/srv/samchat/releases/gastos-prod-7c4055764-assistant009j`
Commit: `7c4055764`
Mode: `calidad`
Actor: Francisco Fernandez allowlisted canary actor
Writes enabled: false
Runtime readonly-only: true

## Health

Before the final canary run:

- `/healthz`: healthy
- `/readyz`: healthy
- systemd WorkingDirectory: `/srv/samchat/releases/gastos-prod-7c4055764-assistant009j`

## Final canary run

Artifact:

- `artifacts/rqf-009j-owner-canary-20260730T162045Z.json`

Summary:

| Metric | Value |
| --- | ---: |
| Prompts | 10 |
| PASS | 9 |
| FAIL | 1 |
| HTTP errors | 0 |
| Pending confirmations | 0 |
| Writes attempted | 0 |
| Invalid tournament snapshot dependencies | 0 |

## Prompt results

| ID | Result | Notes |
| --- | --- | --- |
| AI-OWNER-002 | PASS | Entity-folder canon question routed RAG-only with owner canon. |
| AI-OWNER-014 | PASS | National-phase canon question routed RAG-only with owner canon. |
| AI-OWNER-026 | PASS | Missing-data behavior question routed RAG-only. |
| AI-OWNER-027 | PASS | `sin cambiar datos` stayed read-only/RAG-only. |
| AI-OWNER-030 | PASS | Capability/gap question routed RAG-only. |
| AI-OWNER-007 | PASS | No invalid tournament-id error; handled as owner-needs context. |
| AI-OWNER-015 | PASS | No invalid tournament-id error; handled as owner-needs context. |
| AI-OWNER-018 | FAIL | Safe routing, no writes, no HTTP error; answer still gave a generic definition of medical services/accidents instead of explicitly saying concrete evidence was unavailable. |
| AI-OWNER-023 | PASS | No hallucinated provider after `009J`. |
| AI-OWNER-024 | PASS | Missing photographic evidence handled as unavailable context. |

## Improvement across cuts

| Cut | Result |
| --- | --- |
| Initial live smoke | Failed routing: `operations.tournament_soul_snapshot` for conceptual owner question. |
| 009H canary | 4/10 PASS. |
| 009I canary | 6/10 PASS. |
| 009J canary | 9/10 PASS. |

## Claim boundary

Established in live canary:

- Owner-needs prompts no longer trigger writes.
- Conceptual owner-needs prompts no longer bypass into deterministic tournament snapshots.
- Owner-needs read-only prompts no longer produce invalid `tournament_id` HTTP errors.
- Canon-only retrieval avoids SQL and memory contamination.
- The assistant distinguishes canon from live evidence in 9 of 10 sampled prompts.

Not yet established:

- Full 30-prompt eval pass.
- Dedicated owner-folder tools for live evidence retrieval across operations, finance, documents, media, and marketing.
- Perfect factual abstention with the current local model (`ollama/qwen3:1.7b`) for all owner-needs prompts.

## Next recommended slice

RQF-SAMCHAT-ASSISTANT-009K should implement deterministic owner-needs evidence-gap responses or a higher-quality provider route for canon-only factual prompts. The specific regression target is AI-OWNER-018: medical services and accidents with transfer must not be answered as generic facts when no live evidence is retrieved.
