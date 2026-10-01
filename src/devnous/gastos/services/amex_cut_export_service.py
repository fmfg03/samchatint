"""Export an authorized AMEX cut from immutable journal/evidence snapshots."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from ..models import AmexAccountingCut
from .coi_poliza_exporter import ExpenseCFDI, validate_coi_poliza


def cut_expense_cfdis(cut: AmexAccountingCut) -> list[ExpenseCFDI]:
    """Reject malformed evidence before any export instead of returning a 500."""
    try:
        return _cut_expense_cfdis(cut)
    except (KeyError, TypeError, AttributeError, ArithmeticError) as exc:
        raise ValueError("El corte contiene evidencia incompleta o inválida.") from exc


def _cut_expense_cfdis(cut: AmexAccountingCut) -> list[ExpenseCFDI]:
    """Use frozen movements; never recompute from mutable expense classification."""
    snapshot = cut.snapshot_json
    items = snapshot.get("partidas") or []
    actual = snapshot.get("lines") or []
    flattened = [row for item in items for row in item.get("journal_lines", [])]
    if not actual or flattened != actual:
        # Both arrays must cover precisely the same journal. Metadata such as
        # cut_id may be added only to global rows; compare their account/amount.
        def identity(row: dict[str, Any]) -> tuple[str, str, Decimal, Decimal]:
            return (
                str(row["cuenta_contable_id"]),
                row["cuenta_codigo"],
                Decimal(str(row["debe"])),
                Decimal(str(row["haber"])),
            )

        if not actual or [identity(r) for r in flattened] != [
            identity(r) for r in actual
        ]:
            raise ValueError("El corte no contiene un desglose completo de su póliza.")
    result = []
    for item in items:
        rows = item.get("journal_lines") or []
        if not rows:
            continue
        movements = [
            {
                "kind": (row.get("raw_row_json") or {}).get("movement", "corte"),
                "cuenta": row["cuenta_codigo"],
                "concepto": row["concepto"],
                "debe": float(Decimal(str(row["debe"]))),
                "haber": float(Decimal(str(row["haber"]))),
            }
            for row in rows
        ]
        if any(
            not movement["cuenta"] or movement["debe"] < 0 or movement["haber"] < 0
            for movement in movements
        ):
            raise ValueError("El corte contiene movimientos contables inválidos.")
        cfdi = item.get("cfdi") or {}
        cfdi_date = cfdi.get("date")
        result.append(
            ExpenseCFDI(
                fecha=datetime.combine(
                    cast(date, cut.accounting_date), datetime.min.time()
                ),
                total=float(Decimal(str(item["amount"]))),
                iva_amount=0,
                subtotal_amount=0,
                concepto=item.get("concepto") or "Corte AMEX",
                cuenta_contable=movements[0]["cuenta"],
                cuenta_contrapartida=movements[-1]["cuenta"],
                export_reference=item.get("reference") or item["expense_id"],
                cfdi_uuid=cfdi.get("uuid"),
                cfdi_date=datetime.fromisoformat(cfdi_date) if cfdi_date else None,
                rfc_emisor=cfdi.get("rfc_emisor"),
                rfc_receptor=cfdi.get("rfc_receptor"),
                folio=cfdi.get("folio"),
                nombre_emisor=cfdi.get("nombre_emisor"),
                receptor_uso_cfdi=cfdi.get("uso_cfdi"),
                allows_missing_cfdi=True,
                missing_cfdi_warning="El corte conserva la evidencia del cargo AMEX.",
                poliza_group_key=f"amex-cut:{cut.id}",
                poliza_reference=str(cut.id),
                poliza_description=f"Corte AMEX {cut.kind} · {snapshot.get('informe_reference', cut.informe_id)}",
                posting_movements=movements,
            )
        )
    if any(issue["severity"] == "error" for issue in validate_coi_poliza(result)):
        raise ValueError("El corte no cumple el contrato de exportación COI.")
    return result
