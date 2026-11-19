-- Candidate only. Explicit owner-run migration, never startup DDL.
-- No grants, identity links, backfill or purge. A name collision fails closed.
BEGIN;

CREATE TABLE support_batisenal_cases (
	ticket_id UUID NOT NULL,
	organization VARCHAR(64) NOT NULL,
	project VARCHAR(64) NOT NULL,
	actor VARCHAR(36) NOT NULL,
	request_key VARCHAR(80) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	context_json TEXT NOT NULL,
	image BYTEA,
	image_hash VARCHAR(64),
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (ticket_id),
	UNIQUE (actor, organization, project, request_key),
	FOREIGN KEY(ticket_id) REFERENCES support_tickets (id)
)

;

CREATE TABLE support_batisenal_events (
	id VARCHAR(36) NOT NULL,
	ticket_id UUID NOT NULL,
	actor VARCHAR(36) NOT NULL,
	kind VARCHAR(24) NOT NULL,
	request_key VARCHAR(80) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	status VARCHAR(30) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (ticket_id, actor, request_key),
	FOREIGN KEY(ticket_id) REFERENCES support_batisenal_cases (ticket_id)
)

;
CREATE INDEX ix_support_batisenal_events_ticket_id ON support_batisenal_events (ticket_id);

CREATE INDEX ix_support_batisenal_scope ON support_batisenal_cases (organization, project, actor);
CREATE FUNCTION support_batisenal_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'BATISENAL_IMMUTABLE_EVIDENCE';
END;
$$;
CREATE TRIGGER support_batisenal_cases_immutable
BEFORE UPDATE OR DELETE ON support_batisenal_cases
FOR EACH ROW EXECUTE FUNCTION support_batisenal_immutable();
CREATE TRIGGER support_batisenal_events_immutable
BEFORE UPDATE OR DELETE ON support_batisenal_events
FOR EACH ROW EXECUTE FUNCTION support_batisenal_immutable();
COMMIT;
