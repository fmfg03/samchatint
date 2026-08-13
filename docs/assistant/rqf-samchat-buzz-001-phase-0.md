# RQF-SAMCHAT-BUZZ-001 ? Phase 0 decisions

Status: ROADMAP_ADDED
Source issue: #148
Scope: isolated multiagent collaboration spike for expense-report checking

## Decision summary

BUZZ-001 is a spike, not a production feature. It tests whether isolated specialist-agent collaboration improves expense-report analysis, and whether Buzz adds operational value as a replaceable collaboration projection.

## Closed Phase 0 decisions

1. **Evidence access** ? Agents receive derived evidence only. Raw documents stay in a deterministic Raw-Evidence Enclave for OCR, parsing, validation, redaction, hashing, and extraction. Agents receive normalized fields, permitted excerpts, source hashes, opaque evidence references, and confidence data.

2. **Proposal verification** ? The Proposal Verifier is deterministic. It validates schema, assignment identity, revocation, observed case version, exact evidence bindings, disclosure classification, forbidden fields, and absence of execution instructions or external destinations.

3. **B vs A thresholds** ? B must pass hard containment gates first. Then it must avoid material regressions in amount accuracy and classification precision, stay within cost/latency guardrails, and achieve measured improvements in discrepancy recall, escalation correctness, human correction time, or unsupported-claim reduction.

4. **C vs B thresholds** ? Buzz is justified only if C preserves semantic parity with B and materially improves lineage, reconstruction time, human handoff/review time, retryability, and operational coordination without containment regressions.

5. **Human room interaction** ? Rooms are collaborative, not authoritative. Humans may comment, ask questions, request reanalysis through a typed request, and add permitted context. They may not approve, execute, mutate canonical facts, upload raw documents, choose tools/models/sandboxes, or convert reactions into approvals.

6. **Sandbox runtime** ? The spike target is a rootless pod per assignment using gVisor/runsc, read-only root filesystem, tmpfs writable workspace, no service-account token, no host mounts, zero Linux capabilities, seccomp/AppArmor, and default-deny egress except typed broker APIs.

7. **Buzz retention/deletion** ? Buzz runs in an isolated experimental deployment with no raw documents and no production connection. Rooms are archived and memberships revoked at trial end. Retention is 14 days by default, hard maximum 30 days. Final deletion is proven by destroying Postgres volumes, Redis state, object storage, signing keys/capabilities, and issuing a wipe receipt.

## Open empirical questions

- Does B beat A under the approved thresholds?
- Does C beat B enough to justify Buzz as a replaceable collaboration dependency?

These questions are answered only by executing CaseBench; they are not spec assumptions.

## Non-authority boundary

During BUZZ-001, `execution_allowed` remains false. There are no production writes, no real payment or expense mutation, no approval semantics inside Buzz, no EffectCommand, and no use of real fiscal documents.
