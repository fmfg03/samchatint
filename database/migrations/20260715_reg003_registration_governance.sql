BEGIN;

ALTER TABLE copa_telmex_registration_review_drafts
    ADD COLUMN IF NOT EXISTS draft_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE copa_telmex_players
    ADD COLUMN IF NOT EXISTS governance_state VARCHAR(30) NOT NULL DEFAULT 'LEGACY_ACTIVE',
    ADD COLUMN IF NOT EXISTS governance_draft_id VARCHAR(80),
    ADD COLUMN IF NOT EXISTS governance_draft_version INTEGER,
    ADD COLUMN IF NOT EXISTS governance_decision_id VARCHAR(80),
    ADD COLUMN IF NOT EXISTS roster_draft_binding VARCHAR(80),
    ADD COLUMN IF NOT EXISTS preauthorization_receipt_id VARCHAR(80),
    ADD COLUMN IF NOT EXISTS finality_receipt_id VARCHAR(80);

CREATE INDEX IF NOT EXISTS ix_copa_telmex_players_governance_state
    ON copa_telmex_players (governance_state);
CREATE INDEX IF NOT EXISTS ix_copa_telmex_players_governance_draft_id
    ON copa_telmex_players (governance_draft_id);
CREATE INDEX IF NOT EXISTS ix_copa_telmex_players_governance_decision_id
    ON copa_telmex_players (governance_decision_id);
CREATE INDEX IF NOT EXISTS ix_copa_telmex_players_roster_draft_binding
    ON copa_telmex_players (roster_draft_binding);
CREATE INDEX IF NOT EXISTS ix_copa_telmex_players_preauthorization_receipt_id
    ON copa_telmex_players (preauthorization_receipt_id);
CREATE INDEX IF NOT EXISTS ix_copa_telmex_players_finality_receipt_id
    ON copa_telmex_players (finality_receipt_id);

COMMIT;
