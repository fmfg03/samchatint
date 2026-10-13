// Disposable in-memory PostgreSQL only. Module path comes from test harness.
import {pathToFileURL} from 'node:url';
import readline from 'node:readline';
const {PGlite} = await import(pathToFileURL(process.argv[2]).href);
const db = new PGlite();
await db.exec(`
CREATE TABLE documentos(id uuid PRIMARY KEY,torneo_id uuid,tipo text,estado text,
numero_referencia text,referencia_operaciones text,referencia_base text,
creado_en timestamptz,enviado_en timestamptz,currency text,monto_total numeric,budget_concept_id uuid);
ALTER TABLE documentos ADD COLUMN pagado_en timestamptz;
ALTER TABLE documentos ADD COLUMN gasto_generado_id uuid;
ALTER TABLE documentos ADD COLUMN cuenta_gastos_id uuid;
ALTER TABLE documentos ADD COLUMN concepto_pago text;
ALTER TABLE documentos ADD COLUMN informe_origen_id uuid;
ALTER TABLE documentos ADD COLUMN motivo_comprobacion_parcial text;
CREATE TABLE aprobaciones(id uuid,entidad_id uuid,tipo_entidad text,accion text,fecha timestamptz);
CREATE TABLE expense_reports(id uuid,documento_id uuid,informe_documento_id uuid,cuenta_gastos_id uuid,estado_gasto text);
CREATE TABLE anticipos(id uuid,documento_id uuid,estado text);
CREATE TABLE reembolsos(id uuid,documento_id uuid,cuenta_gastos_id uuid,estado text,tipo text);
CREATE TABLE empleados(id uuid,activo boolean);
CREATE TABLE authorization_positions(position_key text,active boolean);
CREATE TABLE authorization_position_assignments(position_key text,empleado_id uuid,active boolean);
CREATE TABLE documento_authorization_routes(documento_id uuid,eligible_empleado_ids jsonb,
eligible_position_keys jsonb,requires_operations_reference boolean);
INSERT INTO documentos(id,torneo_id,tipo,estado,numero_referencia,referencia_operaciones,referencia_base,creado_en,enviado_en,currency,monto_total,budget_concept_id) VALUES('20000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000001','INFORME','control_presupuestal','I-SYNTH','SYNTH-001','C-SYNTH','2026-01-02',NULL,'MXN',120.5,NULL);
INSERT INTO documentos(id,torneo_id,tipo,estado,numero_referencia,referencia_operaciones,referencia_base,creado_en,enviado_en,currency,monto_total,budget_concept_id) SELECT '20000000-0000-4000-8000-000000000002',torneo_id,tipo,estado,numero_referencia,referencia_operaciones,referencia_base,creado_en,enviado_en,currency,monto_total,budget_concept_id FROM documentos;
INSERT INTO empleados VALUES('40000000-0000-4000-8000-000000000001',TRUE);
INSERT INTO documento_authorization_routes VALUES('20000000-0000-4000-8000-000000000001','["40000000-0000-4000-8000-000000000001"]','["director_operaciones"]',TRUE);
INSERT INTO aprobaciones VALUES('50000000-0000-4000-8000-000000000001','20000000-0000-4000-8000-000000000001','documento','enviar','2026-01-03');
CREATE ROLE fixture_reader NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO fixture_reader;
GRANT SELECT ON documentos,aprobaciones,expense_reports,anticipos,reembolsos,empleados,authorization_positions,authorization_position_assignments,documento_authorization_routes TO fixture_reader;
SET ROLE fixture_reader;
`);
const tables=['documentos','aprobaciones','expense_reports','anticipos','reembolsos','empleados','authorization_positions','authorization_position_assignments','documento_authorization_routes'];
const schema=await db.query("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY($1::text[]) ORDER BY table_name,column_name",[tables]);
const identity=await db.query('SELECT current_database() AS db,current_user AS role');
process.stdout.write(JSON.stringify({ready:true,schema:schema.rows,identity:identity.rows[0]})+'\n');
const lines=readline.createInterface({input:process.stdin});
for await (const line of lines) {
  try {
    const request=JSON.parse(line);
    const result=await db.query(request.sql,request.parameters||[]);
    process.stdout.write(JSON.stringify({rows:result.rows,fields:result.fields.map(f=>f.name)})+'\n');
  } catch(error) {process.stdout.write(JSON.stringify({error:String(error.message)})+'\n');}
}
await db.close();
