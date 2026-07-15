# REG-003 SamChat fail-closed registration gate

This change is intentionally not self-enabling. Do not deploy until the Zaubern gate and Evidence Bus receipt issuer are reachable and REG-004 field geometry evidence has passed an operational sample.

Required SamChat configuration:

- `ZAUBERN_REGISTRATION_GATE_URL`: internal HTTPS URL for the Zaubern gate.
- `ZAUBERN_REGISTRATION_GATE_TIMEOUT_SECONDS`: hard request timeout; default `8`.
- `SAMCHAT_GOVERNANCE_TENANT_ID`: tenant binding; default `samchat-prod`.

Required Zaubern gate configuration:

- `SAMCHAT_IDENTITY_BINDING_KEY`: tenant-scoped secret, at least 32 bytes.
- `EVIDENCE_BUS_URL`: internal Evidence Bus URL.
- `EVIDENCE_HMAC_KEY`: existing Evidence Bus event-signing secret, at least 32 bytes.
- `EVIDENCE_RECEIPT_HMAC_KEY`: distinct receipt-signing secret, at least 32 bytes.

Required Evidence Bus configuration:

- `EVIDENCE_RECEIPT_HMAC_KEY`: same receipt key supplied to the gate.
- `EVIDENCE_RECEIPT_KID`: rotation identifier; default `evidence-bus-hs256-v1`.

Rollout order:

1. Back up PostgreSQL and apply `database/migrations/20260715_reg003_registration_governance.sql`.
2. Configure the Evidence Bus receipt key and deploy Evidence Bus.
3. Deploy the Zaubern registration gate and verify `/health` returns `ready`.
4. Run REG-004 operational samples. Missing field geometry must deny registration.
5. Configure SamChat and deploy it last.

Rollback order:

1. Remove SamChat's gate URL or roll SamChat back. This fails closed; it does not authorize bypass.
2. Keep the additive database columns. Do not drop receipt bindings during incident analysis.
3. Roll back gate and Evidence Bus only after SamChat no longer calls them.

Operational invariants:

- A missing, rejected, invalid, or unavailable EvidenceReceipt aborts the transaction.
- New player rows begin as `PENDING_FINALITY` and become `ACTIVE` only after a second verified receipt.
- Read paths exclude `PENDING_GOVERNANCE` and `PENDING_FINALITY` by default.
- Existing rows are migrated as `LEGACY_ACTIVE`; REG-003 does not retroactively attest them.
