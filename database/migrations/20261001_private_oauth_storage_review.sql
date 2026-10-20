-- REVIEW ONLY: owner-run migration; no startup DDL and no production execution.
-- Prerequisites: canonical empleados and 20260919_agent_action_receipts_v01.sql.
-- Single existing installation. organization_id is deployment namespace, not RFC.
-- Stores hashes of OAuth code/state/CSRF/browser values, never raw secrets.
BEGIN;


CREATE TABLE IF NOT EXISTS private_oauth_links (
	link_id VARCHAR(32) NOT NULL,
	issuer TEXT NOT NULL,
	subject VARCHAR(32) NOT NULL,
	installation_id TEXT NOT NULL,
	employee_id UUID NOT NULL,
	organization_id TEXT NOT NULL,
	profile_id VARCHAR(32) NOT NULL,
	active BOOLEAN NOT NULL,
	PRIMARY KEY (link_id),
	UNIQUE (issuer, installation_id, employee_id),
	UNIQUE (issuer, subject),
	UNIQUE (profile_id),
	FOREIGN KEY(employee_id) REFERENCES empleados (id)
)

;


CREATE TABLE IF NOT EXISTS private_oauth_pending (
	hash_id VARCHAR(64) NOT NULL,
	employee_id UUID NOT NULL,
	request_json JSONB NOT NULL,
	browser_hash VARCHAR(64) NOT NULL,
	csrf_hash VARCHAR(64) NOT NULL,
	expires_at BIGINT NOT NULL,
	used BOOLEAN NOT NULL,
	issuer TEXT NOT NULL,
	installation_id TEXT NOT NULL,
	organization_id TEXT NOT NULL,
	PRIMARY KEY (hash_id),
	FOREIGN KEY(employee_id) REFERENCES empleados (id)
)

;


CREATE TABLE IF NOT EXISTS private_oauth_codes (
	code_hash VARCHAR(64) NOT NULL,
	employee_id UUID NOT NULL,
	link_id VARCHAR(32) NOT NULL,
	request_json JSONB NOT NULL,
	link_snapshot JSONB NOT NULL,
	expires_at BIGINT NOT NULL,
	used BOOLEAN NOT NULL,
	issuer TEXT NOT NULL,
	installation_id TEXT NOT NULL,
	organization_id TEXT NOT NULL,
	PRIMARY KEY (code_hash),
	FOREIGN KEY(employee_id) REFERENCES empleados (id),
	FOREIGN KEY(link_id) REFERENCES private_oauth_links (link_id)
)

;


CREATE TABLE IF NOT EXISTS private_oauth_grants (
	grant_id TEXT NOT NULL,
	issuer TEXT NOT NULL,
	subject TEXT NOT NULL,
	token_id TEXT NOT NULL,
	client_id TEXT NOT NULL,
	installation_id TEXT NOT NULL,
	link_id VARCHAR(32) NOT NULL,
	scopes_json JSONB NOT NULL,
	expires_at BIGINT NOT NULL,
	active BOOLEAN NOT NULL,
	revoked BOOLEAN NOT NULL,
	employee_id UUID NOT NULL,
	organization_id TEXT NOT NULL,
	profile_id VARCHAR(32) NOT NULL,
	PRIMARY KEY (grant_id),
	UNIQUE (issuer, subject, token_id),
	FOREIGN KEY(link_id) REFERENCES private_oauth_links (link_id),
	FOREIGN KEY(employee_id) REFERENCES empleados (id)
)

;

COMMIT;
