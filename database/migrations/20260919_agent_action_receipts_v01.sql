-- Owner-run migration for the disabled Agent Action API perimeter.
-- No startup helper may create or alter this table.
CREATE TABLE IF NOT EXISTS agent_action_receipts (
    id UUID PRIMARY KEY,
    action_id TEXT NOT NULL,
    action_version TEXT NOT NULL,
    actor_id TEXT NULL,
    tenant_id TEXT NULL,
    correlation_id TEXT NULL,
    normalized_redacted_inputs JSONB NOT NULL,
    policy_version TEXT NOT NULL,
    policy_envelope JSONB NOT NULL,
    evaluated_preconditions JSONB NOT NULL,
    decision TEXT NOT NULL,
    invoked_domain TEXT NULL,
    result TEXT NOT NULL,
    verifier TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NULL
);

CREATE INDEX IF NOT EXISTS ix_agent_action_receipts_correlation_id
    ON agent_action_receipts(correlation_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_action_receipts_mutation_idempotency
    ON agent_action_receipts(tenant_id, actor_id, action_id, action_version, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
