-- Never discard finality evidence, source inputs or a configured database pin.
BEGIN;
LOCK TABLE ctt_staff_private.capture_finalities,ctt_staff_private.finality_inputs,
 ctt_staff_private.finality_database_identity,ctt_staff_private.finality_migration_baseline IN ACCESS EXCLUSIVE MODE;
DO $safe$
DECLARE item record;
BEGIN
 IF EXISTS(SELECT 1 FROM ctt_staff_private.capture_finalities)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.finality_inputs)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.finality_database_identity)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_results) THEN
 RAISE EXCEPTION 'CTT finality rollback refuses configured or evidentiary state'; END IF;
 FOR item IN SELECT * FROM ctt_staff_private.finality_migration_baseline LOOP
 IF item.installed_hash IS DISTINCT FROM md5(pg_get_functiondef(to_regprocedure(item.signature)))
 OR item.original_hash IS DISTINCT FROM md5(item.original_definition) THEN
 RAISE EXCEPTION 'CTT finality rollback source drift'; END IF;
 EXECUTE item.original_definition;
 END LOOP;
END
$safe$;
DROP TRIGGER ctt_capture_finality_result_initial ON ctt_staff_private.capture_results;
DROP TRIGGER ctt_capture_finality_result_commit ON ctt_staff_private.capture_results;
DROP TRIGGER ctt_capture_finality_consumption_commit ON ctt_staff_private.review_challenge_consumptions;
DROP TRIGGER ctt_capture_finality_proof_commit ON ctt_staff_private.capture_finalities;
DROP FUNCTION ctt_staff_private.check_capture_finality_transaction();
DROP FUNCTION ctt_staff_private.check_capture_finality_complete();
DROP FUNCTION ctt_staff_private.finality_update_authorized(text,jsonb,jsonb);
DROP FUNCTION ctt_staff_private.record_capture_finality(uuid,bytea,bytea);
DROP FUNCTION ctt_staff_private.validate_capture_finality_rows(uuid,boolean);
DROP FUNCTION ctt_staff_private.check_capture_finality_proof(uuid);
DROP FUNCTION ctt_staff_private.load_capture_finality_context(uuid);
DROP FUNCTION ctt_staff_private.load_capture_finality_inputs(uuid);
DROP FUNCTION ctt_staff_private.project_capture_finality_player(jsonb);
DROP FUNCTION ctt_staff_private.current_finality_writer();
DROP TABLE ctt_staff_private.capture_finalities;
DROP TABLE ctt_staff_private.finality_inputs;
DROP TABLE ctt_staff_private.finality_database_identity;
DROP TABLE ctt_staff_private.finality_migration_baseline;
COMMIT;
