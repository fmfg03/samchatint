"""
Finance / accounting training dataset: deterministic generation and cleanup.

Isolation: every run uses a batch_key slug; manifest JSON lists all row IDs for safe deletion.
Origin tag on synthetic employee-path expenses: expense_reports.origen = finance_training
Terceros payment expenses keep production semantics: origen = solicitud_terceros
"""
from __future__ import annotations

import bcrypt
import csv
import json
import logging
import os
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Adjunto,
    Anticipo,
    Aprobacion,
    BudgetConcept,
    CFDIReport,
    CuentaContable,
    CuentaDeGastos,
    Documento,
    Empleado,
    ExpenseReport,
    InvoiceReport,
    ProveedorCliente,
    Reembolso,
    TelegramNotificationOutbox,
    Tournament,
)
from .expense_service import create_expense_from_data

logger = logging.getLogger(__name__)

TRAINING_EXPENSE_ORIGIN = "finance_training"
TRAINING_EMAIL_DOMAIN = "finance-training.sam.chat"
MANIFEST_VERSION = 1
DEFAULT_TRAINING_PASSWORD = os.getenv("FINANCE_TRAINING_DEFAULT_PASSWORD", "FinTrain2026!")


def _hash_training_password(plain: str) -> str:
    password_bytes = plain.encode("utf-8")[:72]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def training_root_dir(repo_root: Path) -> Path:
    return repo_root / "generated" / "finance-training"


def batch_dir(repo_root: Path, batch_key: str) -> Path:
    d = training_root_dir(repo_root) / batch_key
    d.mkdir(parents=True, exist_ok=True)
    return d


def manifest_path(repo_root: Path, batch_key: str) -> Path:
    return batch_dir(repo_root, batch_key) / "manifest.json"


def cfdi_csv_path(repo_root: Path, batch_key: str) -> Path:
    return batch_dir(repo_root, batch_key) / "cfdi_carga_masiva.csv"


def slug_batch_key(raw: Optional[str] = None) -> str:
    if raw and re.match(r"^[a-zA-Z0-9_-]{4,64}$", raw.strip()):
        return raw.strip()
    return uuid.uuid4().hex[:10]


@dataclass
class FinanceTrainingManifest:
    version: int = MANIFEST_VERSION
    batch_key: str = ""
    created_at: str = ""
    tournament_id: Optional[str] = None
    empleado_ids: List[str] = field(default_factory=list)
    proveedor_ids: List[str] = field(default_factory=list)
    cuenta_contable_ids: List[str] = field(default_factory=list)
    cuenta_gastos_ids: List[str] = field(default_factory=list)
    documento_ids: List[str] = field(default_factory=list)
    expense_ids: List[str] = field(default_factory=list)
    anticipo_ids: List[str] = field(default_factory=list)
    reembolso_ids: List[str] = field(default_factory=list)
    aprobacion_ids: List[str] = field(default_factory=list)
    cfdi_uuids: List[str] = field(default_factory=list)
    csv_relative_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "batch_key": self.batch_key,
            "created_at": self.created_at,
            "tournament_id": self.tournament_id,
            "empleado_ids": self.empleado_ids,
            "proveedor_ids": self.proveedor_ids,
            "cuenta_contable_ids": self.cuenta_contable_ids,
            "cuenta_gastos_ids": self.cuenta_gastos_ids,
            "documento_ids": self.documento_ids,
            "expense_ids": self.expense_ids,
            "anticipo_ids": self.anticipo_ids,
            "reembolso_ids": self.reembolso_ids,
            "aprobacion_ids": self.aprobacion_ids,
            "cfdi_uuids": self.cfdi_uuids,
            "csv_relative_path": self.csv_relative_path,
            "default_login_password_hint": "Set via FINANCE_TRAINING_DEFAULT_PASSWORD env or FinTrain2026!",
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FinanceTrainingManifest":
        m = cls()
        m.version = int(data.get("version") or MANIFEST_VERSION)
        m.batch_key = str(data.get("batch_key") or "")
        m.created_at = str(data.get("created_at") or "")
        m.tournament_id = data.get("tournament_id")
        m.empleado_ids = list(data.get("empleado_ids") or [])
        m.proveedor_ids = list(data.get("proveedor_ids") or [])
        m.cuenta_contable_ids = list(data.get("cuenta_contable_ids") or [])
        m.cuenta_gastos_ids = list(data.get("cuenta_gastos_ids") or [])
        m.documento_ids = list(data.get("documento_ids") or [])
        m.expense_ids = list(data.get("expense_ids") or [])
        m.anticipo_ids = list(data.get("anticipo_ids") or [])
        m.reembolso_ids = list(data.get("reembolso_ids") or [])
        m.aprobacion_ids = list(data.get("aprobacion_ids") or [])
        m.cfdi_uuids = list(data.get("cfdi_uuids") or [])
        m.csv_relative_path = str(data.get("csv_relative_path") or "")
        return m


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


CONCEPTOS = [
    "Transporte",
    "Hospedaje",
    "Alimentos",
    "Uniformes",
    "Arbitraje",
    "Material de oficina",
    "Honorarios terceros",
]

PROV_NAMES = [
    ("Servicios Integrales del Norte SA de CV", "SIN850101ABC"),
    ("Papeleria y Suministros MX", "PSM920312XYZ"),
    ("Logistica Express Rodriguez", "LER770606QWE"),
    ("Catering Eventos Garcia", "CEG810808RTY"),
    ("Tecnologia y Redes SL", "TRS631010UIO"),
    ("Limpieza Profesional Sur", "LPS050505ASD"),
    ("Transporte de Carga Perez", "TCP040707FGH"),
    ("Suministros Medicos Centro", "SMC030202JKL"),
    ("Mantenimiento Industrial Leon", "MIL020808ZXC"),
    ("Consultoria Fiscal del Bajio", "CFB011111VBN"),
]


def _rand_rfc_emisor(idx: int) -> Tuple[str, str]:
    bases = [
        ("Hotel Ejemplo SA", "HEM850101ABC"),
        ("Restaurante Prueba SC", "RPS920202DEF"),
        ("Transportes Union SA", "TUS770303GHI"),
        ("Gasolinera Modelo", "GMO630404JKL"),
        ("Farmacia Simulada", "FSI510505MNO"),
    ]
    name, rfc = bases[idx % len(bases)]
    return name, rfc


