-- Candidate-only rollback: never restore app ownership over protected v2 state.
BEGIN;
SET LOCAL search_path=pg_catalog;
DO $check$
BEGIN
  LOCK TABLE ctt_staff_private.capture_attempt_anchors,ctt_staff_private.capture_payloads,
    ctt_staff_private.legacy_v2_reservations,ctt_staff_private.effect_writers IN ACCESS EXCLUSIVE MODE;
  IF EXISTS(SELECT 1 FROM ctt_staff_private.legacy_v2_reservations)
    OR EXISTS(SELECT 1 FROM ctt_staff_private.capture_attempt_anchors)
    OR EXISTS(SELECT 1 FROM ctt_staff_private.effect_writers) THEN
    RAISE EXCEPTION 'CTT legacy rollback refused: protected configuration or evidence exists'; END IF;
END
$check$;
DROP TRIGGER ctt_v2_legacy_payload_reserve ON ctt_staff_private.capture_payloads;
DROP TRIGGER ctt_v2_legacy_anchor_reserve ON ctt_staff_private.capture_attempt_anchors;
DO $restore$
DECLARE item record; privilege record; acl_role record; role_sql text;
BEGIN
  FOR item IN SELECT * FROM ctt_staff_private.legacy_boundary_baseline WHERE object_kind='table' ORDER BY object_name LOOP
    EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE',item.object_name);
    EXECUTE format('DROP TRIGGER ctt_legacy_v2_boundary ON public.%I',item.object_name);
    EXECUTE format('DROP TRIGGER ctt_legacy_no_truncate ON public.%I',item.object_name);
    FOR acl_role IN SELECT DISTINCT a.grantee FROM pg_catalog.pg_class c
      CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
      WHERE c.oid=pg_catalog.to_regclass('public.'||item.object_name) AND a.grantee<>c.relowner LOOP
      role_sql:=CASE WHEN acl_role.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_catalog.pg_get_userbyid(acl_role.grantee)) END;
      EXECUTE format('REVOKE ALL ON public.%I FROM %s',item.object_name,role_sql);
    END LOOP;
    EXECUTE format('ALTER TABLE public.%I OWNER TO %I',item.object_name,item.original_owner);
    FOR privilege IN SELECT * FROM jsonb_to_recordset(item.original_acl) AS a(role text,privilege text,grantable boolean) LOOP
      role_sql:=CASE WHEN privilege.role='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(privilege.role) END;
      EXECUTE format('GRANT %s ON public.%I TO %s%s',privilege.privilege,item.object_name,role_sql,
        CASE WHEN privilege.grantable THEN ' WITH GRANT OPTION' ELSE '' END);
    END LOOP;
  END LOOP;
  FOR item IN SELECT * FROM ctt_staff_private.legacy_boundary_baseline WHERE object_kind='function' ORDER BY object_name LOOP
    FOR acl_role IN SELECT DISTINCT a.grantee FROM pg_catalog.pg_proc p
      CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
      WHERE p.oid=pg_catalog.to_regprocedure(item.object_name) AND a.grantee<>p.proowner LOOP
      role_sql:=CASE WHEN acl_role.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_catalog.pg_get_userbyid(acl_role.grantee)) END;
      EXECUTE 'REVOKE ALL ON FUNCTION '||item.object_name||' FROM '||role_sql;
    END LOOP;
    -- Snapshot definitions may use their existing public composite type names.
    PERFORM set_config('search_path','public,pg_catalog',true);
    EXECUTE item.original_definition;
    PERFORM set_config('search_path','pg_catalog',true);
    EXECUTE 'ALTER FUNCTION '||item.object_name||' OWNER TO '||quote_ident(item.original_owner);
    FOR privilege IN SELECT * FROM jsonb_to_recordset(item.original_acl) AS a(role text,privilege text,grantable boolean) LOOP
      role_sql:=CASE WHEN privilege.role='PUBLIC' THEN 'PUBLIC' ELSE quote_ident(privilege.role) END;
      EXECUTE 'GRANT EXECUTE ON FUNCTION '||item.object_name||' TO '||role_sql||
        CASE WHEN privilege.grantable THEN ' WITH GRANT OPTION' ELSE '' END;
    END LOOP;
  END LOOP;
END
$restore$;
DROP FUNCTION public.legacy_insert_team(jsonb);
DROP FUNCTION public.legacy_insert_player(jsonb);
DROP FUNCTION public.legacy_finalize_player(uuid,text);
DROP FUNCTION public.legacy_apply_postcommit_projection(text,uuid,bigint,text,jsonb);
DROP FUNCTION ctt_staff_private.guard_legacy_v2_boundary();
DROP FUNCTION ctt_staff_private.current_exact_capture_row(text,uuid);
DROP FUNCTION ctt_staff_private.check_legacy_insert_columns(oid,jsonb);
DROP FUNCTION ctt_staff_private.reserve_v2_legacy_ids();
DROP FUNCTION ctt_staff_private.lock_legacy_reference_ids(uuid[]);
DROP FUNCTION ctt_staff_private.legacy_row_reference_ids(oid,jsonb,oid[]);
DROP TABLE ctt_staff_private.legacy_v2_reservations;
DROP TABLE ctt_staff_private.legacy_boundary_baseline;
COMMIT;
