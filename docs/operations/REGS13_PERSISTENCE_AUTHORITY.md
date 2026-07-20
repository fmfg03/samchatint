# REG-S13 transaction-scoped persistence authority

## Status

Implemented in an isolated SamChat worktree. Not deployed and not counted as an
operational authority closure.

## Boundary

`CopaTelmexDB` now treats Team and Player mutation as denied by default. A
caller must bind a `RegistrationPersistenceCapability` issued from the exact
Zaubern preauthorization response before the session can flush a Team or
Player mutation.

The capability is bound to:

- tenant;
- draft ID and version;
- team ID;
- roster decision ID and HMAC binding;
- preauthorization EvidenceReceipt ID;
- authorized roster slots;
- one database transaction.

It is invalidated after commit or rollback and cannot be rebound to another
session.

## Enforced behavior

- `create_team` denies without the exact team-bound capability.
- `create_player` requires `PENDING_FINALITY` and exact draft, decision,
  receipt, team, and roster-slot bindings.
- `update_team` is limited to the registration metadata fields used by the
  governed commit.
- generic Player updates and Team/Player deletion remain denied because
  REG-003 does not issue authority for those operations.
- a session-level `before_flush` listener catches direct ORM Team/Player writes
  made through a session owned by `CopaTelmexDB`.
- `PENDING_FINALITY` to `ACTIVE` requires an exact post-execution attestation
  and EvidenceReceipt bound to that Player and slot.

## Trust boundary

SamChat verifies the semantic event binding returned by the internal Zaubern
gate. It does not possess the Evidence Bus signing key and therefore does not
independently verify the receipt signature. Signature verification remains the
gate's responsibility; the capability prevents a valid response from being
reused for another transaction, team, draft, Player, or slot.

## Deliberate exclusions

- Raw SQL executed outside `CopaTelmexDB` remains outside REG-S13.
- Sessions that never construct `CopaTelmexDB` are not instrumented by this
  listener; those direct endpoints remain REG-S07.
- Supabase adapters remain REG-S12.
- No deletion or post-commit identity-edit capability is issued.
- This code is not present in the active runtime until an explicit rollout.

## Verification

```bash
pytest -q tests/unit/test_persistence_authority.py
pytest -q tests/unit/test_registration_governance.py \
  tests/unit/test_registration_review_audit.py \
  tests/unit/test_registration_review_incidents.py
```

Required negative cases cover missing capability, wrong slot, altered receipt,
cross-transaction reuse, post-commit reuse, missing finality, and direct ORM
flush.
