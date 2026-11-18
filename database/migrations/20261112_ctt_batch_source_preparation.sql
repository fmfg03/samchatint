-- Approved CTT source observation and shared API budget; no identities seeded.
BEGIN;
DO $dependencies$
DECLARE owner_id oid;
BEGIN
 SELECT oid INTO owner_id FROM pg_roles WHERE rolname='samchat_ctt_staff_owner'
 AND NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
 AND NOT rolreplication AND NOT rolbypassrls;
 IF owner_id IS NULL OR EXISTS(SELECT 1 FROM pg_auth_members WHERE roleid=owner_id OR member=owner_id)
 OR NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='ctt_staff_private' AND nspowner=owner_id)
 OR to_regprocedure('ctt_staff_private.batch_reserve_budget(uuid,uuid,bigint)') IS NULL
 OR to_regprocedure('ctt_staff_private.batch_reconcile_budget(uuid,bigint,text)') IS NULL
 OR to_regclass('ctt_staff_private.review_batch_services') IS NULL
 OR current_setting('max_prepared_transactions')::integer<>0 THEN
 RAISE EXCEPTION 'CTT source preparation dependency unavailable'; END IF;
END $dependencies$;

-- Rollback retains the exact previous function bodies, rather than guessing them.
CREATE TABLE ctt_staff_private.review_source_preparation_installation(
 signature text PRIMARY KEY, function_definition text NOT NULL
);
INSERT INTO ctt_staff_private.review_source_preparation_installation
 SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='ctt_staff_private'
 AND p.proname IN('batch_reserve_budget','batch_reconcile_budget');

