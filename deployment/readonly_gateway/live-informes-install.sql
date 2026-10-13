-- Approval proposal only. Execute with owner authority, never from gateway startup.
-- Exact actor was resolved from public.empleados; no password column was read.
BEGIN;
CREATE ROLE samchat_ro_fmfg_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE samchat_ro_fmfg LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2 VALID UNTIL '2026-11-08 23:59:59+00';
ALTER ROLE samchat_ro_fmfg SET default_transaction_read_only=on;
ALTER ROLE samchat_ro_fmfg SET statement_timeout='3s';
ALTER ROLE samchat_ro_fmfg SET lock_timeout='500ms';
ALTER ROLE samchat_ro_fmfg SET search_path=samchat_ro_fmfg_v1,pg_catalog;
-- Preserve TEMP for both currently existing login roles before removing PUBLIC inheritance.
GRANT TEMPORARY ON DATABASE devnous_db TO devnous_user,postgres;
REVOKE TEMPORARY ON DATABASE devnous_db FROM PUBLIC;
GRANT CONNECT ON DATABASE devnous_db TO samchat_ro_fmfg;
CREATE SCHEMA samchat_ro_fmfg_v1 AUTHORIZATION samchat_ro_fmfg_owner;
REVOKE ALL ON SCHEMA samchat_ro_fmfg_v1 FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO samchat_ro_fmfg_owner;
GRANT SELECT (id,torneo_id,tipo,estado,numero_referencia,referencia_operaciones,referencia_base,creado_en,enviado_en,currency,monto_total,budget_concept_id,pagado_en,gasto_generado_id,cuenta_gastos_id,concepto_pago,informe_origen_id,motivo_comprobacion_parcial,empleado_id) ON public.documentos TO samchat_ro_fmfg_owner;
GRANT SELECT (id,entidad_id,tipo_entidad,accion,fecha) ON public.aprobaciones TO samchat_ro_fmfg_owner;
GRANT SELECT (id,documento_id,informe_documento_id,cuenta_gastos_id,estado_gasto) ON public.expense_reports TO samchat_ro_fmfg_owner;
GRANT SELECT (id,documento_id,estado) ON public.anticipos TO samchat_ro_fmfg_owner;
GRANT SELECT (id,documento_id,cuenta_gastos_id,estado,tipo) ON public.reembolsos TO samchat_ro_fmfg_owner;
GRANT SELECT (documento_id,eligible_empleado_ids,eligible_position_keys,requires_operations_reference) ON public.documento_authorization_routes TO samchat_ro_fmfg_owner;
GRANT SELECT (position_key,empleado_id,active) ON public.authorization_position_assignments TO samchat_ro_fmfg_owner;
GRANT SELECT (position_key,active) ON public.authorization_positions TO samchat_ro_fmfg_owner;
GRANT SELECT (id,activo,correo,rol,departamento,actualizado_en) ON public.empleados TO samchat_ro_fmfg_owner;
GRANT SELECT (tool_key,action_key,role_key,area_key,allowed,active,updated_at) ON public.access_control_rules TO samchat_ro_fmfg_owner;
SET LOCAL ROLE samchat_ro_fmfg_owner;
CREATE VIEW samchat_ro_fmfg_v1.scope_guard WITH (security_barrier=true) AS
SELECT a.id AS actor_uuid, a.rol AS current_role, a.departamento AS current_area,
       md5(concat_ws('|',a.id,a.correo,a.rol,a.departamento,a.activo,a.actualizado_en,
           informe.allowed,solicitud.allowed,informe.updated_at,solicitud.updated_at)) AS permission_stamp
FROM public.empleados a
LEFT JOIN LATERAL (
 SELECT r.allowed,r.updated_at FROM public.access_control_rules r
 WHERE r.active AND r.tool_key='gastos.informes' AND r.action_key='ver'
 AND r.role_key=lower(btrim(a.rol)) AND COALESCE(r.area_key,'')=lower(btrim(COALESCE(a.departamento,'')))
 ORDER BY r.updated_at DESC,r.allowed ASC LIMIT 1
) informe ON true
LEFT JOIN LATERAL (
 SELECT r.allowed,r.updated_at FROM public.access_control_rules r
 WHERE r.active AND r.tool_key='gastos.solicitudes' AND r.action_key='ver'
 AND r.role_key=lower(btrim(a.rol)) AND COALESCE(r.area_key,'')=lower(btrim(COALESCE(a.departamento,'')))
 ORDER BY r.updated_at DESC,r.allowed ASC LIMIT 1
) solicitud ON true
WHERE session_user='samchat_ro_fmfg'
 AND a.id='b8816679-ad77-4590-83d5-50ffce335854'::uuid
 AND lower(btrim(a.correo))='fmfg@pm.me' AND a.activo
 AND statement_timestamp() < '2026-11-08 23:59:59+00'::timestamptz
 AND informe.allowed IS DISTINCT FROM false AND solicitud.allowed IS DISTINCT FROM false
 AND lower(btrim(a.rol)) IN ('empleado','coordinador','finanzas','admin','superadmin','super_admin');
