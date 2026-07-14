-- RQF-SAMCHAT-ASSISTANT-040
-- Product-internal AnalystCase persistence. This migration creates only
-- Analyst Workbench case tables; it does not modify operational tables.

CREATE TABLE IF NOT EXISTS analyst_cases (
    case_id VARCHAR(80) PRIMARY KEY,
    user_id VARCHAR(120) NOT NULL,
    role VARCHAR(80) NOT NULL,
    question TEXT NOT NULL,
    analyst_intent JSON NOT NULL,
    status VARCHAR(40) NOT NULL,
    evidence JSON NOT NULL,
    current_answer TEXT NOT NULL,
    next_questions JSON NOT NULL,
    suggested_routes JSON NOT NULL,
    caveats JSON NOT NULL,
    writes_policy JSON NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(120) NULL,
    closed_at TIMESTAMP NULL,
    closed_by VARCHAR(120) NULL,
    CONSTRAINT check_analyst_cases_status
        CHECK (status IN ('open', 'waiting_context', 'analyzed', 'reviewed', 'closed'))
);

CREATE TABLE IF NOT EXISTS analyst_case_versions (
    version_id VARCHAR(96) PRIMARY KEY,
    case_id VARCHAR(80) NOT NULL,
    version_number INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(120) NOT NULL,
    status VARCHAR(40) NOT NULL,
    answer TEXT NOT NULL,
    evidence JSON NOT NULL,
    next_questions JSON NOT NULL,
    suggested_routes JSON NOT NULL,
    caveats JSON NOT NULL,
    answer_contract JSON NOT NULL,
    changed_fields JSON NOT NULL,
    CONSTRAINT fk_analyst_case_versions_case_id
        FOREIGN KEY (case_id) REFERENCES analyst_cases(case_id) ON DELETE CASCADE,
    CONSTRAINT ux_analyst_case_versions_case_version
        UNIQUE (case_id, version_number),
    CONSTRAINT check_analyst_case_versions_status
        CHECK (status IN ('open', 'waiting_context', 'analyzed', 'reviewed', 'closed'))
);

CREATE INDEX IF NOT EXISTS idx_analyst_cases_user_id
    ON analyst_cases (user_id);

CREATE INDEX IF NOT EXISTS idx_analyst_cases_status
    ON analyst_cases (status);

CREATE INDEX IF NOT EXISTS idx_analyst_cases_updated_at
    ON analyst_cases (updated_at);

CREATE INDEX IF NOT EXISTS idx_analyst_case_versions_case_id
    ON analyst_case_versions (case_id);