CREATE TABLE ctt_staff_private.review_source_inventories(
 inventory_sha256 text PRIMARY KEY CHECK(inventory_sha256~'^[0-9a-f]{64}$'),
 inventory_bytes bytea NOT NULL, inventory_json jsonb NOT NULL,
 tenant_id text NOT NULL, edition_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE ctt_staff_private.review_source_api_budgets(
 budget_id uuid PRIMARY KEY, backend_pin text NOT NULL,
 account_id uuid NOT NULL, tenant_id text NOT NULL, edition_id uuid NOT NULL,
 quote_sha256 text NOT NULL CHECK(quote_sha256~'^[0-9a-f]{64}$'),
 quote_bytes bytea NOT NULL, quote_json jsonb NOT NULL,
 budget_bytes bytea NOT NULL, budget_sha256 text NOT NULL,
 maximum_units bigint NOT NULL CHECK(maximum_units>0),
 valid_from timestamptz NOT NULL, valid_until timestamptz NOT NULL,
 approval_ref text NOT NULL UNIQUE CHECK(approval_ref~'^sha256:[0-9a-f]{64}$'),
 revoked_at timestamptz,
 CHECK(isfinite(valid_from) AND isfinite(valid_until) AND valid_until>valid_from)
);
CREATE TABLE ctt_staff_private.review_source_preparations(
 preparation_id uuid PRIMARY KEY, backend_pin text NOT NULL,
 inventory_sha256 text NOT NULL REFERENCES ctt_staff_private.review_source_inventories,
 profile_hash text NOT NULL CHECK(profile_hash~'^[0-9a-f]{64}$'),
 profile_bytes bytea NOT NULL, profile_json jsonb NOT NULL,
 budget_id uuid NOT NULL REFERENCES ctt_staff_private.review_source_api_budgets,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN('pending','completed')),
 observations_bytes bytea, observations_sha256 text,
 report_bytes bytea, report_sha256 text,
 revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(preparation_id,budget_id),
 CHECK((status='pending' AND observations_bytes IS NULL AND report_bytes IS NULL
 AND observations_sha256 IS NULL AND report_sha256 IS NULL)
 OR (status='completed' AND observations_bytes IS NOT NULL AND report_bytes IS NOT NULL
 AND observations_sha256~'^[0-9a-f]{64}$' AND report_sha256~'^[0-9a-f]{64}$'))
);
CREATE TABLE ctt_staff_private.review_source_budget_run_bindings(
 run_id uuid PRIMARY KEY REFERENCES ctt_staff_private.review_batch_runs,
 preparation_id uuid NOT NULL REFERENCES ctt_staff_private.review_source_preparations,
 budget_id uuid NOT NULL REFERENCES ctt_staff_private.review_source_api_budgets,
 field_profile_bytes bytea NOT NULL CHECK(octet_length(field_profile_bytes) BETWEEN 2 AND 16777216),
 field_profile_hash text NOT NULL CHECK(field_profile_hash=encode(public.digest(field_profile_bytes,'sha256'),'hex')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(run_id,budget_id),
 FOREIGN KEY(preparation_id,budget_id) REFERENCES ctt_staff_private.review_source_preparations(preparation_id,budget_id)
);
ALTER TABLE ctt_staff_private.review_batch_budget_reservations
 ALTER COLUMN run_id DROP NOT NULL,
 ADD COLUMN preparation_id uuid REFERENCES ctt_staff_private.review_source_preparations,
 ADD COLUMN budget_id uuid REFERENCES ctt_staff_private.review_source_api_budgets,
 ADD CONSTRAINT source_budget_exact_stage CHECK(num_nonnulls(run_id,preparation_id)=1 AND (preparation_id IS NULL OR budget_id IS NOT NULL)),
 ADD CONSTRAINT source_budget_prep_fk FOREIGN KEY(preparation_id,budget_id) REFERENCES ctt_staff_private.review_source_preparations(preparation_id,budget_id),
 ADD CONSTRAINT source_budget_run_fk FOREIGN KEY(run_id,budget_id) REFERENCES ctt_staff_private.review_source_budget_run_bindings(run_id,budget_id);
CREATE INDEX source_budget_reservation_sum ON ctt_staff_private.review_batch_budget_reservations(budget_id);

CREATE FUNCTION ctt_staff_private.source_preparation_integer(p_value jsonb,p_key text,p_min bigint,p_max bigint)
RETURNS bigint LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE value numeric;
BEGIN
 IF jsonb_typeof(p_value->p_key) IS DISTINCT FROM 'number' OR (p_value->>p_key)!~'^[0-9]+$' THEN
 RAISE EXCEPTION 'CTT source preparation integer denied' USING ERRCODE='42501'; END IF;
 value:=(p_value->>p_key)::numeric;
 IF value<p_min OR value>p_max THEN RAISE EXCEPTION 'CTT source preparation integer denied' USING ERRCODE='42501';END IF;
 RETURN value::bigint;
END $fn$;

CREATE FUNCTION ctt_staff_private.source_preparation_inventory_check(p_inventory jsonb)
RETURNS void LANGUAGE plpgsql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE pdf jsonb;page jsonb;render jsonb;edition jsonb;index_page integer:=0;n integer;count_pages integer;total_bytes bigint:=0;pixels bigint;
BEGIN
 PERFORM ctt_staff_private.batch_require_keys(p_inventory,ARRAY['schema','edition','render_profile','pdfs','pages']);
 edition:=p_inventory->'edition';render:=p_inventory->'render_profile';
 PERFORM ctt_staff_private.batch_require_keys(edition,ARRAY['tenant_id','edition_id','roster_slug']);
 PERFORM ctt_staff_private.batch_require_keys(render,ARRAY['version','dpi','color','max_page_pixels','max_render_bytes']);
 IF p_inventory->>'schema' IS DISTINCT FROM 'ctt.batch.source-inventory.v1'
 OR jsonb_typeof(edition->'tenant_id') IS DISTINCT FROM 'string' OR length(btrim(edition->>'tenant_id')) NOT BETWEEN 1 AND 128
 OR (edition->>'edition_id')::uuid::text IS DISTINCT FROM edition->>'edition_id'
 OR jsonb_typeof(edition->'roster_slug') IS DISTINCT FROM 'string' OR length(btrim(edition->>'roster_slug')) NOT BETWEEN 1 AND 128
 OR jsonb_typeof(render->'version') IS DISTINCT FROM 'string' OR length(render->>'version') NOT BETWEEN 1 AND 128
 OR render->>'color' IS DISTINCT FROM 'RGB'
 OR jsonb_typeof(p_inventory->'pdfs') IS DISTINCT FROM 'array' OR jsonb_array_length(p_inventory->'pdfs') NOT BETWEEN 1 AND 16
 OR jsonb_typeof(p_inventory->'pages') IS DISTINCT FROM 'array' OR jsonb_array_length(p_inventory->'pages') NOT BETWEEN 1 AND 1200 THEN
 RAISE EXCEPTION 'CTT source inventory denied' USING ERRCODE='42501';END IF;
 PERFORM ctt_staff_private.source_preparation_integer(render,'dpi',72,300);
 pixels:=ctt_staff_private.source_preparation_integer(render,'max_page_pixels',1,25000000);
 PERFORM ctt_staff_private.source_preparation_integer(render,'max_render_bytes',1,33554432);
 IF (SELECT count(DISTINCT value->>'sha256') FROM jsonb_array_elements(p_inventory->'pdfs'))<>jsonb_array_length(p_inventory->'pdfs')
 OR (SELECT count(DISTINCT value->>'filename') FROM jsonb_array_elements(p_inventory->'pdfs'))<>jsonb_array_length(p_inventory->'pdfs') THEN
 RAISE EXCEPTION 'CTT duplicate source inventory denied' USING ERRCODE='42501'; END IF;
 FOR pdf IN SELECT value FROM jsonb_array_elements(p_inventory->'pdfs') LOOP
 PERFORM ctt_staff_private.batch_require_keys(pdf,ARRAY['filename','sha256','bytes','page_count']);
 IF jsonb_typeof(pdf->'filename') IS DISTINCT FROM 'string' OR length(pdf->>'filename') NOT BETWEEN 5 AND 255
 OR (pdf->>'filename')!~*'\.pdf$' OR position('/' IN pdf->>'filename')>0 OR position(chr(92) IN pdf->>'filename')>0
 OR (pdf->>'filename')~'[[:cntrl:]]' OR jsonb_typeof(pdf->'sha256') IS DISTINCT FROM 'string' OR (pdf->>'sha256')!~'^[0-9a-f]{64}$' THEN
 RAISE EXCEPTION 'CTT source PDF metadata denied' USING ERRCODE='42501'; END IF;
 total_bytes:=total_bytes+ctt_staff_private.source_preparation_integer(pdf,'bytes',1,1073741824);
 count_pages:=ctt_staff_private.source_preparation_integer(pdf,'page_count',1,1200)::integer;
 FOR n IN 1..count_pages LOOP
 page:=p_inventory->'pages'->index_page;
 PERFORM ctt_staff_private.batch_require_keys(page,ARRAY['pdf_sha256','pdf_page','render_sha256','width','height']);
 IF page->>'pdf_sha256' IS DISTINCT FROM pdf->>'sha256' OR ctt_staff_private.source_preparation_integer(page,'pdf_page',1,1200)<>n
 OR jsonb_typeof(page->'render_sha256') IS DISTINCT FROM 'string' OR (page->>'render_sha256')!~'^[0-9a-f]{64}$'
 OR ctt_staff_private.source_preparation_integer(page,'width',1,25000000)*ctt_staff_private.source_preparation_integer(page,'height',1,25000000)>pixels THEN
 RAISE EXCEPTION 'CTT source page census denied' USING ERRCODE='42501'; END IF;
 index_page:=index_page+1;
 END LOOP;
 END LOOP;
 IF total_bytes>1073741824 OR index_page<>jsonb_array_length(p_inventory->'pages') THEN
 RAISE EXCEPTION 'CTT source inventory limits denied' USING ERRCODE='42501'; END IF;
END $fn$;

CREATE FUNCTION ctt_staff_private.source_preparation_admit(p_id uuid,p_inventory bytea,p_profile bytea,p_quote bytea,p_budget bytea,p_approval text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;i jsonb;p jsonb;q jsonb;b jsonb;provider jsonb;
 ih text;ph text;qh text;bh text;bid uuid;maximum bigint;start_time timestamptz;end_time timestamptz;
 oldp ctt_staff_private.review_source_preparations%ROWTYPE;oldb ctt_staff_private.review_source_api_budgets%ROWTYPE;worst numeric;
BEGIN
 svc:=ctt_staff_private.batch_require_service('operator');
 i:=ctt_staff_private.batch_json(p_inventory);p:=ctt_staff_private.batch_json(p_profile);
 q:=ctt_staff_private.batch_json(p_quote);b:=ctt_staff_private.batch_json(p_budget);
 PERFORM ctt_staff_private.source_preparation_inventory_check(i);
 PERFORM ctt_staff_private.batch_require_keys(p,ARRAY['schema','provider','templates','segmentation']);
 PERFORM ctt_staff_private.batch_require_keys(q,ARRAY['revision','model_revision','currency','unit','input_per_million_micro_usd','cached_input_per_million_micro_usd','output_per_million_micro_usd','model_input_token_limit']);
 PERFORM ctt_staff_private.batch_require_keys(b,ARRAY['schema','budget_id','tenant_id','edition_id','account_id','quote_sha256','maximum_units','valid_from','valid_until']);
 provider:=p->'provider';
 IF p_id IS NULL OR p_approval IS NULL OR p_approval!~'^sha256:[0-9a-f]{64}$'
 OR p->>'schema' IS DISTINCT FROM 'ctt.batch.structural-profile.v1'
 OR jsonb_typeof(p->'templates') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'templates') NOT BETWEEN 1 AND 32
 OR jsonb_typeof(p->'segmentation') IS DISTINCT FROM 'object'
 OR provider->>'name' IS DISTINCT FROM 'openai' OR provider->>'pipeline_version' IS DISTINCT FROM 'openai.ctt.structural-pages.v1'
 OR provider->'pricing' IS DISTINCT FROM q OR provider->>'model_revision' IS DISTINCT FROM q->>'model_revision'
 OR provider->>'pricing_revision' IS DISTINCT FROM q->>'revision'
 OR jsonb_typeof(q->'revision') IS DISTINCT FROM 'string' OR jsonb_typeof(q->'model_revision') IS DISTINCT FROM 'string' OR length(q->>'revision') NOT BETWEEN 1 AND 128
 OR (q->>'model_revision')!~'^.+-[0-9]{4}-[0-9]{2}-[0-9]{2}$'
 OR q->>'currency' IS DISTINCT FROM 'USD' OR q->>'unit' IS DISTINCT FROM 'micro_usd'
 OR b->>'schema' IS DISTINCT FROM 'ctt.batch.shared-api-budget.v1'
 OR b->>'tenant_id' IS DISTINCT FROM i->'edition'->>'tenant_id'
 OR b->>'edition_id' IS DISTINCT FROM i->'edition'->>'edition_id' THEN
 RAISE EXCEPTION 'CTT source preparation admission denied' USING ERRCODE='42501'; END IF;
 PERFORM right(q->>'model_revision',10)::date;
 PERFORM ctt_staff_private.source_preparation_integer(q,'input_per_million_micro_usd',1,9223372036854775807);
 PERFORM ctt_staff_private.source_preparation_integer(q,'cached_input_per_million_micro_usd',0,(q->>'input_per_million_micro_usd')::bigint);
 PERFORM ctt_staff_private.source_preparation_integer(q,'output_per_million_micro_usd',1,9223372036854775807);
 PERFORM ctt_staff_private.source_preparation_integer(q,'model_input_token_limit',1,2000000);
 PERFORM ctt_staff_private.source_preparation_integer(provider,'batch_size',1,16);
 PERFORM ctt_staff_private.source_preparation_integer(provider,'max_output_tokens',256,16000);
 PERFORM ctt_staff_private.source_preparation_integer(provider,'max_request_bytes',1024,16777216);
 PERFORM ctt_staff_private.source_preparation_integer(provider,'max_analysis_bytes',1024,67108864);
 PERFORM ctt_staff_private.source_preparation_integer(provider,'maximum_call_units',1,100000000);
 IF jsonb_typeof(provider->'timeout_seconds') IS DISTINCT FROM 'number'
 OR (provider->>'timeout_seconds')::numeric NOT BETWEEN 0.001 AND 120 THEN
 RAISE EXCEPTION 'CTT source preparation timeout denied' USING ERRCODE='42501'; END IF;
 worst:=ceil(((q->>'model_input_token_limit')::numeric*(q->>'input_per_million_micro_usd')::numeric
 +(provider->>'max_output_tokens')::numeric*(q->>'output_per_million_micro_usd')::numeric)/1000000);
 IF (provider->>'maximum_call_units')::numeric<worst THEN RAISE EXCEPTION 'CTT source preparation reservation bound denied' USING ERRCODE='42501';END IF;
 ih:=encode(public.digest(p_inventory,'sha256'),'hex');ph:=encode(public.digest(p_profile,'sha256'),'hex');
 qh:=encode(public.digest(p_quote,'sha256'),'hex');bh:=encode(public.digest(p_budget,'sha256'),'hex');
 IF b->>'quote_sha256' IS DISTINCT FROM qh THEN RAISE EXCEPTION 'CTT source quote mismatch' USING ERRCODE='42501';END IF;
 bid:=(b->>'budget_id')::uuid;PERFORM (b->>'account_id')::uuid;
 maximum:=ctt_staff_private.source_preparation_integer(b,'maximum_units',1,9223372036854775807);
 start_time:=(b->>'valid_from')::timestamptz;end_time:=(b->>'valid_until')::timestamptz;
 IF bid IS NULL OR NOT isfinite(start_time) OR NOT isfinite(end_time) OR end_time<=start_time THEN
 RAISE EXCEPTION 'CTT source budget validity denied' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('ctt-source-budget:'||bid::text,0));
 SELECT * INTO oldp FROM ctt_staff_private.review_source_preparations WHERE preparation_id=p_id FOR UPDATE;
 SELECT * INTO oldb FROM ctt_staff_private.review_source_api_budgets WHERE budget_id=bid FOR UPDATE;
 IF oldb.budget_id IS NOT NULL THEN
 IF oldb.backend_pin<>svc.backend_pin OR oldb.budget_bytes<>p_budget OR oldb.quote_bytes<>p_quote OR oldb.approval_ref<>p_approval OR oldb.revoked_at IS NOT NULL THEN
 RAISE EXCEPTION 'CTT source budget replay changed' USING ERRCODE='40001';END IF;
 ELSE
 INSERT INTO ctt_staff_private.review_source_api_budgets(budget_id,backend_pin,account_id,tenant_id,edition_id,quote_sha256,quote_bytes,quote_json,budget_bytes,budget_sha256,maximum_units,valid_from,valid_until,approval_ref)
 VALUES(bid,svc.backend_pin,(b->>'account_id')::uuid,b->>'tenant_id',(b->>'edition_id')::uuid,qh,p_quote,q,p_budget,bh,maximum,start_time,end_time,p_approval);
 END IF;
 INSERT INTO ctt_staff_private.review_source_inventories(inventory_sha256,inventory_bytes,inventory_json,tenant_id,edition_id)
 VALUES(ih,p_inventory,i,i->'edition'->>'tenant_id',(i->'edition'->>'edition_id')::uuid) ON CONFLICT(inventory_sha256) DO NOTHING;
 IF (SELECT inventory_bytes FROM ctt_staff_private.review_source_inventories WHERE inventory_sha256=ih) IS DISTINCT FROM p_inventory THEN
 RAISE EXCEPTION 'CTT source inventory identity changed' USING ERRCODE='40001';END IF;

 IF oldp.preparation_id IS NOT NULL THEN
 IF oldp.backend_pin<>svc.backend_pin OR oldp.inventory_sha256<>ih OR oldp.profile_bytes<>p_profile OR oldp.budget_id<>bid OR oldp.revoked_at IS NOT NULL THEN
 RAISE EXCEPTION 'CTT source preparation replay changed' USING ERRCODE='40001';END IF;
 ELSE
 INSERT INTO ctt_staff_private.review_source_preparations(preparation_id,backend_pin,inventory_sha256,profile_hash,profile_bytes,profile_json,budget_id)
 VALUES(p_id,svc.backend_pin,ih,ph,p_profile,p,bid);
 END IF;
 RETURN jsonb_build_object('preparation_id',p_id,'inventory_sha256',ih,'profile_hash',ph,'quote_sha256',qh,'budget_id',bid,'maximum_units',maximum,'backend_pin',svc.backend_pin,'status',coalesce(oldp.status,'pending'));