CREATE VIEW samchat_ro_fmfg_v1.documentos WITH (security_barrier=true) AS
SELECT d.id,d.torneo_id,d.tipo,d.estado,d.numero_referencia,d.referencia_operaciones,d.referencia_base,d.creado_en,d.enviado_en,d.currency,d.monto_total,d.budget_concept_id,d.pagado_en,d.gasto_generado_id,d.cuenta_gastos_id,d.concepto_pago,d.informe_origen_id,d.motivo_comprobacion_parcial
FROM public.documentos d
JOIN public.empleados requester ON requester.id=d.empleado_id
CROSS JOIN samchat_ro_fmfg_v1.scope_guard actor
WHERE d.tipo='INFORME'
 AND (d.empleado_id=actor.actor_uuid OR actor.current_role IN ('coordinador','finanzas','admin','superadmin','super_admin'))
 AND (lower(btrim(actor.current_role)) NOT IN ('admin','coordinador')
      OR lower(btrim(COALESCE(actor.current_area,''))) NOT IN ('operaciones','mercadotecnia')
      OR lower(btrim(COALESCE(requester.departamento,'')))=lower(btrim(actor.current_area)));
CREATE VIEW samchat_ro_fmfg_v1.aprobaciones WITH (security_barrier=true) AS
SELECT v.id,v.entidad_id,v.tipo_entidad,v.accion,v.fecha FROM public.aprobaciones v WHERE v.tipo_entidad='documento' AND EXISTS (SELECT 1 FROM samchat_ro_fmfg_v1.documentos d WHERE d.id=v.entidad_id);
CREATE VIEW samchat_ro_fmfg_v1.expense_reports WITH (security_barrier=true) AS
SELECT v.id,v.documento_id,v.informe_documento_id,v.cuenta_gastos_id,v.estado_gasto FROM public.expense_reports v WHERE EXISTS (SELECT 1 FROM samchat_ro_fmfg_v1.documentos d WHERE v.documento_id=d.id OR v.informe_documento_id=d.id OR (d.cuenta_gastos_id IS NOT NULL AND v.cuenta_gastos_id=d.cuenta_gastos_id));
CREATE VIEW samchat_ro_fmfg_v1.anticipos WITH (security_barrier=true) AS
SELECT v.id,v.documento_id,v.estado FROM public.anticipos v WHERE EXISTS (SELECT 1 FROM samchat_ro_fmfg_v1.documentos d WHERE v.documento_id=d.id);
CREATE VIEW samchat_ro_fmfg_v1.reembolsos WITH (security_barrier=true) AS
SELECT v.id,v.documento_id,v.cuenta_gastos_id,v.estado,v.tipo FROM public.reembolsos v WHERE EXISTS (SELECT 1 FROM samchat_ro_fmfg_v1.documentos d WHERE v.documento_id=d.id OR (d.cuenta_gastos_id IS NOT NULL AND v.cuenta_gastos_id=d.cuenta_gastos_id));
CREATE VIEW samchat_ro_fmfg_v1.documento_authorization_routes WITH (security_barrier=true) AS
SELECT v.documento_id,v.eligible_empleado_ids,v.eligible_position_keys,v.requires_operations_reference FROM public.documento_authorization_routes v WHERE EXISTS (SELECT 1 FROM samchat_ro_fmfg_v1.documentos d WHERE d.id=v.documento_id);
CREATE VIEW samchat_ro_fmfg_v1.authorization_position_assignments WITH (security_barrier=true) AS
SELECT v.position_key,v.empleado_id,v.active FROM public.authorization_position_assignments v WHERE EXISTS (SELECT 1 FROM samchat_ro_fmfg_v1.scope_guard actor WHERE actor.actor_uuid=v.empleado_id);
CREATE VIEW samchat_ro_fmfg_v1.authorization_positions WITH (security_barrier=true) AS
SELECT v.position_key,v.active FROM public.authorization_positions v WHERE EXISTS (SELECT 1 FROM samchat_ro_fmfg_v1.authorization_position_assignments a WHERE a.position_key=v.position_key);
CREATE VIEW samchat_ro_fmfg_v1.empleados WITH (security_barrier=true) AS
SELECT v.id,v.activo FROM public.empleados v WHERE EXISTS (SELECT 1 FROM samchat_ro_fmfg_v1.scope_guard actor WHERE actor.actor_uuid=v.id);
RESET ROLE;
GRANT USAGE ON SCHEMA samchat_ro_fmfg_v1 TO samchat_ro_fmfg;
GRANT SELECT (actor_uuid,permission_stamp) ON samchat_ro_fmfg_v1.scope_guard TO samchat_ro_fmfg;
GRANT SELECT ON samchat_ro_fmfg_v1.documentos,samchat_ro_fmfg_v1.aprobaciones,samchat_ro_fmfg_v1.expense_reports,samchat_ro_fmfg_v1.anticipos,samchat_ro_fmfg_v1.reembolsos,samchat_ro_fmfg_v1.documento_authorization_routes,samchat_ro_fmfg_v1.authorization_position_assignments,samchat_ro_fmfg_v1.authorization_positions,samchat_ro_fmfg_v1.empleados TO samchat_ro_fmfg;
COMMIT;