async def generate_finance_training_dataset(
    session: AsyncSession,
    *,
    repo_root: Path,
    batch_key: Optional[str] = None,
    apply: bool = True,
    force: bool = False,
    seed: int = 42,
    password_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create ~50 coherent expenses + catalogs + documents + CSV for CFDI bulk upload.

    If apply=False, returns dry-run summary without committing.
    """
    random.seed(seed)
    bkey = slug_batch_key(batch_key)
    mpath = manifest_path(repo_root, bkey)
    if mpath.exists() and apply and not force:
        return {
            "ok": False,
            "error": f"Manifest already exists for batch {bkey}. Pass force=true or cleanup first.",
            "batch_key": bkey,
            "manifest_path": str(mpath),
        }

    if apply and force and mpath.exists():
        try:
            mpath.unlink()
        except OSError:
            pass

    manifest = FinanceTrainingManifest(
        batch_key=bkey,
        created_at=_now_iso(),
        csv_relative_path=f"generated/finance-training/{bkey}/cfdi_carga_masiva.csv",
    )

    # --- Plan counts (total expenses = 50)
    plan: Dict[str, Any] = {
        "batch_key": bkey,
        "empleados": 4,
        "proveedores": 10,
        "cuentas_contables": 8,
        "cuentas_gastos": 3,
        "documentos_informe": 3,
        "documentos_solicitud_terceros": 10,
        "documentos_solicitud_personal": 1,
        "gastos_empleado_informe": 40,
        "gastos_terceros_pago": 10,
        "gastos_sin_uuid_cfdi": 6,
        "gastos_con_uuid_cfdi": 44,
    }

    if not apply:
        return {
            "ok": True,
            "mode": "dry_run",
            "batch_key": bkey,
            "plan": plan,
            "message": "Dry run: no database changes.",
        }

    pwd_hash = password_hash or _hash_training_password(DEFAULT_TRAINING_PASSWORD)

    # 1) Tournament
    t_name = f"FINTRAIN {bkey}"
    torneo = Tournament(
        name=t_name,
        description="Dataset de capacitación finanzas — no usar en producción real",
        active=True,
        display_order=9999,
        cuenta_contable_relacionada="5300-010-099",
        etapas=["Colectiva", "Estatal", "Nacional"],
    )
    session.add(torneo)
    await session.flush()
    manifest.tournament_id = str(torneo.id)

    # 2) Empleados: e0 finanzas (approver), e1-e3 empleados
    def email_for(i: int) -> str:
        return f"fintrain.{bkey[:6]}.{i:02d}@{TRAINING_EMAIL_DOMAIN}"

    e0 = Empleado(
        nombre=f"Coordinador Capacitación {bkey[:4]}",
        correo=email_for(0),
        telefono="5555550000",
        departamento="Finanzas",
        rol="finanzas",
        activo=True,
        password_hash=pwd_hash,
        aprobador_id=None,
        proyecto_predeterminado=t_name,
    )
    session.add(e0)
    await session.flush()
    manifest.empleado_ids.append(str(e0.id))

    subordinates = []
    for i in range(1, 4):
        e = Empleado(
            nombre=f"Empleado Capacitación {i}",
            correo=email_for(i),
            telefono=f"555555000{i}",
            departamento=random.choice(["Operaciones", "Mercadotecnia", "Operaciones"]),
            rol="empleado",
            activo=True,
            password_hash=pwd_hash,
            aprobador_id=e0.id,
            proyecto_predeterminado=t_name,
        )
        session.add(e)
        await session.flush()
        manifest.empleado_ids.append(str(e.id))
        subordinates.append(e)

    e1, e2, e3 = subordinates

    # 3) Proveedores
    for idx, (nombre, rfc) in enumerate(PROV_NAMES):
        p = ProveedorCliente(
            tipo="proveedor",
            nombre=f"{nombre} [{bkey[:4]}]",
            rfc=rfc,
            banco="BBVA",
            cuenta_clabe=f"0121800{idx:010d}"[:18],
            cuenta_bancaria=f"NUM_{bkey[:4]}_{idx}",
            activo=True,
        )
        session.add(p)
        await session.flush()
        manifest.proveedor_ids.append(str(p.id))

    # reload proveedores for FK
    prov_result = await session.execute(
        select(ProveedorCliente).where(ProveedorCliente.id.in_(manifest.proveedor_ids))
    )
    proveedores = list(prov_result.scalars().all())

    # 4) Cuentas contables
    cuentas_spec = [
        ("5300-010-001", "Gastos de viaje transporte", "gasto"),
        ("5300-010-002", "Gastos de viaje hospedaje", "gasto"),
        ("5300-010-003", "Gastos de viaje alimentos", "gasto"),
        ("5300-020-001", "Servicios profesionales", "gasto"),
        ("5300-020-002", "Utiles y materiales", "gasto"),
        ("5300-030-001", "Gastos operativos varios", "gasto"),
        ("1190-001-001", "Anticipos a proveedores", "anticipo"),
        ("0210-001-001", "IVA acreditable pendiente", "iva"),
    ]
    cuenta_rows: List[CuentaContable] = []
    for codigo, nombre, tipo in cuentas_spec:
        c = CuentaContable(
            codigo=f"FT-{bkey[:6]}-{codigo}",
            nombre=nombre,
            tipo=tipo,
            activo=True,
        )
        session.add(c)
        await session.flush()
        cuenta_rows.append(c)
        manifest.cuenta_contable_ids.append(str(c.id))

    # 5) Cuentas de gastos + informes (borrador / enviado / aprobado mix)
    cg_specs = [
        (e1, "CG-01", "abierta", "local"),
        (e2, "CG-02", "abierta", "viaje"),
        (e3, "CG-03", "abierta", "local"),
    ]
    cuentas_gasto_objs: List[CuentaDeGastos] = []
    for emp, suf, estado, tipo in cg_specs:
        ref_base = f"FINTRAIN-{bkey}-{suf}"
        cg = CuentaDeGastos(
            empleado_id=emp.id,
            referencia_base=ref_base,
            nombre=f"Cuenta capacitación {suf}",
            estado=estado,
            tipo_cuenta=tipo,
            torneo_id=torneo.id,
            fase="Estatal" if tipo == "viaje" else None,
        )
        session.add(cg)
        await session.flush()
        cuentas_gasto_objs.append(cg)
        manifest.cuenta_gastos_ids.append(str(cg.id))

    informe_states = ["borrador", "enviado", "aprobado"]
    informes: List[Documento] = []
    for i, cg in enumerate(cuentas_gasto_objs):
        st = informe_states[i % len(informe_states)]
        doc = Documento(
            empleado_id=cg.empleado_id,
            tipo="INFORME",
            numero_referencia=f"FINTRAIN-{bkey}-INF-{i+1:02d}",
            estado=st,
            fecha_inicio=datetime.utcnow() - timedelta(days=14),
            fecha_fin=datetime.utcnow() - timedelta(days=1),
            monto_solicitado=None,
            monto_total=Decimal("85000.00"),
            torneo_id=torneo.id,
            proveedor_cliente_id=None,
            beneficiario_empleado_id=None,
            notas=f"INFORME capacitación batch={bkey}",
            cuenta_gastos_id=cg.id,
            referencia_base=cg.referencia_base,
        )
        if st != "borrador":
            doc.enviado_en = datetime.utcnow() - timedelta(days=3)
        if st == "aprobado":
            doc.aprobado_en = datetime.utcnow() - timedelta(days=1)
        session.add(doc)
        await session.flush()
        informes.append(doc)
        manifest.documento_ids.append(str(doc.id))
        if st in ("enviado", "aprobado"):
            ap = Aprobacion(
                tipo_entidad="documento",
                entidad_id=doc.id,
                aprobador_id=e0.id,
                accion="enviar",
                comentario="Capacitación: envío simulado",
                fecha=datetime.utcnow() - timedelta(days=3),
            )
            session.add(ap)
            await session.flush()
            manifest.aprobacion_ids.append(str(ap.id))
        if st == "aprobado":
            ap2 = Aprobacion(
                tipo_entidad="documento",
                entidad_id=doc.id,
                aprobador_id=e0.id,
                accion="aprobar",
                comentario="Capacitación: aprobación simulada",
                fecha=datetime.utcnow() - timedelta(days=1),
            )
            session.add(ap2)
            await session.flush()
            manifest.aprobacion_ids.append(str(ap2.id))
            # Reembolso on first approved informe only (demo)
            if i == 2:
                rem = Reembolso(
                    empleado_id=doc.empleado_id,
                    documento_id=doc.id,
                    monto=Decimal("5000.00"),
                    moneda="MXN",
                    metodo_pago="TRANSFERENCIA",
                    fecha_pago=datetime.utcnow() - timedelta(hours=12),
                    estado="pagado",
                )
                session.add(rem)
                await session.flush()
                manifest.reembolso_ids.append(str(rem.id))

    # 6) SOLICITUD personal + anticipo (empleado e3)
    sol_per = Documento(
        empleado_id=e3.id,
        tipo="SOLICITUD",
        numero_referencia=f"FINTRAIN-{bkey}-SOL-PER-01",
        estado="aprobado",
        fecha_inicio=datetime.utcnow() - timedelta(days=10),
        torneo_id=torneo.id,
        proveedor_cliente_id=None,
        beneficiario_empleado_id=e3.id,
        monto_solicitado=Decimal("15000.00"),
        monto_total=Decimal("15000.00"),
        notas=f"Solicitud personal capacitación batch={bkey}",
        fecha_pago=date.today() - timedelta(days=2),
        concepto_pago="Anticipo de viaticos",
        metodo_pago="TRANSFERENCIA",
        cuenta_gastos_id=cuentas_gasto_objs[2].id,
        referencia_base=cuentas_gasto_objs[2].referencia_base,
        referencia_operaciones=f"OP-{bkey}-PER",
    )
    sol_per.enviado_en = datetime.utcnow() - timedelta(days=5)
    sol_per.aprobado_en = datetime.utcnow() - timedelta(days=3)
    session.add(sol_per)
    await session.flush()
    manifest.documento_ids.append(str(sol_per.id))

    for accion in ("enviar", "aprobar"):
        ap = Aprobacion(
            tipo_entidad="documento",
            entidad_id=sol_per.id,
            aprobador_id=e0.id,
            accion=accion,
            comentario=f"Capacitación solicitud personal {accion}",
            fecha=datetime.utcnow() - timedelta(days=4 if accion == "enviar" else 3),
        )
        session.add(ap)
        await session.flush()
        manifest.aprobacion_ids.append(str(ap.id))

    ant = Anticipo(
        empleado_id=e3.id,
        documento_id=sol_per.id,
        monto=Decimal("8000.00"),
        moneda="MXN",
        fecha_entrega=datetime.utcnow() - timedelta(days=2),
        estado="pendiente",
    )
    session.add(ant)
    await session.flush()
    manifest.anticipo_ids.append(str(ant.id))

    # 7) Employee expenses linked to informes (40 total: 15+15+10)
    counts_per = [15, 15, 10]
    expense_seq = 0
    csv_rows: List[Dict[str, Any]] = []
    sin_uuid_slots = {3, 7, 12, 18, 25, 33}  # 6 expenses without manual UUID

    for cg, informe, n_gastos in zip(cuentas_gasto_objs, informes, counts_per):
        for _ in range(n_gastos):
            expense_seq += 1
            concepto = random.choice(CONCEPTOS)
            monto = round(random.uniform(250.0, 9200.0), 2)
            fecha = datetime.utcnow() - timedelta(days=random.randint(1, 45))
            cfdi_uuid = str(uuid.uuid4()).upper()
            include_uuid = expense_seq not in sin_uuid_slots

            ref_num = f"FINTRAIN-{bkey}-E{expense_seq:04d}"
            er = ExpenseReport(
                empleado_id=cg.empleado_id,
                proyecto=t_name,
                concepto=concepto,
                sub_cuenta=f"{(expense_seq % 900) + 100:03d}",
                gasto_cantidad=monto,
                fecha=fecha,
                tipo_gasto="manual",
                numero_referencia=ref_num,
                estado_factura=None,
                estado_reembolso=random.choice(["pendiente", "pendiente", "aprobado"]),
                cuenta_contable_base=torneo.cuenta_contable_relacionada,
                cuenta_contable_id=None,
                nombre_enviador=next(e.nombre for e in subordinates if e.id == cg.empleado_id),
                departamento=next(e.departamento for e in subordinates if e.id == cg.empleado_id),
                fase_torneo=random.choice(["Colectiva", "Estatal", "No Aplica"]),
                metodo_pago=random.choice(["Tarjeta Personal", "Tarjeta de Empresa", "Efectivo"]),
                origen=TRAINING_EXPENSE_ORIGIN,
                documento_id=informe.id,
                cuenta_gastos_id=cg.id,
                cfdi_uuid_manual=cfdi_uuid if include_uuid else None,
                cfdi_report_id=None,
                solicitud_documento_id=None,
                informe_documento_id=informe.id,
                cfdi_use="G03" if include_uuid else None,
                iva=round(monto * 0.16 / 1.16, 2) if include_uuid and random.random() > 0.3 else None,
            )
            session.add(er)
            await session.flush()
            manifest.expense_ids.append(str(er.id))
            if include_uuid:
                manifest.cfdi_uuids.append(cfdi_uuid)
                emisor_nombre, emisor_rfc = _rand_rfc_emisor(expense_seq)
                # receptor ≈ empresa training RFC generic
                csv_rows.append(
                    {
                        "UUID": cfdi_uuid,
                        "Fecha": fecha.strftime("%Y-%m-%d %H:%M:%S"),
                        "RFC Emisor": emisor_rfc,
                        "Nombre Emisor": emisor_nombre,
                        "RFC Receptor": "ABC850101XYZ",
                        "Nombre Receptor": "Capacitacion Finanzas Demo",
                        "Serie": "FT",
                        "Folio": f"{expense_seq:05d}",
                        "Total": f"{monto:.2f}",
                        "Subtotal": f"{(monto / 1.16):.2f}" if er.iva else f"{monto:.2f}",
                        "IVA": f"{er.iva:.2f}" if er.iva else "",
                        "Moneda": "MXN",
                        "Tipo de Comprobante": "I",
                        "Metodo de Pago": "PUE",
                        "Forma de Pago": "04",
                        "Uso CFDI": "G03",
                        "Descripcion": concepto[:120],
                    }
                )

    # 8) Terceros: 10 SOLICITUD aprobadas + pago → gasto (same as registrar_pago)
    for j in range(10):
        prov = proveedores[j % len(proveedores)]
        monto_sol = round(random.uniform(3000.0, 28000.0), 2)
        doc_sol = Documento(
            empleado_id=e1.id,
            tipo="SOLICITUD",
            numero_referencia=f"FINTRAIN-{bkey}-SOL-TER-{j+1:02d}",
            estado="aprobado",
            torneo_id=torneo.id,
            proveedor_cliente_id=prov.id,
            beneficiario_empleado_id=None,
            monto_solicitado=Decimal(str(monto_sol)),
            monto_total=Decimal(str(monto_sol)),
            notas=f"Solicitud a terceros capacitación batch={bkey}",
            fecha_pago=date.today() - timedelta(days=j + 1),
            concepto_pago=f"Pago proveedor {prov.nombre[:40]}",
            metodo_pago="TRANSFERENCIA",
            numero_factura=f"FAC-FT-{j+1:04d}",
            referencia_pago=f"TRF-{bkey}-{j+1:04d}",
        )
        doc_sol.enviado_en = datetime.utcnow() - timedelta(days=j + 5)
        doc_sol.aprobado_en = datetime.utcnow() - timedelta(days=j + 2)
        session.add(doc_sol)
        await session.flush()
        manifest.documento_ids.append(str(doc_sol.id))

        for accion in ("enviar", "aprobar"):
            ap = Aprobacion(
                tipo_entidad="documento",
                entidad_id=doc_sol.id,
                aprobador_id=e0.id,
                accion=accion,
                comentario=f"Capacitación terceros {accion}",
                fecha=datetime.utcnow() - timedelta(days=j + 4 if accion == "enviar" else j + 2),
            )
            session.add(ap)
            await session.flush()
            manifest.aprobacion_ids.append(str(ap.id))

        proveedor_nombre = prov.nombre
        concepto_pago = doc_sol.concepto_pago or "Pago de solicitud de transferencia"
        concepto_full = f"Pago a proveedor: {proveedor_nombre} - {concepto_pago}"

        expense_seq += 1
        cfdi_uuid = str(uuid.uuid4()).upper()
        include_csv = j < 8  # last 2 terceros sin CFDI uuid for training

        expense = await create_expense_from_data(
            session=session,
            concepto=concepto_full[:100],
            gasto_cantidad=float(
                Decimal(str(monto_sol)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            ),
            fecha=datetime.combine(doc_sol.fecha_pago or date.today(), datetime.min.time()),
            empleado_id=doc_sol.empleado_id,
            proyecto=t_name,
            tipo_gasto="manual",
            nombre_enviador=e1.nombre,
            metodo_pago=doc_sol.metodo_pago or "TRANSFERENCIA",
            origen="solicitud_terceros",
            departamento=e1.departamento or "Operaciones",
            tournament_id=str(torneo.id),
        )
        expense.documento_id = doc_sol.id
        expense.cfdi_uuid_manual = cfdi_uuid if include_csv else None
        expense.cfdi_report_id = None
        expense.cuenta_contable_id = None
        session.add(expense)
        await session.flush()
        manifest.expense_ids.append(str(expense.id))

        doc_sol.estado = "pagado"
        doc_sol.pagado_en = datetime.utcnow()
        doc_sol.gasto_generado_id = expense.id
        session.add(doc_sol)

        # DB check aprobaciones_accion_check has no 'pagar' (only enviar/aprobar/rechazar/cancelar/editar).
        # Use aprobar + comment to mirror registrar-pago audit semantics without violating the constraint.
        ap_pay = Aprobacion(
            tipo_entidad="documento",
            entidad_id=doc_sol.id,
            aprobador_id=e0.id,
            accion="aprobar",
            comentario=(
                f"Capacitación: pago simulado (acción aprobación por constraint BD). "
                f"Gasto {expense.numero_referencia}"
            ),
            fecha=datetime.utcnow(),
        )
        session.add(ap_pay)
        await session.flush()
        manifest.aprobacion_ids.append(str(ap_pay.id))

        if include_csv:
            manifest.cfdi_uuids.append(cfdi_uuid)
            fecha = datetime.combine(doc_sol.fecha_pago or date.today(), datetime.min.time())
            csv_rows.append(
                {
                    "UUID": cfdi_uuid,
                    "Fecha": fecha.strftime("%Y-%m-%d %H:%M:%S"),
                    "RFC Emisor": (prov.rfc or "XAXX010101000")[:20],
                    "Nombre Emisor": proveedor_nombre[:500],
                    "RFC Receptor": "ABC850101XYZ",
                    "Nombre Receptor": "Capacitacion Finanzas Demo",
                    "Serie": "TER",
                    "Folio": f"{j+1:05d}",
                    "Total": f"{monto_sol:.2f}",
                    "Subtotal": f"{(monto_sol / 1.16):.2f}",
                    "IVA": f"{(monto_sol - monto_sol / 1.16):.2f}",
                    "Moneda": "MXN",
                    "Tipo de Comprobante": "I",
                    "Metodo de Pago": "PUE",
                    "Forma de Pago": "03",
                    "Uso CFDI": "G03",
                    "Descripcion": concepto_pago[:120],
                }
            )

    # Write CSV
    csv_abs = cfdi_csv_path(repo_root, bkey)
    fieldnames = [
        "UUID",
        "Fecha",
        "RFC Emisor",
        "Nombre Emisor",
        "RFC Receptor",
        "Nombre Receptor",
        "Serie",
        "Folio",
        "Total",
        "Subtotal",
        "IVA",
        "Moneda",
        "Tipo de Comprobante",
        "Metodo de Pago",
        "Forma de Pago",
        "Uso CFDI",
        "Descripcion",
    ]
    with open(csv_abs, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in csv_rows:
            w.writerow(row)

    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, indent=2, ensure_ascii=False)

    await session.commit()

    return {
        "ok": True,
        "mode": "apply",
        "batch_key": bkey,
        "manifest_path": str(mpath),
        "csv_path": str(csv_abs),
        "counts": {
            "empleados": len(manifest.empleado_ids),
            "proveedores": len(manifest.proveedor_ids),
            "cuentas_contables": len(manifest.cuenta_contable_ids),
            "cuentas_gastos": len(manifest.cuenta_gastos_ids),
            "documentos": len(manifest.documento_ids),
            "expenses": len(manifest.expense_ids),
            "anticipos": len(manifest.anticipo_ids),
            "reembolsos": len(manifest.reembolso_ids),
            "aprobaciones": len(manifest.aprobacion_ids),
            "cfdi_rows_csv": len(csv_rows),
        },
        "plan": plan,
    }


async def clear_training_expense_cuenta_contable(
    session: AsyncSession,
    *,
    repo_root: Path,
    batch_key: str,
    apply: bool = True,
) -> Dict[str, Any]:
    """
    Set cuenta_contable_id to NULL for every expense id listed in the batch manifest.

    Use after older seeds that assigned synthetic cuentas contables to expenses, so training
    gastos behave like \"sin cuenta contable\" while keeping catalog rows in manifest.
    """
    bkey = (batch_key or "").strip()
    if not bkey:
        return {"ok": False, "error": "batch_key required", "batch_key": ""}
    mpath = manifest_path(repo_root, bkey)
    if not mpath.exists():
        return {
            "ok": False,
            "error": f"No manifest at {mpath}",
            "batch_key": bkey,
        }
    with open(mpath, "r", encoding="utf-8") as f:
        raw = json.load(f)
    m = FinanceTrainingManifest.from_dict(raw)

    def _uu(ids: List[str]) -> List[uuid.UUID]:
        return [uuid.UUID(x) for x in ids if x]

    eids = _uu(m.expense_ids)
    if not eids:
        return {
            "ok": True,
            "mode": "dry_run" if not apply else "apply",
            "batch_key": bkey,
            "expense_ids_in_manifest": 0,
            "updated": 0,
        }

    if not apply:
        cnt_result = await session.execute(
            select(func.count())
            .select_from(ExpenseReport)
            .where(
                ExpenseReport.id.in_(eids),
                ExpenseReport.cuenta_contable_id.isnot(None),
            )
        )
        pending = int(cnt_result.scalar_one() or 0)
        return {
            "ok": True,
            "mode": "dry_run",
            "batch_key": bkey,
            "expense_ids_in_manifest": len(eids),
            "would_clear_cuenta_contable_on": pending,
        }

    result = await session.execute(
        update(ExpenseReport)
        .where(ExpenseReport.id.in_(eids))
        .values(cuenta_contable_id=None)
    )
    await session.commit()
    return {
        "ok": True,
        "mode": "apply",
        "batch_key": bkey,
        "expense_ids_in_manifest": len(eids),
        "rows_matched": int(result.rowcount or 0),
    }


async def cleanup_finance_training_dataset(
    session: AsyncSession,
    *,
    repo_root: Path,
    batch_key: str,
    apply: bool = True,
) -> Dict[str, Any]:
    """Delete all entities recorded in manifest for batch_key."""
    mpath = manifest_path(repo_root, batch_key)
    if not mpath.exists():
        return {
            "ok": False,
            "error": f"No manifest at {mpath}. Nothing to delete.",
            "batch_key": batch_key,
        }
    with open(mpath, "r", encoding="utf-8") as f:
        raw = json.load(f)
    m = FinanceTrainingManifest.from_dict(raw)

    counts = {k: len(v) for k, v in {
        "aprobacion_ids": m.aprobacion_ids,
        "anticipo_ids": m.anticipo_ids,
        "reembolso_ids": m.reembolso_ids,
        "expense_ids": m.expense_ids,
        "documento_ids": m.documento_ids,
        "cuenta_gastos_ids": m.cuenta_gastos_ids,
        "proveedor_ids": m.proveedor_ids,
        "cuenta_contable_ids": m.cuenta_contable_ids,
        "empleado_ids": m.empleado_ids,
    }.items()}

    if not apply:
        return {
            "ok": True,
            "mode": "dry_run",
            "batch_key": batch_key,
            "would_delete": counts,
        }

    def uuids(ids: List[str]) -> List[uuid.UUID]:
        return [uuid.UUID(x) for x in ids if x]

    # Adjuntos
    adj_conds = []
    if m.expense_ids:
        adj_conds.append(Adjunto.gasto_id.in_(uuids(m.expense_ids)))
    if m.documento_ids:
        adj_conds.append(Adjunto.documento_id.in_(uuids(m.documento_ids)))
    if adj_conds:
        await session.execute(delete(Adjunto).where(or_(*adj_conds)))

    # Invoice reports
    if m.expense_ids:
        await session.execute(
            delete(InvoiceReport).where(InvoiceReport.expense_id.in_(uuids(m.expense_ids)))
        )

    # CFDI reports by UUID from training (including user-uploaded CSV after training)
    if m.cfdi_uuids:
        await session.execute(
            delete(CFDIReport).where(
                CFDIReport.cfdi_uuid.in_([u.upper() for u in m.cfdi_uuids])
            )
        )

    # Clear documento.gasto_generado_id
    if m.documento_ids:
        await session.execute(
            update(Documento)
            .where(Documento.id.in_(uuids(m.documento_ids)))
            .values(gasto_generado_id=None)
        )

    # Expenses may reference cfdi_report — already deleted or null
    if m.expense_ids:
        await session.execute(
            update(ExpenseReport)
            .where(ExpenseReport.id.in_(uuids(m.expense_ids)))
            .values(
                cfdi_report_id=None,
                documento_id=None,
                solicitud_documento_id=None,
                informe_documento_id=None,
                cuenta_gastos_id=None,
                cuenta_contable_id=None,
            )
        )
        await session.execute(
            delete(ExpenseReport).where(ExpenseReport.id.in_(uuids(m.expense_ids)))
        )

    # Aprobaciones tied to doc/expense ids
    if m.aprobacion_ids:
        await session.execute(
            delete(Aprobacion).where(Aprobacion.id.in_(uuids(m.aprobacion_ids)))
        )
    # Extra safety: any remaining aprobaciones on those entities
    if m.documento_ids:
        await session.execute(
            delete(Aprobacion).where(
                and_(
                    Aprobacion.tipo_entidad == "documento",
                    Aprobacion.entidad_id.in_(uuids(m.documento_ids)),
                )
            )
        )

    if m.anticipo_ids:
        await session.execute(
            delete(Anticipo).where(Anticipo.id.in_(uuids(m.anticipo_ids)))
        )
    if m.reembolso_ids:
        await session.execute(
            delete(Reembolso).where(Reembolso.id.in_(uuids(m.reembolso_ids)))
        )

    if m.documento_ids:
        await session.execute(
            delete(Documento).where(Documento.id.in_(uuids(m.documento_ids)))
        )

    if m.cuenta_gastos_ids:
        await session.execute(
            delete(CuentaDeGastos).where(CuentaDeGastos.id.in_(uuids(m.cuenta_gastos_ids)))
        )

    if m.tournament_id:
        await session.execute(
            delete(Tournament).where(Tournament.id == uuid.UUID(m.tournament_id))
        )

    if m.proveedor_ids:
        await session.execute(
            delete(ProveedorCliente).where(ProveedorCliente.id.in_(uuids(m.proveedor_ids)))
        )

    if m.cuenta_contable_ids:
        await session.execute(
            delete(CuentaContable).where(CuentaContable.id.in_(uuids(m.cuenta_contable_ids)))
        )

    # Empleados: clear aprobador chain
    if m.empleado_ids:
        euu = uuids(m.empleado_ids)
        await session.execute(
            update(Empleado).where(Empleado.aprobador_id.in_(euu)).values(aprobador_id=None)
        )
        await session.execute(
            update(Empleado).where(Empleado.id.in_(euu)).values(aprobador_id=None)
        )
        await session.execute(delete(Empleado).where(Empleado.id.in_(euu)))

    await session.commit()

    # Remove manifest + csv
    try:
        csv_abs = cfdi_csv_path(repo_root, batch_key)
        if csv_abs.exists():
            csv_abs.unlink()
        if mpath.exists():
            mpath.unlink()
        bdir = batch_dir(repo_root, batch_key)
        if bdir.exists() and not any(bdir.iterdir()):
            bdir.rmdir()
    except OSError as exc:
        logger.warning("Post-cleanup file removal: %s", exc)

    return {
        "ok": True,
        "mode": "apply",
        "batch_key": batch_key,
        "deleted": counts,
    }


E2E_EXPENSE_ORIGIN = "e2e_flow_test"
TRAINING_PROVEEDOR_SUFFIX = " [e2ef]"
TRAINING_E2E_BANK_RFC = "E2EB010101ABC"


async def _discover_finance_training_and_e2e_ids(
    session: AsyncSession,
) -> Dict[str, List[uuid.UUID]]:
    """Collect IDs for finance-training / E2E synthetic data by stable patterns."""
    training_empleado_ids = list(
        (
            await session.execute(
                select(Empleado.id).where(
                    Empleado.correo.like(f"%@{TRAINING_EMAIL_DOMAIN}")
                )
            )
        ).scalars()
    )
    fintrain_tournament_ids = list(
        (
            await session.execute(
                select(Tournament.id).where(Tournament.name.like("FINTRAIN%"))
            )
        ).scalars()
    )

    cuenta_gastos_ids: List[uuid.UUID] = []
    if training_empleado_ids or fintrain_tournament_ids:
        cuenta_conds = []
        if training_empleado_ids:
            cuenta_conds.append(CuentaDeGastos.empleado_id.in_(training_empleado_ids))
        if fintrain_tournament_ids:
            cuenta_conds.append(CuentaDeGastos.torneo_id.in_(fintrain_tournament_ids))
        cuenta_conds.append(CuentaDeGastos.referencia_base.like("E2E%"))
        cuenta_conds.append(CuentaDeGastos.referencia_base.like("FINTRAIN-%"))
        cuenta_gastos_ids = list(
            (await session.execute(select(CuentaDeGastos.id).where(or_(*cuenta_conds))))
            .scalars()
        )

    documento_ids: List[uuid.UUID] = []
    doc_conds = []
    if training_empleado_ids:
        doc_conds.append(Documento.empleado_id.in_(training_empleado_ids))
    if fintrain_tournament_ids:
        doc_conds.append(Documento.torneo_id.in_(fintrain_tournament_ids))
    if cuenta_gastos_ids:
        doc_conds.append(Documento.cuenta_gastos_id.in_(cuenta_gastos_ids))
    if doc_conds:
        documento_ids = list(
            (await session.execute(select(Documento.id).where(or_(*doc_conds))))
            .scalars()
        )

    expense_ids: List[uuid.UUID] = []
    exp_conds = [
        ExpenseReport.origen.in_((TRAINING_EXPENSE_ORIGIN, E2E_EXPENSE_ORIGIN)),
    ]
    if training_empleado_ids:
        exp_conds.append(ExpenseReport.empleado_id.in_(training_empleado_ids))
    if documento_ids:
        exp_conds.extend(
            [
                ExpenseReport.documento_id.in_(documento_ids),
                ExpenseReport.solicitud_documento_id.in_(documento_ids),
                ExpenseReport.informe_documento_id.in_(documento_ids),
            ]
        )
    if cuenta_gastos_ids:
        exp_conds.append(ExpenseReport.cuenta_gastos_id.in_(cuenta_gastos_ids))
    expense_ids = list(
        (await session.execute(select(ExpenseReport.id).where(or_(*exp_conds))))
        .scalars()
    )

    gasto_generado_ids: List[uuid.UUID] = []
    if documento_ids:
        gasto_generado_ids = list(
            (
                await session.execute(
                    select(Documento.gasto_generado_id).where(
                        Documento.id.in_(documento_ids),
                        Documento.gasto_generado_id.isnot(None),
                    )
                )
            ).scalars()
        )
        expense_ids = list(set(expense_ids) | set(gasto_generado_ids))

    aprobacion_ids: List[uuid.UUID] = []
    if documento_ids or expense_ids:
        ap_conds = []
        if documento_ids:
            ap_conds.append(
                and_(
                    Aprobacion.tipo_entidad == "documento",
                    Aprobacion.entidad_id.in_(documento_ids),
                )
            )
        if expense_ids:
            ap_conds.append(
                and_(
                    Aprobacion.tipo_entidad == "gasto",
                    Aprobacion.entidad_id.in_(expense_ids),
                )
            )
        aprobacion_ids = list(
            (
                await session.execute(
                    select(Aprobacion.id).where(or_(*ap_conds))
                )
            ).scalars()
        )

    anticipo_ids: List[uuid.UUID] = []
    if documento_ids:
        anticipo_ids = list(
            (
                await session.execute(
                    select(Anticipo.id).where(
                        Anticipo.documento_id.in_(documento_ids)
                    )
                )
            ).scalars()
        )

    reembolso_ids: List[uuid.UUID] = []
    if documento_ids or cuenta_gastos_ids or training_empleado_ids:
        rem_conds = []
        if documento_ids:
            rem_conds.append(Reembolso.documento_id.in_(documento_ids))
        if cuenta_gastos_ids:
            rem_conds.append(Reembolso.cuenta_gastos_id.in_(cuenta_gastos_ids))
        if training_empleado_ids:
            rem_conds.append(Reembolso.empleado_id.in_(training_empleado_ids))
        reembolso_ids = list(
            (
                await session.execute(
                    select(Reembolso.id).where(or_(*rem_conds))
                )
            ).scalars()
        )

    proveedor_ids = list(
        (
            await session.execute(
                select(ProveedorCliente.id).where(
                    or_(
                        ProveedorCliente.nombre.like(f"%{TRAINING_PROVEEDOR_SUFFIX}"),
                        ProveedorCliente.rfc == TRAINING_E2E_BANK_RFC,
                    )
                )
            )
        ).scalars()
    )

    cuenta_contable_ids = list(
        (
            await session.execute(
                select(CuentaContable.id).where(CuentaContable.codigo.like("FT-%"))
            )
        ).scalars()
    )

    budget_concept_ids: List[uuid.UUID] = []
    bc_conds = [BudgetConcept.source == "e2e_seed"]
    if fintrain_tournament_ids:
        bc_conds.append(BudgetConcept.tournament_id.in_(fintrain_tournament_ids))
    budget_concept_ids = list(
        (
            await session.execute(
                select(BudgetConcept.id).where(or_(*bc_conds))
            )
        ).scalars()
    )

    cfdi_report_ids = list(
        (
            await session.execute(
                select(CFDIReport.id).where(
                    CFDIReport.id.in_(
                        select(ExpenseReport.cfdi_report_id).where(
                            ExpenseReport.id.in_(expense_ids),
                            ExpenseReport.cfdi_report_id.isnot(None),
                        )
                    )
                )
            )
        ).scalars()
    ) if expense_ids else []

    return {
        "empleado_ids": training_empleado_ids,
        "tournament_ids": fintrain_tournament_ids,
        "cuenta_gastos_ids": cuenta_gastos_ids,
        "documento_ids": documento_ids,
        "expense_ids": expense_ids,
        "aprobacion_ids": aprobacion_ids,
        "anticipo_ids": anticipo_ids,
        "reembolso_ids": reembolso_ids,
        "proveedor_ids": proveedor_ids,
        "cuenta_contable_ids": cuenta_contable_ids,
        "budget_concept_ids": budget_concept_ids,
        "cfdi_report_ids": cfdi_report_ids,
    }


async def _delete_finance_training_id_sets(
    session: AsyncSession,
    ids: Dict[str, List[uuid.UUID]],
) -> None:
    """Delete discovered finance-training / E2E rows in FK-safe order."""
    documento_ids = ids.get("documento_ids") or []
    expense_ids = ids.get("expense_ids") or []
    empleado_ids = ids.get("empleado_ids") or []

    outbox_conds = []
    if documento_ids:
        outbox_conds.append(
            TelegramNotificationOutbox.documento_id.in_(documento_ids)
        )
    if empleado_ids:
        outbox_conds.append(
            TelegramNotificationOutbox.recipient_empleado_id.in_(empleado_ids)
        )
    if outbox_conds:
        await session.execute(
            delete(TelegramNotificationOutbox).where(or_(*outbox_conds))
        )

    adj_conds = []
    if expense_ids:
        adj_conds.append(Adjunto.gasto_id.in_(expense_ids))
    if documento_ids:
        adj_conds.append(Adjunto.documento_id.in_(documento_ids))
    if adj_conds:
        await session.execute(delete(Adjunto).where(or_(*adj_conds)))

    if expense_ids:
        await session.execute(
            delete(InvoiceReport).where(InvoiceReport.expense_id.in_(expense_ids))
        )

    cfdi_report_ids = ids.get("cfdi_report_ids") or []
    if cfdi_report_ids:
        await session.execute(
            delete(CFDIReport).where(CFDIReport.id.in_(cfdi_report_ids))
        )

    if documento_ids:
        await session.execute(
            update(Documento)
            .where(Documento.id.in_(documento_ids))
            .values(gasto_generado_id=None)
        )

    if expense_ids:
        await session.execute(
            update(ExpenseReport)
            .where(ExpenseReport.id.in_(expense_ids))
            .values(
                cfdi_report_id=None,
                documento_id=None,
                solicitud_documento_id=None,
                informe_documento_id=None,
                cuenta_gastos_id=None,
                cuenta_contable_id=None,
            )
        )
        await session.execute(
            delete(ExpenseReport).where(ExpenseReport.id.in_(expense_ids))
        )

    aprobacion_ids = ids.get("aprobacion_ids") or []
    if aprobacion_ids:
        await session.execute(
            delete(Aprobacion).where(Aprobacion.id.in_(aprobacion_ids))
        )

    anticipo_ids = ids.get("anticipo_ids") or []
    if anticipo_ids:
        await session.execute(delete(Anticipo).where(Anticipo.id.in_(anticipo_ids)))

    reembolso_ids = ids.get("reembolso_ids") or []
    if reembolso_ids:
        await session.execute(
            delete(Adjunto).where(Adjunto.reembolso_id.in_(reembolso_ids))
        )
        await session.execute(
            delete(Reembolso).where(Reembolso.id.in_(reembolso_ids))
        )

    if documento_ids:
        await session.execute(
            delete(Documento).where(Documento.id.in_(documento_ids))
        )

    cuenta_gastos_ids = ids.get("cuenta_gastos_ids") or []
    if cuenta_gastos_ids:
        await session.execute(
            delete(CuentaDeGastos).where(CuentaDeGastos.id.in_(cuenta_gastos_ids))
        )

    budget_concept_ids = ids.get("budget_concept_ids") or []
    if budget_concept_ids:
        await session.execute(
            delete(BudgetConcept).where(BudgetConcept.id.in_(budget_concept_ids))
        )

    tournament_ids = ids.get("tournament_ids") or []
    if tournament_ids:
        await session.execute(
            delete(Tournament).where(Tournament.id.in_(tournament_ids))
        )

    proveedor_ids = ids.get("proveedor_ids") or []
    if proveedor_ids:
        await session.execute(
            delete(ProveedorCliente).where(ProveedorCliente.id.in_(proveedor_ids))
        )

    cuenta_contable_ids = ids.get("cuenta_contable_ids") or []
    if cuenta_contable_ids:
        await session.execute(
            delete(CuentaContable).where(CuentaContable.id.in_(cuenta_contable_ids))
        )

    if empleado_ids:
        await session.execute(
            update(Empleado)
            .where(Empleado.aprobador_id.in_(empleado_ids))
            .values(aprobador_id=None)
        )
        await session.execute(
            update(Empleado)
            .where(Empleado.id.in_(empleado_ids))
            .values(aprobador_id=None)
        )
        await session.execute(delete(Empleado).where(Empleado.id.in_(empleado_ids)))


async def cleanup_all_finance_training_and_e2e_data(
    session: AsyncSession,
    *,
    repo_root: Path,
    apply: bool = True,
    remove_manifests: bool = True,
) -> Dict[str, Any]:
    """Remove all finance-training / E2E synthetic data discovered by stable patterns."""
    ids = await _discover_finance_training_and_e2e_ids(session)
    counts = {k: len(v) for k, v in ids.items()}

    if not apply:
        return {"ok": True, "mode": "dry_run", "would_delete": counts}

    if not any(counts.values()):
        return {"ok": True, "mode": "apply", "deleted": counts, "note": "nothing_found"}

    await _delete_finance_training_id_sets(session, ids)
    await session.commit()

    if remove_manifests:
        training_root = training_root_dir(repo_root)
        if training_root.exists():
            for mpath in training_root.glob("*/manifest.json"):
                batch_key = mpath.parent.name
                try:
                    csv_abs = cfdi_csv_path(repo_root, batch_key)
                    if csv_abs.exists():
                        csv_abs.unlink()
                    mpath.unlink()
                    bdir = mpath.parent
                    if bdir.exists() and not any(bdir.iterdir()):
                        bdir.rmdir()
                except OSError as exc:
                    logger.warning("Post-cleanup manifest removal for %s: %s", batch_key, exc)

        for artifact in (
            repo_root / "generated" / "e2e-gastos-context.json",
            repo_root / "generated" / "e2e-gastos-report.json",
        ):
            try:
                if artifact.exists():
                    artifact.unlink()
            except OSError as exc:
                logger.warning("Post-cleanup artifact removal %s: %s", artifact, exc)

    return {"ok": True, "mode": "apply", "deleted": counts}


async def reset_finance_training_dataset(
    session: AsyncSession,
    *,
    repo_root: Path,
    batch_key: Optional[str] = None,
    apply: bool = True,
    seed: int = 42,
) -> Dict[str, Any]:
    """Cleanup existing manifest for batch_key if present, then generate fresh."""
    bkey = slug_batch_key(batch_key)
    mpath = manifest_path(repo_root, bkey)
    if mpath.exists():
        cu = await cleanup_finance_training_dataset(
            session, repo_root=repo_root, batch_key=bkey, apply=apply
        )
        if not apply:
            return cu
        if not cu.get("ok"):
            return cu
    return await generate_finance_training_dataset(
        session,
        repo_root=repo_root,
        batch_key=bkey,
        apply=apply,
        force=True,
        seed=seed,
    )
