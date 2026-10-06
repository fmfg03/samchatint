// Isolated PostgreSQL/WASM check. Run with:
// node tests/integration/partial_advance_migration.mjs /path/to/@electric-sql/pglite/dist/index.js
// No network, production connection, or application secrets are used.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const { PGlite } = await import(process.argv[2]);
const db = new PGlite();
await db.exec(`
CREATE TABLE cuentas_de_gastos (
 id uuid PRIMARY KEY, estado text, empleado_id uuid, beneficiario_empleado_id uuid,
 beneficiario_proveedor_cliente_id uuid, beneficiario_alterno_tipo text,
 currency text, torneo_id uuid
);
CREATE TABLE documentos (
 id uuid PRIMARY KEY, tipo text, estado text, cuenta_gastos_id uuid, empleado_id uuid,
 beneficiario_empleado_id uuid, beneficiario_proveedor_cliente_id uuid,
 beneficiario_alterno_tipo text, currency text, torneo_id uuid, client_submission_id uuid
);
CREATE TABLE expense_reports (
 id uuid PRIMARY KEY, documento_id uuid, informe_documento_id uuid,
 cuenta_gastos_id uuid, gasto_cantidad numeric, estado_gasto text,
 coi_estado text, coi_exported_at timestamptz, coi_exported_by_id uuid,
 coi_status_updated_at timestamptz, coi_status_updated_by_id uuid, updated_at timestamptz
);
CREATE TABLE reembolsos (
 id uuid PRIMARY KEY, cuenta_gastos_id uuid, estado text, tipo text
);
CREATE UNIQUE INDEX legacy_active_settlement ON reembolsos(cuenta_gastos_id) WHERE estado <> 'cancelado';
`);
const migration = readFileSync('database/migrations/20261006_partial_advance_comprobaciones.sql', 'utf8');
await db.exec(migration);
await db.exec(migration); // Owner rerun is idempotent.
const account = '10000000-0000-0000-0000-000000000001';
const original = '20000000-0000-0000-0000-000000000001';
const lot = '20000000-0000-0000-0000-000000000002';
const employee = '30000000-0000-0000-0000-000000000001';
const expense = '40000000-0000-0000-0000-000000000001';
await db.query(`INSERT INTO cuentas_de_gastos(id,estado,empleado_id,currency) VALUES ($1,'abierta',$2,'MXN')`, [account, employee]);
await db.query(`INSERT INTO documentos(id,tipo,estado,cuenta_gastos_id,empleado_id,currency) VALUES ($1,'INFORME','borrador',$2,$3,'MXN')`, [original, account, employee]);
await db.query(`INSERT INTO documentos(id,tipo,estado,informe_origen_id,empleado_id,currency,client_submission_id,motivo_comprobacion_parcial)
 VALUES ($1,'INFORME','borrador',$2,$3,'MXN',$1,'Faltan comprobantes')`, [lot, original, employee]);
await db.query(`INSERT INTO expense_reports(id,documento_id,informe_documento_id,cuenta_gastos_id,gasto_cantidad,estado_gasto)
 VALUES ($1,$2,$2,$3,13912.50,'activo')`, [expense, lot, account]);
await db.query(`UPDATE cuentas_de_gastos SET comprobacion_parcial=true WHERE id=$1`, [account]);
await assert.rejects(db.query(`UPDATE documentos SET empleado_id=NULL WHERE id=$1`, [original]), /identity cannot change/);
await assert.rejects(db.query(`UPDATE documentos SET estado='aprobado' WHERE id=$1`, [original]), /case container/);
await assert.rejects(db.query(`DELETE FROM documentos WHERE id=$1`, [original]), /cannot be deleted/);
await assert.rejects(db.query(`UPDATE documentos SET motivo_comprobacion_parcial='Motivo cambiado' WHERE id=$1`, [lot]), /identity is immutable/);
await db.query(`UPDATE documentos SET estado='aprobado' WHERE id=$1`, [lot]);
for (const mutation of [
 `UPDATE expense_reports SET gasto_cantidad=14000 WHERE id=$1`,
 `UPDATE expense_reports SET estado_gasto='cancelado' WHERE id=$1`,
 `UPDATE expense_reports SET informe_documento_id=NULL WHERE id=$1`,
 `DELETE FROM expense_reports WHERE id=$1`,
]) {
 await assert.rejects(db.query(mutation, [expense]), /comprobación|comprobaci|Partial|partial/);
}
await db.query(`UPDATE expense_reports SET coi_estado='contabilizado',coi_exported_at=now() WHERE id=$1`, [expense]);
await assert.rejects(db.query(`UPDATE documentos SET estado='borrador' WHERE id=$1`, [lot]), /immutable/);
await assert.rejects(db.query(`UPDATE documentos SET informe_origen_id=NULL WHERE id=$1`, [lot]), /immutable/);
await assert.rejects(db.query(`UPDATE cuentas_de_gastos SET empleado_id=NULL WHERE id=$1`, [account]), /identity cannot change/);
await assert.rejects(db.query(`INSERT INTO expense_reports(id,documento_id,informe_documento_id,cuenta_gastos_id,gasto_cantidad,estado_gasto)
 VALUES ('40000000-0000-0000-0000-000000000002',$1,$1,$2,10,'activo')`, [lot, account]), /Cannot add/);
// Multiple actual returns are permitted; both submission identity and the
// historical one-active-reimbursement invariant remain database-enforced.
await db.query(`INSERT INTO reembolsos VALUES
 ('50000000-0000-0000-0000-000000000001',$1,'pagado','devolucion','60000000-0000-0000-0000-000000000001'),
 ('50000000-0000-0000-0000-000000000002',$1,'pagado','devolucion','60000000-0000-0000-0000-000000000002')`, [account]);
await assert.rejects(db.query(`INSERT INTO reembolsos VALUES
 ('50000000-0000-0000-0000-000000000003',$1,'pagado','devolucion','60000000-0000-0000-0000-000000000002')`, [account]), /unique/);
await db.query(`INSERT INTO reembolsos(id,cuenta_gastos_id,estado,tipo) VALUES ('50000000-0000-0000-0000-000000000004',$1,'pagado','reembolso')`, [account]);
await assert.rejects(db.query(`INSERT INTO reembolsos(id,cuenta_gastos_id,estado,tipo) VALUES ('50000000-0000-0000-0000-000000000005',$1,'pagado','reembolso')`, [account]), /unique/);
assert.equal((await db.query(`SELECT gasto_cantidad FROM expense_reports WHERE id=$1`, [expense])).rows[0].gasto_cantidad, '13912.50');
await db.close();
console.log('PASS: migration rerun, approval immutability, lot membership, identity, COI status, partial returns and duplicate constraints');