END $fn$;

CREATE FUNCTION ctt_staff_private.source_preparation_context(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;p ctt_staff_private.review_source_preparations%ROWTYPE;b ctt_staff_private.review_source_api_budgets%ROWTYPE;i ctt_staff_private.review_source_inventories%ROWTYPE;used bigint;
BEGIN
 svc:=ctt_staff_private.batch_require_service('verifier');
 SELECT * INTO p FROM ctt_staff_private.review_source_preparations WHERE preparation_id=p_id FOR SHARE;
 SELECT * INTO b FROM ctt_staff_private.review_source_api_budgets WHERE budget_id=p.budget_id;
 SELECT * INTO i FROM ctt_staff_private.review_source_inventories WHERE inventory_sha256=p.inventory_sha256;
 IF p.preparation_id IS NULL OR p.backend_pin IS DISTINCT FROM svc.backend_pin OR b.backend_pin IS DISTINCT FROM svc.backend_pin
 OR p.revoked_at IS NOT NULL OR b.revoked_at IS NOT NULL OR clock_timestamp()<b.valid_from OR clock_timestamp()>=b.valid_until
 OR encode(public.digest(i.inventory_bytes,'sha256'),'hex') IS DISTINCT FROM p.inventory_sha256
 OR encode(public.digest(p.profile_bytes,'sha256'),'hex') IS DISTINCT FROM p.profile_hash
 OR encode(public.digest(b.quote_bytes,'sha256'),'hex') IS DISTINCT FROM b.quote_sha256 THEN
 RAISE EXCEPTION 'CTT source preparation unavailable' USING ERRCODE='42501';END IF;
 SELECT coalesce(sum(coalesce(actual_units,maximum_units)),0) INTO used FROM ctt_staff_private.review_batch_budget_reservations WHERE budget_id=b.budget_id;
 RETURN jsonb_build_object('preparation_id',p_id,'inventory_sha256',p.inventory_sha256,'profile_hash',p.profile_hash,'quote_sha256',b.quote_sha256,'budget_id',b.budget_id,
 'inventory',i.inventory_json,'profile',p.profile_json,'quote',b.quote_json,'status',p.status,'backend_pin',svc.backend_pin,
 'maximum_units',b.maximum_units,'used_units',used,'remaining_units',b.maximum_units-used,'valid_until',b.valid_until,
 'observations_sha256',p.observations_sha256,'report_sha256',p.report_sha256,
 'observations',CASE WHEN p.observations_bytes IS NULL THEN NULL ELSE convert_from(p.observations_bytes,'UTF8')::jsonb END,
 'report',CASE WHEN p.report_bytes IS NULL THEN NULL ELSE convert_from(p.report_bytes,'UTF8')::jsonb END);
END $fn$;

CREATE FUNCTION ctt_staff_private.source_preparation_budget_report(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;p ctt_staff_private.review_source_preparations%ROWTYPE;b ctt_staff_private.review_source_api_budgets%ROWTYPE;charged bigint;held bigint;
BEGIN
 svc:=ctt_staff_private.batch_require_service('verifier');
 SELECT * INTO p FROM ctt_staff_private.review_source_preparations WHERE preparation_id=p_id;
 SELECT * INTO b FROM ctt_staff_private.review_source_api_budgets WHERE budget_id=p.budget_id;
 IF p.preparation_id IS NULL OR p.backend_pin IS DISTINCT FROM svc.backend_pin OR b.backend_pin IS DISTINCT FROM svc.backend_pin THEN
 RAISE EXCEPTION 'CTT source consumption report denied' USING ERRCODE='42501';END IF;
 SELECT coalesce(sum(actual_units) FILTER(WHERE actual_units IS NOT NULL),0),
 coalesce(sum(maximum_units) FILTER(WHERE actual_units IS NULL),0)
 INTO charged,held FROM ctt_staff_private.review_batch_budget_reservations WHERE budget_id=b.budget_id;
 RETURN jsonb_build_object('budget_id',b.budget_id,'maximum_units',b.maximum_units,
 'charged_units',charged,'held_units',held,'remaining_units',b.maximum_units-charged-held);
END $fn$;

CREATE FUNCTION ctt_staff_private.source_preparation_reserve(p_prep uuid,p_id uuid,p_units bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE ctx jsonb;p ctt_staff_private.review_source_preparations%ROWTYPE;b ctt_staff_private.review_source_api_budgets%ROWTYPE;old ctt_staff_private.review_batch_budget_reservations%ROWTYPE;used bigint;
BEGIN
 ctx:=ctt_staff_private.source_preparation_context(p_prep);
 SELECT * INTO p FROM ctt_staff_private.review_source_preparations WHERE preparation_id=p_prep FOR SHARE;
 SELECT * INTO b FROM ctt_staff_private.review_source_api_budgets WHERE budget_id=p.budget_id FOR UPDATE;
 IF p.status='completed' THEN RAISE EXCEPTION 'CTT source preparation already completed' USING ERRCODE='55000';END IF;
 IF p.status<>'pending' OR p_id IS NULL OR p_units IS NULL OR p_units<=0
 OR p_units<(p.profile_json->'provider'->>'maximum_call_units')::bigint
 OR p.revoked_at IS NOT NULL OR b.revoked_at IS NOT NULL OR clock_timestamp()<b.valid_from OR clock_timestamp()>=b.valid_until THEN
 RAISE EXCEPTION 'CTT source reservation denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO old FROM ctt_staff_private.review_batch_budget_reservations WHERE reservation_id=p_id;
 IF old.reservation_id IS NOT NULL THEN
 IF old.preparation_id IS DISTINCT FROM p_prep OR old.budget_id IS DISTINCT FROM b.budget_id OR old.maximum_units<>p_units THEN
 RAISE EXCEPTION 'CTT source reservation changed' USING ERRCODE='40001';END IF;
 RETURN jsonb_build_object('reservation_id',p_id,'maximum_units',p_units);END IF;
 SELECT coalesce(sum(coalesce(actual_units,maximum_units)),0) INTO used FROM ctt_staff_private.review_batch_budget_reservations WHERE budget_id=b.budget_id;
 IF used+p_units>b.maximum_units THEN RAISE EXCEPTION 'CTT shared source budget unavailable' USING ERRCODE='55000';END IF;
 INSERT INTO ctt_staff_private.review_batch_budget_reservations(reservation_id,preparation_id,budget_id,maximum_units) VALUES(p_id,p_prep,b.budget_id,p_units);
 RETURN jsonb_build_object('reservation_id',p_id,'maximum_units',p_units,'remaining_units',b.maximum_units-used-p_units);
END $fn$;

CREATE FUNCTION ctt_staff_private.source_preparation_finish(p_id uuid,p_observations bytea,p_report bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;p ctt_staff_private.review_source_preparations%ROWTYPE;i jsonb;o jsonb;r jsonb;oh text;rh text;
BEGIN
 svc:=ctt_staff_private.batch_require_service('verifier');
 SELECT * INTO p FROM ctt_staff_private.review_source_preparations WHERE preparation_id=p_id FOR UPDATE;
 SELECT inventory_json INTO i FROM ctt_staff_private.review_source_inventories WHERE inventory_sha256=p.inventory_sha256;
 o:=ctt_staff_private.batch_json(p_observations);r:=ctt_staff_private.batch_json(p_report);
 PERFORM ctt_staff_private.batch_require_keys(o,ARRAY['schema','preparation_id','inventory_sha256','profile_hash','pages']);
 oh:=encode(public.digest(p_observations,'sha256'),'hex');rh:=encode(public.digest(p_report,'sha256'),'hex');
 IF p.preparation_id IS NULL OR p.backend_pin IS DISTINCT FROM svc.backend_pin
 OR o->>'schema' IS DISTINCT FROM 'ctt.batch.structural-observations.v1' OR r->>'schema' IS DISTINCT FROM 'ctt.batch.structural-report.v1'
 OR o->>'preparation_id' IS DISTINCT FROM p_id::text OR r->>'preparation_id' IS DISTINCT FROM p_id::text
 OR o->>'inventory_sha256' IS DISTINCT FROM p.inventory_sha256 OR r->>'inventory_sha256' IS DISTINCT FROM p.inventory_sha256
 OR o->>'profile_hash' IS DISTINCT FROM p.profile_hash OR r->>'profile_hash' IS DISTINCT FROM p.profile_hash
 OR r->>'observations_sha256' IS DISTINCT FROM oh
 OR jsonb_typeof(o->'pages') IS DISTINCT FROM 'array'
 OR (SELECT jsonb_agg(jsonb_build_object('pdf_sha256',value->'pdf_sha256','pdf_page',value->'pdf_page','render_sha256',value->'render_sha256') ORDER BY ordinal) FROM jsonb_array_elements(o->'pages') WITH ORDINALITY AS v(value,ordinal))
 IS DISTINCT FROM (SELECT jsonb_agg(jsonb_build_object('pdf_sha256',value->'pdf_sha256','pdf_page',value->'pdf_page','render_sha256',value->'render_sha256') ORDER BY ordinal) FROM jsonb_array_elements(i->'pages') WITH ORDINALITY AS v(value,ordinal)) THEN
 RAISE EXCEPTION 'CTT source observations binding denied' USING ERRCODE='42501';END IF;
 IF p.status='completed' THEN
 IF p.observations_bytes IS DISTINCT FROM p_observations OR p.report_bytes IS DISTINCT FROM p_report THEN
 RAISE EXCEPTION 'CTT source observations immutable' USING ERRCODE='40001';END IF;
 ELSE
 UPDATE ctt_staff_private.review_source_preparations SET status='completed',observations_bytes=p_observations,observations_sha256=oh,report_bytes=p_report,report_sha256=rh WHERE preparation_id=p_id;
 END IF;
 RETURN jsonb_build_object('preparation_id',p_id,'status','completed','observations_sha256',oh,'report_sha256',rh,'backend_pin',svc.backend_pin);
END $fn$;

-- Funding is admitted separately from capture authority and cannot move an old
-- run's costs into a fresh allowance. All physical source refs must already match.
CREATE FUNCTION ctt_staff_private.source_preparation_bind_run(p_prep uuid,p_run uuid,p_field_profile_bytes bytea)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;p ctt_staff_private.review_source_preparations%ROWTYPE;b ctt_staff_private.review_source_api_budgets%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;i jsonb;field_profile jsonb;m text;ctx jsonb;old ctt_staff_private.review_source_budget_run_bindings%ROWTYPE;
BEGIN
 svc:=ctt_staff_private.batch_require_service('operator');
 SELECT * INTO p FROM ctt_staff_private.review_source_preparations WHERE preparation_id=p_prep FOR SHARE;
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run FOR UPDATE;
 SELECT * INTO b FROM ctt_staff_private.review_source_api_budgets WHERE budget_id=p.budget_id FOR UPDATE;
 SELECT inventory_json INTO i FROM ctt_staff_private.review_source_inventories WHERE inventory_sha256=p.inventory_sha256;
 IF p.preparation_id IS NULL OR r.run_id IS NULL OR p.status<>'completed' OR p.revoked_at IS NOT NULL OR b.revoked_at IS NOT NULL
 OR p.backend_pin IS DISTINCT FROM svc.backend_pin OR r.backend_pin IS DISTINCT FROM svc.backend_pin
 OR b.account_id IS DISTINCT FROM r.account_id OR b.tenant_id IS DISTINCT FROM r.tenant_id OR b.edition_id IS DISTINCT FROM r.edition_id
 OR clock_timestamp()<b.valid_from OR clock_timestamp()>=b.valid_until THEN
 RAISE EXCEPTION 'CTT shared source run binding denied' USING ERRCODE='42501';END IF;
 -- The capability's price label alone cannot accredit the actual extractor
 -- configuration. Bind the exact profile bytes whose hash the reader enforces.
 field_profile:=ctt_staff_private.batch_json(p_field_profile_bytes);
 IF encode(public.digest(p_field_profile_bytes,'sha256'),'hex') IS DISTINCT FROM r.profile_hash
 OR field_profile->'provider'->'pricing' IS DISTINCT FROM b.quote_json THEN
 RAISE EXCEPTION 'CTT shared source extraction profile price mismatch' USING ERRCODE='42501';END IF;
 IF jsonb_array_length(r.manifest_hashes)<1 OR NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_members WHERE run_id=p_run) THEN
 RAISE EXCEPTION 'CTT shared source run scope empty' USING ERRCODE='42501';END IF;
 FOR m IN SELECT value#>>'{}' FROM jsonb_array_elements(r.manifest_hashes) LOOP
 ctx:=ctt_staff_private.batch_account_context(r.account_id,r.edition_id,m);
 IF ctx->>'provider_price_hash' IS DISTINCT FROM b.quote_sha256 OR ctx->>'profile_hash' IS DISTINCT FROM r.profile_hash THEN
 RAISE EXCEPTION 'CTT shared source run quote mismatch' USING ERRCODE='42501';END IF;
 IF NOT EXISTS(SELECT 1 FROM ctt_staff_private.review_source_manifests_v2 WHERE manifest_sha256=m AND tenant_id=b.tenant_id AND edition_id=b.edition_id)
 OR EXISTS(SELECT 1 FROM ctt_staff_private.review_source_manifests_v2 source CROSS JOIN LATERAL jsonb_array_elements(source.manifest_json->'pdfs') pdf WHERE source.manifest_sha256=m AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(i->'pdfs') inv WHERE inv->>'sha256'=pdf->>'sha256' AND inv->'page_count'=pdf->'page_count'))
 OR EXISTS(SELECT 1 FROM ctt_staff_private.review_source_pages_v2 page WHERE page.manifest_sha256=m AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(i->'pages') inv WHERE inv->>'pdf_sha256'=page.pdf_sha256 AND (inv->>'pdf_page')::integer=page.pdf_page)) THEN
 RAISE EXCEPTION 'CTT shared source run corpus mismatch' USING ERRCODE='42501';END IF;
 END LOOP;
 SELECT * INTO old FROM ctt_staff_private.review_source_budget_run_bindings WHERE run_id=p_run;
 IF old.run_id IS NOT NULL THEN
 IF old.preparation_id<>p_prep OR old.budget_id<>b.budget_id OR old.field_profile_bytes IS DISTINCT FROM p_field_profile_bytes OR old.field_profile_hash IS DISTINCT FROM r.profile_hash THEN RAISE EXCEPTION 'CTT shared source run binding immutable' USING ERRCODE='40001';END IF;
 RETURN jsonb_build_object('run_id',p_run,'preparation_id',p_prep,'budget_id',b.budget_id,'field_profile_hash',r.profile_hash);END IF;
 IF EXISTS(SELECT 1 FROM ctt_staff_private.review_batch_budget_reservations WHERE run_id=p_run) THEN
 RAISE EXCEPTION 'CTT shared source run already spent' USING ERRCODE='55000';END IF;
 INSERT INTO ctt_staff_private.review_source_budget_run_bindings(run_id,preparation_id,budget_id,field_profile_bytes,field_profile_hash) VALUES(p_run,p_prep,b.budget_id,p_field_profile_bytes,r.profile_hash);
 RETURN jsonb_build_object('run_id',p_run,'preparation_id',p_prep,'budget_id',b.budget_id,'field_profile_hash',r.profile_hash);
END $fn$;

CREATE FUNCTION ctt_staff_private.source_preparation_revoke(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;
BEGIN
 svc:=ctt_staff_private.batch_require_service('operator');
 UPDATE ctt_staff_private.review_source_preparations SET revoked_at=coalesce(revoked_at,clock_timestamp()) WHERE preparation_id=p_id AND backend_pin=svc.backend_pin;
 IF NOT FOUND THEN RAISE EXCEPTION 'CTT source preparation unknown' USING ERRCODE='42501';END IF;
 RETURN jsonb_build_object('preparation_id',p_id,'revoked',true);
END $fn$;
CREATE FUNCTION ctt_staff_private.source_budget_revoke(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;
BEGIN
 svc:=ctt_staff_private.batch_require_service('operator');
 UPDATE ctt_staff_private.review_source_api_budgets SET revoked_at=coalesce(revoked_at,clock_timestamp()) WHERE budget_id=p_id AND backend_pin=svc.backend_pin;
 IF NOT FOUND THEN RAISE EXCEPTION 'CTT source budget unknown' USING ERRCODE='42501';END IF;
 RETURN jsonb_build_object('budget_id',p_id,'revoked',true);
END $fn$;

CREATE FUNCTION ctt_staff_private.source_budget_overlaps_run(p_run uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $fn$
 SELECT EXISTS(
 SELECT 1 FROM ctt_staff_private.review_batch_runs run
 JOIN ctt_staff_private.review_source_api_budgets budget ON budget.backend_pin=run.backend_pin
 AND budget.account_id=run.account_id AND budget.tenant_id=run.tenant_id AND budget.edition_id=run.edition_id
 JOIN ctt_staff_private.review_source_preparations prep ON prep.budget_id=budget.budget_id
 JOIN ctt_staff_private.review_source_inventories inventory USING(inventory_sha256)
 JOIN ctt_staff_private.review_source_manifests_v2 source ON run.manifest_hashes ? source.manifest_sha256
 CROSS JOIN LATERAL jsonb_array_elements(source.manifest_json->'pdfs') source_pdf
 CROSS JOIN LATERAL jsonb_array_elements(inventory.inventory_json->'pdfs') inventory_pdf
 WHERE run.run_id=p_run AND source_pdf->>'sha256'=inventory_pdf->>'sha256'
 )
$fn$;

CREATE OR REPLACE FUNCTION ctt_staff_private.batch_reserve_budget(p_run uuid,p_id uuid,p_units bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;
 b ctt_staff_private.review_source_api_budgets%ROWTYPE;p ctt_staff_private.review_source_preparations%ROWTYPE;
 binding ctt_staff_private.review_source_budget_run_bindings%ROWTYPE;
 old ctt_staff_private.review_batch_budget_reservations%ROWTYPE;cap bigint;used bigint;global_used bigint;m text;ctx jsonb;
BEGIN
 svc:=ctt_staff_private.batch_require_service('verifier');
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=p_run FOR UPDATE;
 IF r.run_id IS NULL OR r.backend_pin IS DISTINCT FROM svc.backend_pin OR p_id IS NULL OR p_units IS NULL OR p_units<=0 THEN
 RAISE EXCEPTION 'CTT batch budget denied' USING ERRCODE='42501';END IF;
 SELECT * INTO binding FROM ctt_staff_private.review_source_budget_run_bindings WHERE run_id=p_run;
 IF binding.run_id IS NULL AND ctt_staff_private.source_budget_overlaps_run(p_run) THEN
 RAISE EXCEPTION 'CTT shared source run binding required' USING ERRCODE='55000';END IF;
 IF binding.run_id IS NOT NULL THEN
 SELECT * INTO p FROM ctt_staff_private.review_source_preparations WHERE preparation_id=binding.preparation_id FOR SHARE;
 SELECT * INTO b FROM ctt_staff_private.review_source_api_budgets WHERE budget_id=binding.budget_id FOR UPDATE;
 IF b.backend_pin IS DISTINCT FROM svc.backend_pin OR p.backend_pin IS DISTINCT FROM svc.backend_pin
 OR b.account_id IS DISTINCT FROM r.account_id OR b.tenant_id IS DISTINCT FROM r.tenant_id OR b.edition_id IS DISTINCT FROM r.edition_id
 OR p.status<>'completed' OR p.revoked_at IS NOT NULL OR b.revoked_at IS NOT NULL
 OR binding.field_profile_hash IS DISTINCT FROM r.profile_hash
 OR encode(public.digest(binding.field_profile_bytes,'sha256'),'hex') IS DISTINCT FROM r.profile_hash
 OR ctt_staff_private.batch_json(binding.field_profile_bytes)->'provider'->'pricing' IS DISTINCT FROM b.quote_json
 OR clock_timestamp()<b.valid_from OR clock_timestamp()>=b.valid_until THEN
 RAISE EXCEPTION 'CTT shared source run unavailable' USING ERRCODE='42501';END IF;
 FOR m IN SELECT value#>>'{}' FROM jsonb_array_elements(r.manifest_hashes) LOOP
 ctx:=ctt_staff_private.batch_account_context(r.account_id,r.edition_id,m);
 IF ctx->>'provider_price_hash' IS DISTINCT FROM b.quote_sha256 OR ctx->>'profile_hash' IS DISTINCT FROM r.profile_hash THEN
 RAISE EXCEPTION 'CTT shared source run price changed' USING ERRCODE='42501';END IF;
 END LOOP;
 END IF;
 SELECT * INTO old FROM ctt_staff_private.review_batch_budget_reservations WHERE reservation_id=p_id;
 IF old.reservation_id IS NOT NULL THEN
 IF old.run_id IS DISTINCT FROM p_run OR old.maximum_units<>p_units OR old.budget_id IS DISTINCT FROM binding.budget_id THEN
 RAISE EXCEPTION 'CTT batch reservation mismatch' USING ERRCODE='40001';END IF;
 RETURN jsonb_build_object('reservation_id',p_id,'maximum_units',p_units);END IF;
 SELECT min(provider_budget_units) INTO cap FROM ctt_staff_private.review_batch_capabilities
 WHERE account_id=r.account_id AND edition_id=r.edition_id AND (r.manifest_hashes ? manifest_sha256)
 AND revoked_at IS NULL AND backend_pin=svc.backend_pin AND profile_hash=r.profile_hash
 AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_until AND provider_price_hash IS NOT NULL;
 SELECT coalesce(sum(coalesce(actual_units,maximum_units)),0) INTO used FROM ctt_staff_private.review_batch_budget_reservations WHERE run_id=p_run;
 IF cap IS NULL OR used::numeric+p_units>cap THEN RAISE EXCEPTION 'CTT batch budget unavailable' USING ERRCODE='55000';END IF;
 IF binding.run_id IS NOT NULL THEN
 SELECT coalesce(sum(coalesce(actual_units,maximum_units)),0) INTO global_used FROM ctt_staff_private.review_batch_budget_reservations WHERE budget_id=b.budget_id;
 IF global_used::numeric+p_units>b.maximum_units THEN RAISE EXCEPTION 'CTT shared source budget unavailable' USING ERRCODE='55000';END IF;
 ELSE global_used:=used;END IF;
 INSERT INTO ctt_staff_private.review_batch_budget_reservations(reservation_id,run_id,budget_id,maximum_units) VALUES(p_id,p_run,binding.budget_id,p_units);
 RETURN jsonb_build_object('reservation_id',p_id,'maximum_units',p_units,'remaining_units',CASE WHEN binding.run_id IS NULL THEN cap-used-p_units ELSE least(cap-used-p_units,b.maximum_units-global_used-p_units) END);
END $fn$;

CREATE OR REPLACE FUNCTION ctt_staff_private.batch_reconcile_budget(p_id uuid,p_actual bigint,p_receipt text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
DECLARE svc ctt_staff_private.review_batch_services%ROWTYPE;reservation ctt_staff_private.review_batch_budget_reservations%ROWTYPE;
 b ctt_staff_private.review_source_api_budgets%ROWTYPE;r ctt_staff_private.review_batch_runs%ROWTYPE;
BEGIN
 svc:=ctt_staff_private.batch_require_service('verifier');
 SELECT * INTO reservation FROM ctt_staff_private.review_batch_budget_reservations WHERE reservation_id=p_id;
 IF reservation.budget_id IS NOT NULL THEN
 SELECT * INTO b FROM ctt_staff_private.review_source_api_budgets WHERE budget_id=reservation.budget_id FOR UPDATE;
 IF b.backend_pin IS DISTINCT FROM svc.backend_pin THEN RAISE EXCEPTION 'CTT source budget receipt denied' USING ERRCODE='42501';END IF;
 ELSE
 SELECT * INTO r FROM ctt_staff_private.review_batch_runs WHERE run_id=reservation.run_id;
 IF r.backend_pin IS DISTINCT FROM svc.backend_pin THEN RAISE EXCEPTION 'CTT batch budget receipt denied' USING ERRCODE='42501';END IF;
 END IF;
 SELECT * INTO reservation FROM ctt_staff_private.review_batch_budget_reservations WHERE reservation_id=p_id FOR UPDATE;
 IF reservation.reservation_id IS NULL OR p_actual IS NULL OR p_actual NOT BETWEEN 0 AND reservation.maximum_units
 OR p_receipt IS NULL OR p_receipt!~'^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'CTT batch budget receipt denied' USING ERRCODE='42501';END IF;
 IF reservation.actual_units IS NOT NULL AND (reservation.actual_units<>p_actual OR reservation.receipt_hash IS DISTINCT FROM p_receipt) THEN
 RAISE EXCEPTION 'CTT batch budget receipt changed' USING ERRCODE='40001';END IF;
 -- Settlement after expiration/revocation records incurred usage; it never
 -- grants another request or releases an unproved reservation.
 UPDATE ctt_staff_private.review_batch_budget_reservations SET actual_units=p_actual,receipt_hash=p_receipt WHERE reservation_id=p_id;
 RETURN jsonb_build_object('reservation_id',p_id,'actual_units',p_actual);
END $fn$;

CREATE FUNCTION ctt_staff_private.source_preparation_protect_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $fn$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'CTT source preparation evidence immutable' USING ERRCODE='42501';END IF;
 IF TG_TABLE_NAME='review_source_api_budgets' THEN
 IF (to_jsonb(OLD)-'revoked_at') IS DISTINCT FROM (to_jsonb(NEW)-'revoked_at') OR (OLD.revoked_at IS NOT NULL AND OLD.revoked_at IS DISTINCT FROM NEW.revoked_at) THEN
 RAISE EXCEPTION 'CTT source budget immutable' USING ERRCODE='42501';END IF;
 ELSIF TG_TABLE_NAME='review_source_preparations' THEN
 IF (to_jsonb(OLD)-ARRAY['status','observations_bytes','observations_sha256','report_bytes','report_sha256','revoked_at']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','observations_bytes','observations_sha256','report_bytes','report_sha256','revoked_at'])
 OR (OLD.revoked_at IS NOT NULL AND OLD.revoked_at IS DISTINCT FROM NEW.revoked_at)
 OR (OLD.status='completed' AND (to_jsonb(OLD)-'revoked_at') IS DISTINCT FROM (to_jsonb(NEW)-'revoked_at')) THEN
 RAISE EXCEPTION 'CTT source preparation immutable' USING ERRCODE='42501';END IF;
 ELSIF TG_TABLE_NAME='review_batch_budget_reservations' THEN
 IF (to_jsonb(OLD)-ARRAY['actual_units','receipt_hash']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['actual_units','receipt_hash'])
 OR (OLD.actual_units IS NOT NULL AND (OLD.actual_units IS DISTINCT FROM NEW.actual_units OR OLD.receipt_hash IS DISTINCT FROM NEW.receipt_hash)) THEN
 RAISE EXCEPTION 'CTT source reservation immutable' USING ERRCODE='42501';END IF;
 END IF;
 RETURN NEW;
END $fn$;
CREATE TRIGGER source_preparation_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.review_source_preparations FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.source_preparation_protect_mutation();
CREATE TRIGGER source_budget_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.review_source_api_budgets FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.source_preparation_protect_mutation();
CREATE TRIGGER source_reservation_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.review_batch_budget_reservations FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.source_preparation_protect_mutation();
CREATE TRIGGER source_inventory_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.review_source_inventories FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER source_binding_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.review_source_budget_run_bindings FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
CREATE TRIGGER source_installation_immutable BEFORE UPDATE OR DELETE ON ctt_staff_private.review_source_preparation_installation FOR EACH ROW EXECUTE FUNCTION ctt_staff_private.reject_review_evidence_mutation();
DO $ownership$
DECLARE row record;name text;
BEGIN
 FOREACH name IN ARRAY ARRAY['review_source_preparation_installation','review_source_inventories','review_source_api_budgets','review_source_preparations','review_source_budget_run_bindings'] LOOP
 EXECUTE format('ALTER TABLE ctt_staff_private.%I OWNER TO samchat_ctt_staff_owner',name);
 EXECUTE format('REVOKE ALL ON ctt_staff_private.%I FROM PUBLIC,devnous_user',name);
 END LOOP;
 FOR row IN SELECT p.oid::regprocedure signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='ctt_staff_private' AND (p.proname LIKE 'source_preparation_%' OR p.proname LIKE 'source_budget_%' OR p.proname IN('batch_reserve_budget','batch_reconcile_budget')) LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO samchat_ctt_staff_owner',row.signature);
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,devnous_user',row.signature);
 END LOOP;
END $ownership$;
COMMIT;
