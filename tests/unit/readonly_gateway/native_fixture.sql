-- Synthetic native PostgreSQL fixtures only; never run against serving database.
CREATE TABLE public.documentos (id uuid,torneo_id uuid,tipo text,estado text,numero_referencia text,referencia_operaciones text,referencia_base text,creado_en timestamp with time zone,enviado_en timestamp with time zone,currency character varying(3),monto_total numeric,budget_concept_id uuid,pagado_en timestamp with time zone,gasto_generado_id uuid,cuenta_gastos_id uuid,concepto_pago text,informe_origen_id uuid,motivo_comprobacion_parcial text,empleado_id uuid);
CREATE TABLE public.aprobaciones (id uuid,entidad_id uuid,tipo_entidad text,accion text,fecha timestamp with time zone);
CREATE TABLE public.expense_reports (id uuid,documento_id uuid,informe_documento_id uuid,cuenta_gastos_id uuid,estado_gasto text);
CREATE TABLE public.anticipos (id uuid,documento_id uuid,estado text);
CREATE TABLE public.reembolsos (id uuid,documento_id uuid,cuenta_gastos_id uuid,estado text,tipo character varying(20));
CREATE TABLE public.documento_authorization_routes (documento_id uuid,eligible_empleado_ids jsonb,eligible_position_keys jsonb,requires_operations_reference boolean);
CREATE TABLE public.authorization_position_assignments (position_key character varying(100),empleado_id uuid,active boolean);
CREATE TABLE public.authorization_positions (position_key character varying(100),active boolean);
CREATE TABLE public.empleados (id uuid,activo boolean,correo character varying(200),rol character varying(50),departamento character varying(100),actualizado_en timestamp with time zone);
CREATE TABLE public.access_control_rules (tool_key character varying(120),action_key character varying(40),role_key character varying(50),area_key character varying(100),allowed boolean,active boolean,updated_at timestamp with time zone);
CREATE ROLE devnous_user LOGIN;
CREATE ROLE outside_actor LOGIN;
INSERT INTO public.empleados(id,activo,correo,rol,departamento,actualizado_en) VALUES
 ('b8816679-ad77-4590-83d5-50ffce335854',true,'fmfg@pm.me','superadmin','Dirección',now()),
 ('10000000-0000-4000-8000-000000000001',true,'synthetic-one@example.invalid','empleado','Operaciones',now()),
 ('10000000-0000-4000-8000-000000000002',true,'synthetic-two@example.invalid','empleado','Mercadotecnia',now());
INSERT INTO public.documentos(id,empleado_id,tipo,estado,numero_referencia,referencia_operaciones,creado_en,currency,monto_total)
 VALUES ('20000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000001','INFORME','enviado','I-SYNTH-1','SYNTH-1','2026-10-01','MXN',100),
 ('20000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000002','INFORME','enviado','I-SYNTH-2','SYNTH-2','2026-10-02','MXN',200),
 ('20000000-0000-4000-8000-000000000003','10000000-0000-4000-8000-000000000001','SOLICITUD','enviado','S-SYNTH-3','SYNTH-3','2026-10-03','MXN',300);
