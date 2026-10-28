"""Canonical intake owner for payroll source amounts, with explicit confirmation."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload, selectinload

from ..models import (
    CuentaContable,
    PayrollAmountImport,
    PayrollAmountImportLine,
    PayrollEmployee,
    PayrollEmployer,
    PayrollPeriod,
)
from .payroll_amount_workbook_parser import (
    PARSER_VERSION,
    ParsedPayrollAmounts,
    digest,
    normalized,
)


async def build_payroll_amount_preview(
    session: AsyncSession,
    parsed: ParsedPayrollAmounts,
    *,
    employer_id: UUID,
    period_id: UUID,
) -> dict[str, Any]:
    """Resolve exact identities and accounts without persisting any business record."""
    errors = list(parsed.errors)
    warnings = list(parsed.warnings)
    employer = await session.get(
        PayrollEmployer, employer_id, options=[raiseload("*")], populate_existing=True
    )
    period = await session.get(PayrollPeriod, period_id, populate_existing=True)
    if employer is None or not employer.active:
        errors.append("Seleccione un empleador activo existente.")
    if period is None or period.status not in {"draft", "open", "calculated"}:
        errors.append("El período no existe o no admite cargas.")
    codes = {row["employee_code"] for row in parsed.rows}
    employees = (
        (
            await session.execute(
                select(PayrollEmployee)
                .options(
                    raiseload("*"),
                    selectinload(PayrollEmployee.employer_registration).raiseload("*"),
                    selectinload(PayrollEmployee.empleado).raiseload("*"),
                )
                .where(PayrollEmployee.employee_number.in_(codes))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    by_code = {employee.employee_number: employee for employee in employees}
    account_codes = {
        c["account_code"]
        for row in parsed.rows
        for c in row["components"]
        if c["account_code"]
    }
    accounts = (
        (
            await session.execute(
                select(CuentaContable)
                .where(CuentaContable.codigo.in_(account_codes))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    by_account = {account.codigo: account for account in accounts if account.activo}
    rows = copy.deepcopy(parsed.rows)
    pending = 0
    for row in rows:
        row_no = row["source_row"]
        employee = by_code.get(row["employee_code"])
        registration = employee.employer_registration if employee else None
        valid_employee = (
            employee is not None
            and employee.active
            and employee.empleado is not None
            and employee.empleado.activo
            and registration is not None
            and registration.active
            and registration.payroll_employer_id == employer_id
        )
        if not valid_employee:
            errors.append(
                f"Fila {row_no}: empleado no encontrado, inactivo "
                "o ajeno al empleador/período."
            )
        row["payroll_employee_id"] = str(employee.id) if valid_employee else None
        if valid_employee and normalized(employee.empleado.nombre) != normalized(
            row["source_name"]
        ):
            warnings.append(
                f"Fila {row_no}: el nombre difiere del empleado resuelto por clave."
            )
        for component in row["components"]:
            account = by_account.get(component["account_code"])
            component["account_id"] = str(account.id) if account else None
            component["pending_mapping"] = False
            if component["account_code"] and account is None:
                errors.append(
                    f"Fila {row_no}, {component['label']}: "
                    "cuenta inexistente o inactiva."
                )
            elif not account and Decimal(component["amount"]):
                if component["concept_key"] in {"bonus", "subsidy", "compensation"}:
                    component["pending_mapping"] = True
                    pending += 1
                else:
                    errors.append(
                        f"Fila {row_no}, {component['label']}: falta cuenta de destino."
                    )
    totals = {
        key: str(sum((Decimal(row[key]) for row in rows), Decimal("0.00")))
        for key in ("gross_total", "deductions_total", "net_total")
    }
    if any(
        abs(Decimal(value)) >= Decimal("10000000000000000") for value in totals.values()
    ):
        errors.append("Los totales superan el límite permitido.")
    preview = {
        "employer_id": str(employer_id),
        "period_id": str(period_id),
        "employer_label": employer.legal_name if employer else "—",
        "period_label": f"{period.start_date} a {period.end_date}" if period else "—",
        "file_sha256": parsed.file_sha256,
        "content_sha256": parsed.content_sha256,
        "parser_version": PARSER_VERSION,
        "source_title": parsed.source_title,
        "rows": rows,
        "errors": errors,
        "warnings": warnings,
        "pending_count": pending,
        "totals": totals,
        "status": "pending_mapping" if pending else "validated",
    }
    preview["plan_sha256"] = digest(preview)
    return preview


async def confirm_payroll_amount_import(
    session: AsyncSession,
    parsed: ParsedPayrollAmounts,
    *,
    employer_id: UUID,
    period_id: UUID,
    actor_id: UUID,
    expected_plan_sha256: str,
) -> UUID:
    """Persist a revalidated source-only import; retries return its receipt."""
    try:
        # All intake writers lock the same existing period, including disjoint uploads.
        locked_period = await session.execute(
            select(PayrollPeriod)
            .where(PayrollPeriod.id == period_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        locked_period.scalar_one_or_none()
        preview = await build_payroll_amount_preview(
            session, parsed, employer_id=employer_id, period_id=period_id
        )
        if preview["errors"]:
            raise ValueError("La carga contiene errores; revise la vista previa.")
        if preview["plan_sha256"] != expected_plan_sha256:
            raise ValueError(
                "El archivo o su resolución cambiaron; genere otra vista previa."
            )
        existing = (
            (
                await session.execute(
                    select(PayrollAmountImport).where(
                        PayrollAmountImport.period_id == period_id,
                        PayrollAmountImport.payroll_employer_id == employer_id,
                        or_(
                            PayrollAmountImport.file_sha256 == parsed.file_sha256,
                            PayrollAmountImport.content_sha256 == parsed.content_sha256,
                        ),
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing:
            existing_id = existing.id
            await session.rollback()
            return existing_id
        employee_ids = [UUID(row["payroll_employee_id"]) for row in preview["rows"]]
        conflict = await session.scalar(
            select(PayrollAmountImportLine.id)
            .join(
                PayrollAmountImport,
                PayrollAmountImport.id == PayrollAmountImportLine.import_id,
            )
            .where(
                PayrollAmountImport.period_id == period_id,
                PayrollAmountImport.payroll_employer_id == employer_id,
                PayrollAmountImportLine.payroll_employee_id.in_(employee_ids),
            )
            .limit(1)
        )
        if conflict:
            raise ValueError(
                "Ya existe una carga para algún empleado de este empleador y período."
            )
        batch = PayrollAmountImport(
            id=uuid4(),
            period_id=period_id,
            payroll_employer_id=employer_id,
            created_by_empleado_id=actor_id,
            file_sha256=parsed.file_sha256,
            content_sha256=parsed.content_sha256,
            parser_version=PARSER_VERSION,
            status=preview["status"],
            **{key: Decimal(value) for key, value in preview["totals"].items()},
            metadata_json={
                "source_title": parsed.source_title,
                "line_count": len(preview["rows"]),
                "pending_count": preview["pending_count"],
                "warnings": preview["warnings"],
                "plan_sha256": preview["plan_sha256"],
            },
        )
        session.add(batch)
        await session.flush()
        for row in preview["rows"]:
            session.add(
                PayrollAmountImportLine(
                    id=uuid4(),
                    import_id=batch.id,
                    payroll_employee_id=UUID(row["payroll_employee_id"]),
                    source_row=row["source_row"],
                    employee_code=row["employee_code"],
                    source_name=row["source_name"],
                    days_paid=Decimal(row["days_paid"]),
                    daily_salary=(
                        Decimal(row["daily_salary"])
                        if row["daily_salary"] is not None
                        else None
                    ),
                    gross_total=Decimal(row["gross_total"]),
                    deductions_total=Decimal(row["deductions_total"]),
                    net_total=Decimal(row["net_total"]),
                    source_json=row,
                )
            )
        batch_id = batch.id
        await session.commit()
        return batch_id
    except Exception:
        await session.rollback()
        raise
