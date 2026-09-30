"""Excel export for the unified executive center."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

_HEADER_FILL = PatternFill("solid", fgColor="0F766E")
_WHITE_FONT = Font(color="FFFFFF", bold=True)
_BOLD_FONT = Font(bold=True)


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_text(value: Any) -> str:
    return "" if value is None else str(value)


def _write_table(
    ws,
    headers: list[str],
    rows: list[list[Any]],
    *,
    start_row: int = 1,
) -> int:
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=start_row, column=col_idx, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _WHITE_FONT
        cell.alignment = Alignment(horizontal="center")
    for row_idx, row in enumerate(rows, start=start_row + 1):
        for col_idx, value in enumerate(row, start=1):
            ws.cell(row=row_idx, column=col_idx, value=value)
    for col_idx, _header in enumerate(headers, start=1):
        max_len = max(
            len(_safe_text(ws.cell(row=row, column=col_idx).value))
            for row in range(start_row, start_row + len(rows) + 1)
        )
        ws.column_dimensions[get_column_letter(col_idx)].width = min(
            max(max_len + 2, 12),
            55,
        )
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1).coordinate
    return start_row + len(rows) + 2


def generate_direction_report_xlsx(report: dict[str, Any]) -> bytes:
    """Export the exact Direction cut, preserving missing facts as empty cells."""
    from openpyxl.chart import BarChart, Reference

    from samchat.client_executive.home import amount

    def money(value):
        parsed = amount(value)
        return float(parsed) if parsed is not None else None

    wb = Workbook()
    ws = wb.active
    ws.title = "Consejo"
    for row in (
        [report["title"]],
        [report["scope"]],
        [f"Periodo: {report['period']['start']} a {report['period']['end']}"],
        [f"Corte: {report['cut']}"],
        [f"Identificador de cifras: {report['snapshot_id']}"],
        [report["conclusion"]],
        [report["validation"]],
    ):
        ws.append(row)
    ws["A1"].font = Font(size=20, bold=True, color="143548")
    for row in range(1, 8):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    end = _write_table(
        ws,
        ["Indicador", "MXN", "Cobertura", "Periodo", "Definición", "Datos faltantes"],
        [
            [
                m["label"],
                money(m["value"]),
                f"{m['coverage']['covered']}/{m['coverage']['total']} torneos",
                m["period"],
                m["definition"],
                "; ".join(m["gaps"]),
            ]
            for m in report["indicators"]
        ],
        start_row=9,
    )
    for advice in report["recommendations"]:
        for title, key in (
            ("Asunto", "title"),
            ("Evidencia", "evidence"),
            ("Recomendación", "recommendation"),
            ("Alternativa", "alternative"),
            ("Riesgo", "risk"),
            ("Siguiente paso", "next_step"),
        ):
            ws.cell(end, 1, title)
            ws.cell(end, 2, advice[key])
            ws.merge_cells(start_row=end, start_column=2, end_row=end, end_column=6)
            end += 1
    ws.cell(end + 1, 1, report["method"])
    ws.merge_cells(start_row=end + 1, start_column=1, end_row=end + 1, end_column=6)
    backing = wb.create_sheet("Respaldo")
    headers = [
        "Torneo",
        "Indicador",
        "MXN",
        "Fuente",
        "Definición",
        "Corte",
        "Referencia",
        "Fecha",
    ]
    rows = []
    for tournament in report["tournaments"]:
        for metric in report["indicators"]:
            rows.append(
                [
                    tournament["name"],
                    metric["label"],
                    money(tournament["values"].get(metric["id"])),
                    metric["source"],
                    metric["definition"],
                    tournament.get("as_of") or "Sin corte individual acreditado",
                    "",
                    metric["period"],
                ]
            )
        for item in tournament.get("payment_evidence", []):
            rows.append(
                [
                    tournament["name"],
                    "Obligación programada",
                    money(item["value"]),
                    "Solicitud elegible · importe pagable",
                    "Sin pago documental registrado",
                    report["cut"],
                    item.get("reference") or "Sin referencia",
                    item["date"],
                ]
            )
        for item in (tournament.get("concepts") or {}).get("rows", []):
            rows.append(
                [
                    tournament["name"],
                    item["label"],
                    money(item["actual"]),
                    "Gastos por concepto · desglose conciliado",
                    f"Presupuesto anual: {item['budget']} MXN",
                    report["cut"],
                    "",
                    "Intervalo del tablero",
                ]
            )
    _write_table(backing, headers, rows)
    comparison = wb.create_sheet("Comparación")
    _write_table(
        comparison,
        ["Torneo", "Presupuesto anual MXN", "Ejercido del intervalo MXN"],
        [
            [
                t["name"],
                money(t["values"].get("budget")),
                money(t["values"].get("actual")),
            ]
            for t in report["tournaments"]
        ],
    )
    if report["tournaments"]:
        chart = BarChart()
        chart.title = "Ejercido del intervalo frente a presupuesto anual"
        chart.y_axis.title = "MXN"
        chart.height = 10
        chart.width = 22
        chart.add_data(
            Reference(
                comparison,
                min_col=2,
                max_col=3,
                min_row=1,
                max_row=len(report["tournaments"]) + 1,
            ),
            titles_from_data=True,
        )
        chart.set_categories(
            Reference(
                comparison, min_col=1, min_row=2, max_row=len(report["tournaments"]) + 1
            )
        )
        comparison.add_chart(chart, "E2")
    scenario = report.get("scenario")
    if scenario:
        case = wb.create_sheet("Escenario")
        for row in (
            [scenario["title"]],
            ["Hipotético; los valores reales permanecen en Consejo y Respaldo."],
            [scenario["scope_label"]],
            ["Base MXN", money(scenario["base"])],
            ["Porcentaje supuesto", float(scenario["assumptions"].get("percent", 0))],
            ["Impacto MXN", money(scenario["effect"])],
            ["Resultado MXN", money(scenario["result"])],
            ["Fórmula", scenario["formula"]],
            ["Límites", "; ".join(scenario["limits"])],
            ["Diferimiento en días", scenario["assumptions"].get("days")],
            ["Identificador de cifras", report["snapshot_id"]],
        ):
            case.append(row)
        if scenario["assumptions"]["kind"] != "payment_delay":
            case["B6"] = "=ROUND(B4*B5/100,2)"
            case["B7"] = "=B4-B6"
    for sheet in wb:
        sheet.sheet_view.showGridLines = False
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.print_title_rows = "1:1"
        for row in sheet:
            sheet.row_dimensions[row[0].row].height = 44
            for cell in row:
                if isinstance(cell.value, str) and cell.data_type == "f":
                    if not (
                        sheet.title == "Escenario" and cell.coordinate in {"B6", "B7"}
                    ):
                        cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if isinstance(cell.value, (int, float)):
                    cell.number_format = '#,##0.00;[Red](#,##0.00);"-"'
        for column in ("A", "B", "C", "D", "E", "F", "G", "H"):
            sheet.column_dimensions[column].width = 34 if column != "B" else 24
    ws.column_dimensions["E"].width = 55
    ws.column_dimensions["F"].width = 55
    # Size wrapped text using the effective width of merged cells.
    import math

    for sheet in wb:
        for cells in sheet:
            lines = 1
            for cell in cells:
                if cell.value is None:
                    continue
                width = sheet.column_dimensions[cell.column_letter].width
                for merged in sheet.merged_cells.ranges:
                    if cell.coordinate in merged:
                        width = sum(
                            sheet.column_dimensions[get_column_letter(col)].width
                            for col in range(merged.min_col, merged.max_col + 1)
                        )
                        break
                lines = max(
                    lines,
                    sum(
                        max(1, math.ceil(len(line) / max(width - 2, 1)))
                        for line in str(cell.value).split("\n")
                    ),
                )
            sheet.row_dimensions[cells[0].row].height = max(30, lines * 15 + 10)
    buffer = io.BytesIO()
    wb.save(buffer)
    if not scenario or scenario["assumptions"]["kind"] == "payment_delay":
        return buffer.getvalue()
    # Excel recalculates these formulas. Cache the same signed Decimal result so
    # read-only viewers also display numbers before opening in a calculation engine.
    import zipfile
    from xml.etree import ElementTree as ET

    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    output = io.BytesIO()
    scenario_path = f"xl/worksheets/sheet{wb.sheetnames.index('Escenario') + 1}.xml"
    with zipfile.ZipFile(buffer) as source, zipfile.ZipFile(
        output, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == scenario_path:
                root = ET.fromstring(content)
                for coordinate, key in (("B6", "effect"), ("B7", "result")):
                    cell = root.find(f".//s:c[@r='{coordinate}']", namespace)
                    cell.find("s:v", namespace).text = scenario[key]
                content = ET.tostring(root, encoding="utf-8")
            target.writestr(item, content)
    return output.getvalue()


def generate_direction_report_pdf(report: dict[str, Any]) -> bytes:
    """Paginated report with wrapped business text, vector charts and source notes."""
    from html import escape

    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from samchat.client_executive.home import amount, format_money

    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 9
    styles["Normal"].leading = 13
    styles["Heading2"].keepWithNext = True
    styles["Heading3"].keepWithNext = True

    def p(value, style="Normal"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])

    story = [
        p(report["title"], "Title"),
        p(report["scope"]),
        p(
            f"Periodo: {report['period']['start']} a {report['period']['end']} · Corte: {report['cut']}"
        ),
        Spacer(1, 12),
        p("Conclusión", "Heading2"),
        p(report["conclusion"]),
        p(report["validation"]),
        Spacer(1, 10),
    ]
    data = [[p("Indicador"), p("MXN"), p("Cobertura y periodo")]]
    for metric in report["indicators"]:
        data.append(
            [
                p(metric["label"]),
                p(format_money(metric["value"])),
                p(
                    f"{metric['coverage']['covered']}/{metric['coverage']['total']} torneos. {metric['period']}"
                ),
            ]
        )
    table = Table(data, colWidths=[170, 110, 230], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dcecee")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1dfe8")),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story += [
        table,
        Spacer(1, 10),
        p("Ejecución frente a presupuesto anual", "Heading2"),
    ]
    for row in report["tournaments"]:
        budget, actual = amount(row["values"].get("budget")), amount(
            row["values"].get("actual")
        )
        story.append(p(row["name"], "Heading3"))
        story.append(
            p(
                f"Presupuesto anual: {format_money(budget)} · Ejercido del intervalo: {format_money(actual)}"
            )
        )
        if budget is not None and actual is not None and budget >= 0 and actual >= 0:
            maximum = max(budget, actual, Decimal("1"))
            drawing = Drawing(500, 52)
            for y, value, label, color in (
                (28, budget, "Presupuesto", "#8aaab4"),
                (5, actual, "Ejercido", "#078590"),
            ):
                drawing.add(String(0, y + 4, label, fontSize=9))
                drawing.add(
                    Rect(
                        85,
                        y,
                        float(value / maximum) * 405,
                        13,
                        fillColor=colors.HexColor(color),
                        strokeColor=None,
                    )
                )
            story.append(drawing)
        series = row.get("monthly_execution") or {}
        if series.get("status") == "available":
            story.append(p("Evolución mensual conciliada", "Heading3"))
            for point in series["rows"]:
                story.append(
                    p(f"Mes {point['month']:02d}: {format_money(point['value'])}")
                )
    for index, item in enumerate(report["recommendations"]):
        story.append(
            KeepTogether(
                (
                    [p("Riesgos, recomendaciones y alternativas", "Heading2")]
                    if index == 0
                    else []
                )
                + [
                    p(item["title"], "Heading3"),
                    p("Evidencia: " + item["evidence"]),
                    p("Recomendación: " + item["recommendation"]),
                    p("Alternativa: " + item["alternative"]),
                    p("Riesgo: " + item["risk"]),
                    p("Siguiente paso: " + item["next_step"]),
                ]
            )
        )
    if report.get("scenario"):
        s = report["scenario"]
        story += [
            p("Escenario hipotético separado del real", "Heading2"),
            p(s["title"]),
            p(s["scope_label"]),
            p(
                f"Base: {format_money(s['base'])} · impacto: {format_money(s['effect'])} · resultado: {format_money(s['result'])}"
            ),
            p(s["formula"]),
            p(
                "Supuestos: "
                + "; ".join(
                    f"{dict(percent='Porcentaje', days='Días de diferimiento')[k]}: {v}"
                    for k, v in s["assumptions"].items()
                    if k in {"percent", "days"}
                )
            ),
            *[p(note) for note in s["limits"]],
        ]
    story += [
        p("Datos faltantes y límites", "Heading2"),
        *[p(g) for g in report["gaps"]],
        p(report["method"]),
        p("Definiciones y fuentes", "Heading2"),
    ]
    for metric in report["indicators"]:
        story.append(
            KeepTogether(
                [
                    p(metric["label"], "Heading3"),
                    p(metric["definition"]),
                    p("Fuente: " + metric["source"]),
                ]
            )
        )
    story.append(p("Identificador de cifras: " + report["snapshot_id"]))
    buffer = io.BytesIO()

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.drawString(42, 24, "SamChat · Dirección · " + report["snapshot_id"][:12])
        canvas.drawRightString(A4[0] - 42, 24, f"Página {doc.page}")

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=40,
        bottomMargin=42,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _summary_metric_rows(
    *,
    finance_platform: dict[str, Any],
    budget_snapshot: dict[str, Any],
    ar_payload: dict[str, Any],
    alerts: list[dict[str, Any]],
) -> list[list[Any]]:
    finance_summary = finance_platform.get("summary") or {}
    budget_summary = budget_snapshot.get("summary") or {}
    budget_forecast = budget_snapshot.get("forecast") or {}
    ar_summary = ar_payload.get("summary") or {}
    high_alerts = sum(1 for item in alerts if item.get("severity") == "high")
    medium_alerts = sum(1 for item in alerts if item.get("severity") == "medium")
    return [
        ["Alertas ejecutivas", len(alerts)],
        ["Alertas alta prioridad", high_alerts],
        ["Alertas media prioridad", medium_alerts],
        [
            "Acciones financieras abiertas",
            _safe_int(finance_summary.get("open_actions")),
        ],
        ["Presupuesto total", _safe_float(budget_summary.get("budget_total"))],
        ["Comprometido", _safe_float(budget_summary.get("committed_total"))],
        ["Pagado", _safe_float(budget_summary.get("paid_total"))],
        [
            "Cierre proyectado",
            _safe_float(budget_forecast.get("projected_close_total")),
        ],
        [
            "Ingreso presupuestado",
            _safe_float(ar_summary.get("expected_income_total")),
        ],
        ["Facturado CxC", _safe_float(ar_summary.get("invoiced_total"))],
        [
            "Cobrado comprobado CxC",
            _safe_float(ar_summary.get("collected_total")),
        ],
        ["Saldo pendiente CxC", _safe_float(ar_summary.get("balance_total"))],
    ]


def _budget_rows(snapshot: dict[str, Any]) -> list[list[Any]]:
    summary = snapshot.get("summary") or {}
    forecast = snapshot.get("forecast") or {}
    rows = [
        [
            "Presupuesto",
            _safe_float(summary.get("budget_total")),
            "Bolsa aprobada",
        ],
        [
            "Solicitado",
            _safe_float(summary.get("requested_total")),
            "Solicitudes",
        ],
        [
            "Comprometido",
            _safe_float(summary.get("committed_total")),
            "Compromisos",
        ],
        [
            "Pagado",
            _safe_float(summary.get("paid_total")),
            "Salida ejecutada",
        ],
        ["Real", _safe_float(summary.get("actual_total")), "Gasto observado"],
        [
            "Pendiente por pagar",
            _safe_float(summary.get("pending_to_pay_total")),
            "Presión operativa",
        ],
        [
            "Necesidad de caja",
            _safe_float(forecast.get("projected_cash_need")),
            "Forecast",
        ],
    ]
    for item in list(snapshot.get("executive_comparison") or [])[:12]:
        rows.append(
            [
                _safe_text(item.get("label")),
                _safe_float(item.get("total")),
                _safe_text(item.get("detail")),
            ]
        )
    return rows


def _cashflow_rows(platform: dict[str, Any]) -> list[list[Any]]:
    cash = platform.get("cash_control_center") or {}
    payment = platform.get("payment_run") or {}
    accounting = platform.get("accounting_close_center") or {}
    tax = platform.get("tax_readiness") or {}
    return [
        [
            "Pagos pendientes",
            _safe_float(payment.get("payable_total")),
            _safe_int(payment.get("payable_count")),
        ],
        [
            "Pagado",
            _safe_float(cash.get("paid_total")),
            _safe_int(cash.get("paid_documents_count")),
        ],
        [
            "Ingresos contabilizados",
            _safe_float(cash.get("income_total")),
            _safe_int(cash.get("income_polizas_count")),
        ],
        [
            "COI pendiente",
            _safe_int(accounting.get("pending_coi_expenses_count")),
            "gastos",
        ],
        [
            "Pólizas descuadradas",
            _safe_int(accounting.get("unbalanced_count")),
            "pólizas",
        ],
        [
            "DIOT/CFDI bloqueado",
            _safe_int(tax.get("diot_blockers_count")),
            _safe_text(tax.get("status")),
        ],
    ]


def _ar_rows(
    ar_payload: dict[str, Any],
    ar_rows: list[dict[str, Any]],
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    summary = ar_payload.get("summary") or {}
    rows.append(
        [
            "Resumen",
            "Ingreso presupuestado",
            _safe_float(summary.get("expected_income_total")),
            "",
        ]
    )
    rows.append(
        [
            "Resumen",
            "Facturado",
            _safe_float(summary.get("invoiced_total")),
            "",
        ]
    )
    rows.append(
        [
            "Resumen",
            "Cobrado comprobado",
            _safe_float(summary.get("collected_total")),
            "",
        ]
    )
    rows.append(
        [
            "Resumen",
            "Saldo pendiente",
            _safe_float(summary.get("balance_total")),
            "",
        ]
    )
    for item in ar_rows[:100]:
        rows.append(
            [
                _safe_text(item.get("operational_status")),
                _safe_text(item.get("payer_name") or item.get("tournament_name")),
                _safe_float(item.get("balance_amount") or item.get("issued_amount")),
                _safe_text(item.get("ar_item_id")),
            ]
        )
    return rows


def _alert_rows(alerts: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            _safe_text(item.get("severity")),
            _safe_text(item.get("module")),
            _safe_text(item.get("title")),
            _safe_text(item.get("detail")),
            _safe_text(item.get("owner")),
            _safe_text(item.get("source")),
            _safe_text(item.get("href")),
        ]
        for item in alerts[:100]
    ]


def generate_executive_export_xlsx(
    *,
    finance_platform: dict[str, Any],
    budget_snapshot: dict[str, Any],
    ar_payload: dict[str, Any],
    ar_rows: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    source_notes: list[str] | None = None,
) -> bytes:
    """Build a read-only workbook for executive review."""

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen ejecutivo"
    generated_at = (
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    period = finance_platform.get("period") or {}

    ws["A1"] = "Export ejecutivo SamChat"
    ws["A1"].font = Font(size=18, bold=True)
    ws["A2"] = f"Periodo: {period.get('month') or ''}/{period.get('year') or ''}"
    ws["A3"] = f"Generado: {generated_at}"
    _write_table(
        ws,
        ["Métrica", "Valor"],
        _summary_metric_rows(
            finance_platform=finance_platform,
            budget_snapshot=budget_snapshot,
            ar_payload=ar_payload,
            alerts=alerts,
        ),
        start_row=5,
    )

    ws_budget = wb.create_sheet("Presupuesto")
    _write_table(
        ws_budget,
        ["Métrica", "Valor", "Detalle"],
        _budget_rows(budget_snapshot),
    )

    ws_cash = wb.create_sheet("Flujo y pagos")
    _write_table(
        ws_cash,
        ["Métrica", "Valor", "Conteo/estado"],
        _cashflow_rows(finance_platform),
    )

    ws_ar = wb.create_sheet("Cuentas por cobrar")
    _write_table(
        ws_ar,
        ["Estado", "Cliente/proyecto", "Monto", "Referencia"],
        _ar_rows(ar_payload, ar_rows),
    )

    ws_alerts = wb.create_sheet("Alertas")
    _write_table(
        ws_alerts,
        [
            "Severidad",
            "Módulo",
            "Alerta",
            "Detalle",
            "Responsable",
            "Fuente",
            "Ruta",
        ],
        _alert_rows(alerts),
    )

    ws_sources = wb.create_sheet("Fuentes y limites")
    ws_sources["A1"] = "Fuentes y límites"
    ws_sources["A1"].font = _BOLD_FONT
    _write_table(
        ws_sources,
        ["Fuente", "Estado"],
        [
            ["Finance Platform", "snapshot read-only"],
            ["Presupuestos", "snapshot read-only"],
            ["Cuentas por cobrar", "read model AR"],
            ["Alertas ejecutivas", "consolidación read-only"],
            ["Límite", "No ejecuta pagos, no crea pólizas, no cambia saldos."],
            *[["Aviso", note] for note in source_notes or []],
        ],
        start_row=3,
    )

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
