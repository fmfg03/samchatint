-- Owner-only rollback; never remove inventory admission, spend or observations.
BEGIN;
DO $empty$
DECLARE name text;n bigint;
BEGIN
 FOREACH name IN ARRAY ARRAY['review_source_inventories','review_source_api_budgets','review_source_preparations','review_source_budget_run_bindings'] LOOP
 EXECUTE format('SELECT count(*) FROM ctt_staff_private.%I',name) INTO n;
 IF n<>0 THEN RAISE EXCEPTION 'CTT source preparation rollback retains evidence in %',name;END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_budget_reservations WHERE preparation_id IS NOT NULL OR budget_id IS NOT NULL) THEN
 RAISE EXCEPTION 'CTT source preparation rollback retains reservations';END IF;
END $empty$;
DO $restore$
DECLARE row record;
BEGIN
 FOR row IN SELECT function_definition FROM ctt_staff_private.review_source_preparation_installation LOOP EXECUTE row.function_definition;END LOOP;
END $restore$;
DROP TRIGGER source_reservation_immutable ON ctt_staff_private.review_batch_budget_reservations;
ALTER TABLE ctt_staff_private.review_batch_budget_reservations DROP CONSTRAINT source_budget_exact_stage,DROP CONSTRAINT source_budget_prep_fk,DROP CONSTRAINT source_budget_run_fk,DROP COLUMN preparation_id,DROP COLUMN budget_id,ALTER COLUMN run_id SET NOT NULL;
DO $functions$
DECLARE row record;
BEGIN
 FOR row IN SELECT p.oid::regprocedure signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ctt_staff_private' AND (p.proname LIKE 'source_preparation_%' OR p.proname LIKE 'source_budget_%') LOOP EXECUTE format('DROP FUNCTION %s CASCADE',row.signature);END LOOP;
END $functions$;
DROP TABLE ctt_staff_private.review_source_budget_run_bindings;
DROP TABLE ctt_staff_private.review_source_preparations;
DROP TABLE ctt_staff_private.review_source_api_budgets;
DROP TABLE ctt_staff_private.review_source_inventories;
DROP TABLE ctt_staff_private.review_source_preparation_installation;
COMMIT;
