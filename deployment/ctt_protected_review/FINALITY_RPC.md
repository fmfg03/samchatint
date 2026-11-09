# Finality gate RPC

The writer holds only public Gate/issuer/Bus verification keys and the existing
review/effect HMAC consumer keys. It must not receive the accredited Gate's
Ed25519 private key or identity-binding key. The Gate remains in the private
workflow process. These two RPCs replace the in-process Gate object at the
writer's existing `finalize_rows` and `authorize_finality_effect` calls.

`FinalityGateRPCServer` requires a concrete `StaffCaptureGateV2`, protected
`PostgresReviewProposalStore`, Gate-owned source CAS copy, fixed database identity
and explicit byte limits. `mount_finality_rpc` mounts only the two allowlisted
operations at `/v2/staff-review/finality/{operation}` on the private workflow
listener. It requires a separate writer-only channel key, the public-operations
channel key for separation checking, and durable `PostgresTransportNonces`.

`FinalityRPCClient` implements the existing synchronous methods; the writer
already invokes them through `asyncio.to_thread`. Configure an `httpx.Client`
with `trust_env=False`, no redirects and an explicit loopback URL. The constructor
also takes the existing consumer review/effect HMAC keys and public verification
registries. It never takes a signer private key. Close it on process shutdown.
No automatic retries occur after an uncertain response.

Every request binds method, fixed operation path, nonce, bounded timestamp and
exact canonical envelope bytes with a new HMAC domain. The response binds path,
nonce, request digest, HTTP status and exact bytes with another domain. The
nonce is consumed durably before invoking the Gate. Request/response limits are
8MiB; each embedded byte field is separately bounded. Duplicate/noncanonical JSON,
unknown keys, boolean/float integer substitutions and extra authority flags fail
closed. Errors contain no source data or credentials.

The wire carries exact scope/review/pending/preauthorization/signed-review bytes,
observed rows or the complete finality bundle/effect, and the trusted writer's
transaction attestation: database identity/OID/top XID, fixed issued/expires time
and finality nonce. It never carries `cas_succeeded`, blocking counts, a verified
flag, a callback or a serialized SourceSnapshot.

The Gate calls `load_review_finality_gate_context(attempt)` using its separately
configured SQL service. That owner function returns immutable private input bytes
and independently resolves current head, account/principal/faculty versions,
source identity, raw draft snapshot and DB identity/OID. It intentionally uses
MVCC reads without locks on writer-owned anchor/credential/session rows or the
capture advisory lock: the writer holds those locks while awaiting this RPC.
Using the normal full context/status loader here would deadlock.

The Gate matches every private input byte to the request, recounts incident flags
from protected validation, compares observed pending rows exactly, and reopens its
own sealed source copy. It constructs the in-process FinalityContext only from
these checks and the authenticated writer transaction attestation. The Gate
reuses the existing full preauthorization/proof verification and finality
producer. The client independently verifies real Ed25519 Bus receipts and Gate
activation capability; the existing writer consumer verifies them again against
its own live transaction/source before SQL activation and commit.

A finality receipt is a transaction-bound observation. A timeout may leave Bus
receipts for a rolled-back writer transaction; it never means the team committed.
The existing same-attempt recovery reads the official result, and a new writer
transaction receives a different transaction derivation. Existing SQL/HMAC/commit
guards reject replay of the previous transaction's activation capability.

Source roots remain owner-only: use separate exact CAS copies for workflow and
writer UIDs. No permission relaxation, roles, keys, source admission or process
startup occurs on import. Operational launchers/configuration are separate.
