-- Explicit rollback: export configured catalog rows before owner approval.
-- RESTRICT prevents silently removing subsequently installed dependencies.
BEGIN;
DROP TABLE IF EXISTS copa_telmex_tournament_editions RESTRICT;
COMMIT;
