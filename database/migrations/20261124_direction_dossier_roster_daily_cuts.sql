-- Owner-run migration. The web role reads observations and never records them.
BEGIN;

CREATE TABLE IF NOT EXISTS direction_dossier_roster_daily_cuts (
    edition_id uuid NOT NULL REFERENCES copa_telmex_tournament_editions(id)
        ON DELETE RESTRICT,
    cut_date date NOT NULL,
    observed_at timestamptz NOT NULL,
    team_count integer NOT NULL CHECK (team_count BETWEEN 0 AND 25000),
    team_roster jsonb NOT NULL CHECK (jsonb_typeof(team_roster) = 'array'),
    PRIMARY KEY (edition_id, cut_date),
    CHECK (cut_date = (observed_at AT TIME ZONE 'America/Mexico_City')::date),
    CHECK (team_count = jsonb_array_length(team_roster))
);

COMMENT ON TABLE direction_dossier_roster_daily_cuts IS
    'First complete observed daily team roster for an exact CTT edition. No historical inference.';

REVOKE ALL ON direction_dossier_roster_daily_cuts FROM PUBLIC, devnous_user;
GRANT SELECT ON direction_dossier_roster_daily_cuts TO devnous_user;

COMMIT;
