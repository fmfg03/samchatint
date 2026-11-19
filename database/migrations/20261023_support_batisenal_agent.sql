-- Explicit owner-run candidate, after 20261023_support_batisenal.sql.
-- No identities/grants, backfill, purge, startup DDL or external channel.
BEGIN;
CREATE TABLE support_batisenal_agent_drafts (
 id VARCHAR(36) PRIMARY KEY,
 ticket_id UUID NOT NULL REFERENCES support_batisenal_cases(ticket_id),
 agent_id VARCHAR(36) NOT NULL,
 employee VARCHAR(36) NOT NULL,
 grant_hash VARCHAR(64) NOT NULL,
 source_kind VARCHAR(24) NOT NULL CHECK (source_kind IN ('ai_agent','synthetic_stub')),
 request_key VARCHAR(80) NOT NULL,
 request_hash VARCHAR(64) NOT NULL,
 source_version VARCHAR(64) NOT NULL,
 body TEXT NOT NULL,
 response_json TEXT NOT NULL,
 attention VARCHAR(30) NOT NULL CHECK (attention IN ('escalated','awaiting_verification')),
 created_at TIMESTAMP WITH TIME ZONE NOT NULL,
 UNIQUE(ticket_id, agent_id, request_key)
);
CREATE TABLE support_batisenal_agent_publications (
 draft_id VARCHAR(36) PRIMARY KEY REFERENCES support_batisenal_agent_drafts(id),
 comment_id UUID NOT NULL UNIQUE REFERENCES support_ticket_comments(id),
 reviewer VARCHAR(36) NOT NULL,
 created_at TIMESTAMP WITH TIME ZONE NOT NULL
);
CREATE TRIGGER support_batisenal_agent_drafts_immutable
BEFORE UPDATE OR DELETE ON support_batisenal_agent_drafts
FOR EACH ROW EXECUTE FUNCTION support_batisenal_immutable();
CREATE TRIGGER support_batisenal_agent_publications_immutable
BEFORE UPDATE OR DELETE ON support_batisenal_agent_publications
FOR EACH ROW EXECUTE FUNCTION support_batisenal_immutable();
CREATE TABLE support_batisenal_agent_activity (
 id VARCHAR(36) PRIMARY KEY,
 ticket_id UUID NOT NULL REFERENCES support_batisenal_cases(ticket_id),
 agent_id VARCHAR(36) NOT NULL,
 employee VARCHAR(36) NOT NULL,
 grant_hash VARCHAR(64) NOT NULL,
 source_kind VARCHAR(24) NOT NULL CHECK (source_kind IN ('ai_agent','synthetic_stub')),
 request_key VARCHAR(80) NOT NULL,
 request_hash VARCHAR(64) NOT NULL,
 source_version VARCHAR(64) NOT NULL,
 kind VARCHAR(24) NOT NULL CHECK (kind IN ('started','source_unavailable')),
 created_at TIMESTAMP WITH TIME ZONE NOT NULL,
 UNIQUE(ticket_id, agent_id, request_key)
);
CREATE TRIGGER support_batisenal_agent_activity_immutable
BEFORE UPDATE OR DELETE ON support_batisenal_agent_activity
FOR EACH ROW EXECUTE FUNCTION support_batisenal_immutable();
COMMIT;
