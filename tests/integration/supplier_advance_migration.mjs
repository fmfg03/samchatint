// Isolated PostgreSQL validation; no production connection or secrets.
// node tests/integration/supplier_advance_migration.mjs /path/to/@electric-sql/pglite/dist/index.js
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const { PGlite } = await import(process.argv[2]);
const db = new PGlite();
await db.exec(`CREATE TABLE documentos(id uuid PRIMARY KEY,tipo text,estado text,empleado_id uuid,proveedor_cliente_id uuid,beneficiario_empleado_id uuid,cuenta_gastos_id uuid,monto_solicitado numeric,monto_total numeric,currency text,torneo_id uuid,proyecto_otro text,fase text,pagado_en timestamptz,cfdi_report_id uuid); CREATE TABLE accounting_polizas(id uuid PRIMARY KEY,origen text,numero_poliza text);`);
const migration=readFileSync('database/migrations/20261008_supplier_advances.sql','utf8');
await db.exec(migration); await db.exec(migration);
const id=n=>`10000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
await db.query(`INSERT INTO documentos(id,tipo,estado,empleado_id,proveedor_cliente_id,monto_solicitado,monto_total,currency,proyecto_otro,is_supplier_advance,supplier_advance_due_date) VALUES ($1,'SOLICITUD','en_proceso_pago',$2,$3,1000,1000,'MXN','Proyecto',true,'2026-10-31')`,[id(1),id(2),id(3)]);
await assert.rejects(db.query(`UPDATE documentos SET currency='USD' WHERE id=$1`,[id(1)]),/ck_supplier_advance_mxn/);
async function child(n,allocation,total,provider=id(3),cfdi=id(100+n)) {
 return db.query(`INSERT INTO documentos(id,tipo,estado,empleado_id,proveedor_cliente_id,monto_solicitado,monto_total,currency,proyecto_otro,supplier_advance_id,supplier_advance_applied,supplier_invoice_total,cfdi_report_id) VALUES ($1,'SOLICITUD','control_presupuestal',$2,$3,$4,$4,'MXN','Proyecto',$5,$6,$7,$8)`,[id(n),id(2),provider,total-allocation,id(1),allocation,total,cfdi]);
}
await assert.rejects(child(4,600,600),/confirmed paid/);
await db.query(`UPDATE documentos SET estado='pagado',pagado_en=now() WHERE id=$1`,[id(1)]);
await assert.rejects(child(4,600,600,id(99)),/identity/);
await child(4,600,600);
await assert.rejects(child(5,500,500),/exceeds paid balance/);
await child(5,400,600);
await assert.rejects(db.query(`UPDATE documentos SET supplier_advance_applied=300,monto_total=300,monto_solicitado=300 WHERE id=$1`,[id(5)]),/immutable/);
await assert.rejects(db.query(`UPDATE documentos SET cfdi_report_id=$2 WHERE id=$1`,[id(5),id(99)]),/immutable/);
await assert.rejects(db.query(`DELETE FROM documentos WHERE id=$1`,[id(5)]),/cannot be deleted/);
await db.query(`UPDATE documentos SET estado='rechazado' WHERE id=$1`,[id(5)]);
await assert.rejects(db.query(`UPDATE documentos SET estado='control_presupuestal' WHERE id=$1`,[id(5)]),/cannot be reopened/);
await child(6,400,400);
await db.query(`UPDATE documentos SET estado='aprobado' WHERE id=$1`,[id(6)]);
await assert.rejects(db.query(`UPDATE documentos SET estado='cancelado' WHERE id=$1`,[id(6)]),/accounting reversal/);
await assert.rejects(db.query(`UPDATE documentos SET monto_total=2000,monto_solicitado=2000 WHERE id=$1`,[id(1)]),/accounting reversal/);
await assert.rejects(child(7,1,1,id(3),id(104)),/exceeds paid balance|unique/);
await db.query(`INSERT INTO accounting_polizas VALUES ($1,'proveedor_anticipo_pago','LAM-SUP-ADV-test')`,[id(8)]);
await assert.rejects(db.query(`INSERT INTO accounting_polizas VALUES ($1,'proveedor_anticipo_pago','LAM-SUP-ADV-test')`,[id(9)]),/unique/);
// Historical ordinary requests remain writable under the new defaults.
await db.query(`INSERT INTO documentos(id,tipo,estado,monto_solicitado,monto_total) VALUES ($1,'SOLICITUD','borrador',123,123)`,[id(10)]);
await db.query(`UPDATE documentos SET monto_total=200,monto_solicitado=200 WHERE id=$1`,[id(10)]);
assert.equal((await db.query(`SELECT SUM(supplier_advance_applied) AS allocated FROM documentos WHERE supplier_advance_id=$1 AND estado NOT IN ('rechazado','cancelado')`,[id(1)])).rows[0].allocated,'1000.00');
await db.close();
console.log('PASS: migration rerun, payment evidence, party identity, allocation cap, immutable receipts, rejection release, no reopening, unique posting and ordinary request compatibility');
