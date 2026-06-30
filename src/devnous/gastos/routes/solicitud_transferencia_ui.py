"""
HTML/CSS helpers for solicitud de transferencia forms and detail views.

Layout mirrors docs/Solicitud_Transferencia_DUMMY.xlsx (Sol trans.xlsx).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from html import escape
from typing import Optional

from ..services.payment_schedule_service import preview_fecha_pago_for_now
from ..utils.mexico_city_dates import today_mexico_city

MATERIALIDADES_FILE_ACCEPT = (
    ".pdf,.xml,.jpg,.jpeg,.png,.gif,.webp,.txt,.csv,.doc,.docx,.xls,.xlsx,"
    "application/pdf,application/xml,text/xml,image/*,text/plain,text/csv,"
    "application/msword,application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document,application/vnd.ms-excel,"
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

_WEEKDAY_ES = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]
_MONTH_ES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


def cuenta_last4(
    clabe: Optional[str] = None,
    cuenta: Optional[str] = None,
) -> str:
    """Return last 4 digits from CLABE or account number."""
    for raw in (clabe, cuenta):
        if not raw:
            continue
        digits = "".join(c for c in str(raw) if c.isdigit())
        if len(digits) >= 4:
            return digits[-4:]
        if digits:
            return digits
    return ""


def mask_cuenta_display(
    cuenta: Optional[str] = None,
    *,
    ultimos4: Optional[str] = None,
) -> str:
    """Mask account number like the Excel template (****1234)."""
    if ultimos4:
        digits = "".join(c for c in str(ultimos4) if c.isdigit())
        if digits:
            return f"****{digits[-4:]}"
    if cuenta:
        digits = "".join(c for c in str(cuenta) if c.isdigit())
        if len(digits) >= 4:
            return f"****{digits[-4:]}"
        if digits:
            return f"****{digits}"
    return ""


def format_fecha_transferencia_display(value: Optional[object]) -> str:
    """Format date values like the Excel export (Spanish long form)."""
    if not value:
        return "—"
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return "—"
        try:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return raw
    else:
        return str(value)
    weekday = _WEEKDAY_ES[dt.weekday()].capitalize()
    month = _MONTH_ES[dt.month - 1]
    return f"{weekday}, {dt.day} de {month} del {dt.year}"


def format_fecha_transferencia_preview_today(value: Optional[object]) -> str:
    """Short CDMX date for the 'Si se aprueba hoy' preview prefix."""
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    else:
        return ""
    weekday_abbr = _WEEKDAY_ES[d.weekday()][:3]
    month_abbr = _MONTH_ES[d.month - 1][:3]
    return f"{weekday_abbr} {d.day} {month_abbr} {d.year}"


def st_fecha_pago_form_kwargs(
    documento: Optional[object] = None,
) -> dict[str, Optional[date]]:
    """Build kwargs for render_st_fecha_pago_field on create/edit forms."""
    today = today_mexico_city()
    preview = preview_fecha_pago_for_now()
    stored: Optional[date] = None
    if documento is not None:
        raw = getattr(documento, "fecha_pago", None)
        if raw is not None:
            stored = raw if isinstance(raw, date) else None
    return {
        "value": stored,
        "preview_value": preview if stored is None else None,
        "today": today,
    }


def solicitud_transferencia_doc_styles() -> str:
    return """
        .st-page-wrap {
            max-width: 920px;
            margin: 0 auto;
        }
        .st-doc {
            border: 1px solid #1e293b;
            background: #ffffff;
            font-family: Arial, Helvetica, sans-serif;
            font-size: 14px;
            color: #0f172a;
            margin-bottom: 20px;
        }
        .st-doc-header {
            padding: 18px 16px 10px;
            border-bottom: 1px solid #cbd5e1;
            text-align: center;
        }
        .st-doc-company {
            font-size: 14px;
            font-weight: 700;
            text-decoration: underline;
            letter-spacing: 0.02em;
        }
        .st-doc-title {
            margin-top: 6px;
            font-size: 12px;
            font-style: italic;
            font-weight: 600;
            color: #334155;
        }
        .st-doc-meta {
            display: flex;
            justify-content: flex-end;
            align-items: flex-start;
            gap: 28px;
            padding: 10px 16px 12px;
            border-bottom: 1px solid #cbd5e1;
            font-size: 13px;
            color: #334155;
        }
        .st-header-fecha-formatted {
            font-weight: 600;
            text-align: right;
            line-height: 1.35;
            min-height: 1.35em;
        }
        .st-fecha-pago-field {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 6px;
            width: 100%;
        }
        .st-fecha-pago-field.st-fecha-pago-locked {
            background: #f8fafc;
            border-radius: 4px;
            padding: 8px 10px;
            box-sizing: border-box;
        }
        .st-fecha-pago-field .st-fecha-pago-display {
            font-weight: 600;
            line-height: 1.35;
            min-height: 1.35em;
        }
        .st-fecha-pago-field input[type="date"] {
            width: 100%;
            font-size: 12px;
            border: 1px solid #cbd5e1;
            border-radius: 4px;
            padding: 5px 8px;
            font-family: inherit;
            color: #334155;
            background: #ffffff;
            box-sizing: border-box;
        }
        .st-fecha-pago-preview-box {
            margin-top: 4px;
            padding: 8px 10px;
            border: 1px dashed #cbd5e1;
            border-radius: 4px;
            background: #ffffff;
            width: 100%;
            box-sizing: border-box;
        }
        .st-fecha-pago-preview-label {
            font-size: 11px;
            color: #64748b;
            margin-bottom: 4px;
        }
        .st-fecha-pago-preview-value {
            font-weight: 600;
            font-size: 13px;
            color: #334155;
            line-height: 1.35;
        }
        .st-fecha-pago-field small {
            font-size: 11px;
            color: #64748b;
            font-weight: 400;
        }
        .st-doc-row {
            display: grid;
            grid-template-columns: minmax(150px, 190px) minmax(0, 1fr);
            border-bottom: 1px solid #dbe2ea;
            min-height: 40px;
        }
        .st-doc-row.st-dual {
            grid-template-columns: minmax(150px, 190px) minmax(0, 1fr) minmax(130px, 160px) minmax(0, 1fr);
        }
        .st-doc-row.st-cuenta {
            grid-template-columns: minmax(150px, 190px) minmax(0, 1fr) minmax(130px, 160px) minmax(0, 1fr);
        }
        .st-doc-label {
            font-weight: 700;
            padding: 10px 12px;
            background: #f8fafc;
            border-right: 1px solid #dbe2ea;
            display: flex;
            align-items: center;
            text-transform: uppercase;
            font-size: 12px;
            letter-spacing: 0.03em;
        }
        .st-doc-value {
            padding: 6px 10px;
            display: flex;
            align-items: center;
            min-width: 0;
        }
        .st-doc-value.readonly {
            background: #f1f5f9;
            color: #334155;
        }
        .st-doc-value.st-value-bold {
            font-weight: 700;
        }
        .st-doc-value.st-value-bold input,
        .st-doc-value.st-value-bold select,
        .st-doc-value.st-value-bold textarea,
        .st-doc-value.st-value-bold span:not(small) {
            font-weight: 700;
        }
        .st-doc-value input,
        .st-doc-value select,
        .st-doc-value textarea {
            width: 100%;
            border: 1px solid #cbd5e1;
            border-radius: 4px;
            padding: 8px 10px;
            font-size: 14px;
            font-family: inherit;
            box-sizing: border-box;
            background: #ffffff;
        }
        .st-doc-value textarea {
            min-height: 72px;
            resize: vertical;
        }
        .st-doc-value input[readonly],
        .st-doc-value textarea[readonly] {
            background: #f1f5f9;
            color: #334155;
        }
        .st-doc-value small {
            display: block;
            margin-top: 4px;
            color: #64748b;
            font-size: 11px;
            font-weight: 400;
        }
        .st-doc-firmas {
            display: grid;
            grid-template-columns: 1fr 1fr;
            border-top: 2px solid #1e293b;
        }
        .st-doc-firma-block {
            padding: 14px 16px 18px;
            border-right: 1px solid #dbe2ea;
        }
        .st-doc-firma-block:last-child {
            border-right: none;
        }
        .st-doc-firma-label {
            font-weight: 700;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            margin-bottom: 8px;
        }
        .st-doc-firma-name {
            min-height: 28px;
            padding: 8px 10px;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 4px;
        }
        .st-doc-supplement {
            margin-top: 16px;
            padding: 14px 16px;
            border: 1px dashed #cbd5e1;
            border-radius: 10px;
            background: #f8fafc;
        }
        .st-doc-supplement h3 {
            margin: 0 0 12px;
            font-size: 14px;
            color: #334155;
        }
        .st-support-section {
            margin-top: 0;
            margin-bottom: 18px;
            padding: 16px;
            border: 1px solid #dbe2ea;
            border-radius: 10px;
            background: #f8fafc;
        }
        .st-support-section h3 {
            margin: 0 0 12px;
            font-size: 14px;
            color: #334155;
        }
        .st-materialidades-picker {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        .st-materialidades-list {
            list-style: none;
            margin: 0;
            padding: 0;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .st-materialidades-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding: 8px 10px;
            border: 1px solid #dbe2ea;
            border-radius: 8px;
            background: #ffffff;
        }
        .st-materialidades-name {
            flex: 1;
            min-width: 0;
            font-size: 13px;
            color: #334155;
            word-break: break-word;
        }
        .st-materialidades-remove {
            flex-shrink: 0;
            padding: 4px 10px;
            font-size: 12px;
        }
        .st-materialidades-empty {
            margin: 0;
            font-size: 12px;
            color: #64748b;
        }
        .st-form-actions {
            margin-top: 22px;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        @media (max-width: 720px) {
            .st-doc-row.st-dual,
            .st-doc-row.st-cuenta {
                grid-template-columns: minmax(120px, 150px) minmax(0, 1fr);
            }
            .st-doc-row.st-dual .st-doc-label:nth-child(3),
            .st-doc-row.st-dual .st-doc-value:nth-child(4),
            .st-doc-row.st-cuenta .st-doc-label:nth-child(3),
            .st-doc-row.st-cuenta .st-doc-value:nth-child(4) {
                border-top: 1px solid #dbe2ea;
            }
            .st-doc-firmas {
                grid-template-columns: 1fr;
            }
            .st-doc-firma-block {
                border-right: none;
                border-bottom: 1px solid #dbe2ea;
            }
        }
    """


def render_st_doc_header(*, date_display: Optional[str] = None) -> str:
    if not date_display:
        date_display = "—"
    return f"""
        <div class="st-doc-header">
            <div class="st-doc-company">PLATAFORMA SPORTS S. C.</div>
            <div class="st-doc-title">SOLICITUD DE TRANSFERENCIA</div>
        </div>
        <div class="st-doc-meta">
            <span>CDMX</span>
            <span>{escape(date_display)}</span>
        </div>
    """


def render_st_doc_header_form(*, current_date: Optional[date] = None) -> str:
    """Header for create forms: top-right shows today's date in CDMX."""
    if current_date is None:
        current_date = today_mexico_city()
    date_display = format_fecha_transferencia_display(current_date)
    return f"""
        <div class="st-doc-header">
            <div class="st-doc-company">PLATAFORMA SPORTS S. C.</div>
            <div class="st-doc-title">SOLICITUD DE TRANSFERENCIA</div>
        </div>
        <div class="st-doc-meta">
            <span>CDMX</span>
            <span class="st-header-fecha-formatted">{escape(date_display)}</span>
        </div>
    """


def render_st_fecha_pago_field(
    *,
    locked: bool = True,
    input_id: str = "fecha_pago",
    display_id: str = "st_row_fecha_pago_display",
    value: Optional[date] = None,
    preview_value: Optional[date] = None,
    today: Optional[date] = None,
    show_preview_hint: bool = True,
) -> str:
    """FECHA DE PAGO row: locked policy-driven display with optional preview."""
    if not locked:
        required_attr = " required"
        value_attr = f' value="{escape(value.isoformat())}"' if value else ""
        return f"""
        <div class="st-fecha-pago-field">
            <span id="{display_id}" class="st-fecha-pago-display">—</span>
            <input type="date" name="fecha_pago" id="{input_id}"{required_attr}{value_attr} aria-label="Fecha de pago">
            <small>Seleccione la fecha de pago</small>
        </div>
    """

    if value is not None:
        display_text = format_fecha_transferencia_display(value)
        helper = "Fecha asignada al momento de la aprobación."
        preview_html = ""
    else:
        display_text = "—"
        helper = (
            "Se calculará al aprobar según política de pagos "
            "(pagos los viernes y el último día hábil del mes)."
        )
        preview_html = ""
        if show_preview_hint and preview_value is not None:
            today_date = today or today_mexico_city()
            preview_prefix = format_fecha_transferencia_preview_today(today_date)
            preview_formatted = format_fecha_transferencia_display(preview_value)
            preview_html = f"""
            <div class="st-fecha-pago-preview-box">
                <div class="st-fecha-pago-preview-label">
                    Si se aprueba hoy ({escape(preview_prefix)}):
                </div>
                <div class="st-fecha-pago-preview-value">{escape(preview_formatted)}</div>
            </div>
            """

    return f"""
        <div class="st-fecha-pago-field st-fecha-pago-locked">
            <span id="{display_id}" class="st-fecha-pago-display">{escape(display_text)}</span>
            {preview_html}
            <small>{escape(helper)}</small>
        </div>
    """


def render_pago_urgente_field(*, checked: bool = False) -> str:
    """Render explicit urgent-payment checkbox for solicitud forms."""
    checked_attr = " checked" if checked else ""
    return f"""
        <label style="display:flex;align-items:center;gap:8px;font-weight:600;">
            <input type="checkbox" name="pago_urgente" value="1"{checked_attr}>
            Pago urgente
        </label>
        <small>Si se aprueba, se programa para pago el mismo dia.</small>
    """


_ST_DOC_BOLD_LABELS = frozenset(
    {
        "BENEFICIARIO:",
        "BANCO:",
        "CUENTA CLABE:",
        "CANTIDAD A PAGAR:",
        "REFERENCIA:",
    }
)


def _render_st_doc_label(label: str) -> str:
    escaped = escape(label)
    if label in _ST_DOC_BOLD_LABELS:
        return f"<strong>{escaped}</strong>"
    return escaped


def _st_doc_value_class(*, readonly: bool = False, bold: bool = False) -> str:
    parts = ["st-doc-value"]
    if readonly:
        parts.append("readonly")
    if bold:
        parts.append("st-value-bold")
    return " ".join(parts)


def render_st_doc_row(label: str, content_html: str, *, readonly: bool = False) -> str:
    value_class = _st_doc_value_class(
        readonly=readonly,
        bold=label in _ST_DOC_BOLD_LABELS,
    )
    return f"""
        <div class="st-doc-row">
            <div class="st-doc-label">{_render_st_doc_label(label)}</div>
            <div class="{value_class}">{content_html}</div>
        </div>
    """


def render_st_doc_row_dual(
    left_label: str,
    left_content_html: str,
    right_label: str,
    right_content_html: str,
    *,
    left_readonly: bool = False,
    right_readonly: bool = False,
) -> str:
    left_class = _st_doc_value_class(
        readonly=left_readonly,
        bold=left_label in _ST_DOC_BOLD_LABELS,
    )
    right_class = _st_doc_value_class(
        readonly=right_readonly,
        bold=right_label in _ST_DOC_BOLD_LABELS,
    )
    return f"""
        <div class="st-doc-row st-dual">
            <div class="st-doc-label">{_render_st_doc_label(left_label)}</div>
            <div class="{left_class}">{left_content_html}</div>
            <div class="st-doc-label">{_render_st_doc_label(right_label)}</div>
            <div class="{right_class}">{right_content_html}</div>
        </div>
    """


def render_st_doc_row_cuenta(
    cuenta_html: str,
    clabe_html: str,
    *,
    readonly: bool = True,
) -> str:
    cuenta_class = _st_doc_value_class(readonly=readonly, bold=False)
    clabe_class = _st_doc_value_class(readonly=readonly, bold=True)
    return f"""
        <div class="st-doc-row st-cuenta">
            <div class="st-doc-label">{_render_st_doc_label("CUENTA:")}</div>
            <div class="{cuenta_class}">{cuenta_html}</div>
            <div class="st-doc-label">{_render_st_doc_label("CUENTA CLABE:")}</div>
            <div class="{clabe_class}">{clabe_html}</div>
        </div>
    """


def render_st_doc_firmas(solicita: str, aprueba: str) -> str:
    return f"""
        <div class="st-doc-firmas">
            <div class="st-doc-firma-block">
                <div class="st-doc-firma-label">SOLICITA:</div>
                <div class="st-doc-firma-name">{escape(solicita) if solicita else "—"}</div>
            </div>
            <div class="st-doc-firma-block">
                <div class="st-doc-firma-label">APRUEBA:</div>
                <div class="st-doc-firma-name">{escape(aprueba) if aprueba else "—"}</div>
            </div>
        </div>
    """


def render_st_doc_readonly_value(value: Optional[str]) -> str:
    display = (value or "").strip() or "—"
    return escape(display)


def render_solicitud_transferencia_detail_view(
    *,
    beneficiario: str = "",
    banco: str = "",
    cuenta: str = "",
    cuenta_clabe: str = "",
    cantidad_display: str = "",
    cantidad_letra: str = "",
    proyecto: str = "",
    fecha_pago_display: str = "",
    concepto: str = "",
    numero_factura: str = "",
    referencia_operaciones: str = "",
    solicita: str = "",
    aprueba: str = "",
    referencia_pago: str = "",
    metodo_pago: str = "",
    pago_urgente: bool = False,
    fecha_inicio_display: str = "",
    fecha_fin_display: str = "",
    notas: str = "",
    date_display: Optional[str] = None,
) -> str:
    """Read-only document layout for SOLICITUD detail pages."""
    supplement_rows = []
    if referencia_pago:
        supplement_rows.append(
            render_st_doc_row("REFERENCIA DE PAGO:", render_st_doc_readonly_value(referencia_pago), readonly=True)
        )
    if metodo_pago:
        supplement_rows.append(
            render_st_doc_row("MÉTODO DE PAGO:", render_st_doc_readonly_value(metodo_pago), readonly=True)
        )
    if pago_urgente:
        supplement_rows.append(
            render_st_doc_row("PAGO URGENTE:", "Si", readonly=True)
        )
    if fecha_inicio_display and fecha_inicio_display != "—":
        supplement_rows.append(
            render_st_doc_row("FECHA INICIO:", render_st_doc_readonly_value(fecha_inicio_display), readonly=True)
        )
    if fecha_fin_display and fecha_fin_display != "—":
        supplement_rows.append(
            render_st_doc_row("FECHA FIN:", render_st_doc_readonly_value(fecha_fin_display), readonly=True)
        )

    supplement_html = ""
    if supplement_rows or notas:
        supplement_html = f"""
            <div class="st-doc-supplement">
                <h3>Información adicional</h3>
                <div class="st-doc">
                    {''.join(supplement_rows)}
                    {render_st_doc_row('NOTAS:', render_st_doc_readonly_value(notas), readonly=True) if notas else ''}
                </div>
            </div>
        """

    return f"""
        <section class="surface">
            <div class="section-head">
                <div>
                    <h2>Solicitud de transferencia</h2>
                    <div class="section-note">Vista alineada al formato oficial de solicitud de transferencia.</div>
                </div>
            </div>
            <div class="st-page-wrap">
                <div class="st-doc">
                    {render_st_doc_header(date_display=date_display)}
                    {render_st_doc_row('BENEFICIARIO:', render_st_doc_readonly_value(beneficiario), readonly=True)}
                    {render_st_doc_row('BANCO:', render_st_doc_readonly_value(banco), readonly=True)}
                    {render_st_doc_row_cuenta(render_st_doc_readonly_value(cuenta), render_st_doc_readonly_value(cuenta_clabe), readonly=True)}
                    {render_st_doc_row('CANTIDAD A PAGAR:', render_st_doc_readonly_value(cantidad_display), readonly=True)}
                    {render_st_doc_row('CANTIDAD CON LETRA:', render_st_doc_readonly_value(cantidad_letra), readonly=True)}
                    {render_st_doc_row_dual('PROYECTO:', render_st_doc_readonly_value(proyecto), 'FECHA DE PAGO:', render_st_doc_readonly_value(fecha_pago_display), left_readonly=True, right_readonly=True)}
                    {render_st_doc_row('CONCEPTO:', render_st_doc_readonly_value(concepto), readonly=True)}
                    {render_st_doc_row_dual('NÚMERO DE FACTURA:', render_st_doc_readonly_value(numero_factura), 'REFERENCIA:', render_st_doc_readonly_value(referencia_operaciones), left_readonly=True, right_readonly=True)}
                    {render_st_doc_firmas(solicita, aprueba)}
                </div>
                {supplement_html}
            </div>
        </section>
    """


def render_fecha_pago_sync_script(
    *,
    input_id: str = "fecha_pago",
    row_display_id: str = "st_row_fecha_pago_display",
) -> str:
    """Keep the FECHA DE PAGO row display in sync with the calendar picker."""
    return f"""
    <script>
        (function() {{
            const input = document.getElementById({json.dumps(input_id)});
            const rowDisplay = document.getElementById({json.dumps(row_display_id)});
            const WEEKDAY_ES = [
                'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'
            ];
            const MONTH_ES = [
                'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
                'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'
            ];

            function capitalize(value) {{
                if (!value) return '';
                return value.charAt(0).toUpperCase() + value.slice(1);
            }}

            function formatFechaTransferencia(isoDate) {{
                if (!isoDate) return '—';
                const parts = String(isoDate).split('-');
                if (parts.length !== 3) return isoDate;
                const year = parseInt(parts[0], 10);
                const month = parseInt(parts[1], 10);
                const day = parseInt(parts[2], 10);
                if (!year || !month || !day) return isoDate;
                const dt = new Date(year, month - 1, day);
                const weekday = capitalize(WEEKDAY_ES[(dt.getDay() + 6) % 7]);
                const monthName = MONTH_ES[month - 1] || '';
                return `${{weekday}}, ${{day}} de ${{monthName}} del ${{year}}`;
            }}

            function syncFechaDisplay() {{
                const formatted = formatFechaTransferencia(input ? input.value : '');
                if (rowDisplay) rowDisplay.textContent = formatted;
            }}

            if (input) {{
                input.addEventListener('change', syncFechaDisplay);
                input.addEventListener('input', syncFechaDisplay);
                syncFechaDisplay();
            }}
        }})();
    </script>
    """


def render_proveedor_bank_preview_script(
    *,
    select_id: str = "proveedor_cliente_id",
    banco_id: str = "st_preview_banco",
    cuenta_id: str = "st_preview_cuenta",
    clabe_id: str = "st_preview_clabe",
) -> str:
    return f"""
    <script>
        (function() {{
            const select = document.getElementById({json.dumps(select_id)});
            const bancoEl = document.getElementById({json.dumps(banco_id)});
            const cuentaEl = document.getElementById({json.dumps(cuenta_id)});
            const clabeEl = document.getElementById({json.dumps(clabe_id)});

            function maskCuenta(raw, ultimos4) {{
                const digits = String(ultimos4 || raw || '').replace(/\\D/g, '');
                if (!digits) return '—';
                return '****' + digits.slice(-4);
            }}

            function syncBankPreview() {{
                if (!select) return;
                const option = select.options[select.selectedIndex];
                if (!option || option.getAttribute('data-is-placeholder') === 'true') {{
                    if (bancoEl) bancoEl.textContent = '—';
                    if (cuentaEl) cuentaEl.textContent = '—';
                    if (clabeEl) clabeEl.textContent = '—';
                    return;
                }}
                const banco = option.getAttribute('data-banco') || '';
                const cuenta = option.getAttribute('data-cuenta') || '';
                const clabe = option.getAttribute('data-cuenta-clabe') || '';
                const u4 = option.getAttribute('data-ultimos4') || '';
                if (bancoEl) bancoEl.textContent = banco || '—';
                if (cuentaEl) cuentaEl.textContent = maskCuenta(cuenta, u4);
                if (clabeEl) clabeEl.textContent = clabe || '—';
            }}

            if (select) {{
                select.addEventListener('change', syncBankPreview);
                syncBankPreview();
            }}
        }})();
    </script>
    """


def render_cfdi_solicitud_terceros_autofill_script(
    *,
    api_url: str = "/api/documentos/cfdi-autofill",
    xml_input_id: str = "archivo_xml",
    pdf_input_id: str = "archivo_pdf",
    proveedor_search_id: str = "proveedor_search",
    monto_display_id: str = "monto_solicitado_display",
    currency_select_name: str = "currency",
    numero_factura_id: str = "numero_factura",
    notice_id: str = "cfdi_autofill_notice",
) -> str:
    return f"""
    <script>
        (function() {{
            const apiUrl = {json.dumps(api_url)};
            const xmlInput = document.getElementById({json.dumps(xml_input_id)});
            const pdfInput = document.getElementById({json.dumps(pdf_input_id)});
            const proveedorSearch = document.getElementById(
                {json.dumps(proveedor_search_id)}
            );
            const montoDisplay = document.getElementById(
                {json.dumps(monto_display_id)}
            );
            const currencySelect = document.querySelector(
                'select[name="{currency_select_name}"]'
            );
            const numeroFactura = document.getElementById(
                {json.dumps(numero_factura_id)}
            );
            const notice = document.getElementById({json.dumps(notice_id)});
            let autofillRequestId = 0;

            function hideNotice() {{
                if (!notice) return;
                notice.hidden = true;
                notice.textContent = '';
                notice.classList.remove('warn');
                notice.classList.add('info');
            }}

            function showNotice(message, isError) {{
                if (!notice) return;
                notice.hidden = false;
                notice.textContent = message;
                notice.classList.toggle('warn', !!isError);
                notice.classList.toggle('info', !isError);
            }}

            function applyAutofill(payload) {{
                const proveedorQuery = payload.emisor_nombre || payload.emisor_rfc;
                if (proveedorSearch && proveedorQuery) {{
                    proveedorSearch.value = proveedorQuery;
                    proveedorSearch.dispatchEvent(
                        new Event('input', {{ bubbles: true }})
                    );
                }}
                if (currencySelect && payload.currency) {{
                    const wanted = String(payload.currency || 'MXN').toUpperCase();
                    const option = Array.from(currencySelect.options).find(
                        function(opt) {{
                            return String(opt.value || '').toUpperCase() === wanted;
                        }}
                    );
                    if (option) {{
                        currencySelect.value = option.value;
                        currencySelect.dispatchEvent(
                            new Event('change', {{ bubbles: true }})
                        );
                    }}
                }}
                if (montoDisplay && payload.monto) {{
                    montoDisplay.value = payload.monto;
                    montoDisplay.dispatchEvent(
                        new Event('input', {{ bubbles: true }})
                    );
                    montoDisplay.dispatchEvent(
                        new Event('blur', {{ bubbles: true }})
                    );
                }}
                if (numeroFactura && payload.numero_factura) {{
                    numeroFactura.value = payload.numero_factura;
                }}
            }}

            async function requestAutofill(sourceInput, otherInput, preferXml) {{
                if (!sourceInput || !sourceInput.files || !sourceInput.files.length) {{
                    hideNotice();
                    return;
                }}
                if (
                    preferXml
                    && otherInput
                    && otherInput.files
                    && otherInput.files.length
                ) {{
                    return;
                }}

                const requestId = ++autofillRequestId;
                const formData = new FormData();
                formData.append(sourceInput.name, sourceInput.files[0]);

                showNotice('Leyendo CFDI para precargar campos…', false);

                try {{
                    const response = await fetch(apiUrl, {{
                        method: 'POST',
                        body: formData,
                        headers: {{ Accept: 'application/json' }},
                    }});
                    const payload = await response.json();
                    if (requestId !== autofillRequestId) {{
                        return;
                    }}
                    if (!response.ok || !payload.ok) {{
                        const errMsg = (
                            payload.error
                            || 'No se pudo leer el CFDI para precargar campos.'
                        );
                        showNotice(errMsg, true);
                        return;
                    }}
                    applyAutofill(payload.data || {{}});
                    showNotice(
                        'Campos precargados desde el CFDI. Revise antes de enviar.',
                        false
                    );
                }} catch (_err) {{
                    if (requestId === autofillRequestId) {{
                        showNotice(
                            'No se pudo leer el CFDI para precargar campos.',
                            true
                        );
                    }}
                }}
            }}

            if (xmlInput) {{
                xmlInput.addEventListener('change', function() {{
                    requestAutofill(xmlInput, pdfInput, false);
                }});
            }}
            if (pdfInput) {{
                pdfInput.addEventListener('change', function() {{
                    requestAutofill(pdfInput, xmlInput, true);
                }});
            }}
        }})();
    </script>
    """


def render_cfdi_quick_expense_autofill_script(
    *,
    api_url: str = "/api/informes-de-gastos/cfdi-autofill",
    xml_input_id: str = "quick-cfdi-xml",
    pdf_input_id: str = "quick-cfdi-pdf",
    concepto_id: str = "quick-concepto",
    fecha_id: str = "quick-fecha",
    numero_factura_id: str = "quick-numero-factura",
    subtotal_id: str = "quick-subtotal",
    descuento_id: str = "quick-descuento",
    impuestos_y_retenciones_id: str = "quick-impuestos-y-retenciones",
    total_id: str = "quick-total",
    notice_id: str = "quick_cfdi_autofill_notice",
) -> str:
    return f"""
    <script>
        (function() {{
            const apiUrl = {json.dumps(api_url)};
            const xmlInput = document.getElementById({json.dumps(xml_input_id)});
            const pdfInput = document.getElementById({json.dumps(pdf_input_id)});
            const concepto = document.getElementById({json.dumps(concepto_id)});
            const fecha = document.getElementById({json.dumps(fecha_id)});
            const numeroFactura = document.getElementById({json.dumps(numero_factura_id)});
            const subtotal = document.getElementById({json.dumps(subtotal_id)});
            const descuento = document.getElementById({json.dumps(descuento_id)});
            const impuestosYRetenciones = document.getElementById(
                {json.dumps(impuestos_y_retenciones_id)}
            );
            const total = document.getElementById({json.dumps(total_id)});
            const notice = document.getElementById({json.dumps(notice_id)});
            let autofillRequestId = 0;

            function money(value) {{
                const parsed = Number.parseFloat(value || '0');
                return Number.isFinite(parsed) ? parsed : 0;
            }}

            function updateTotal() {{
                if (!total) return;
                const computed = (
                    money(subtotal && subtotal.value)
                    - money(descuento && descuento.value)
                    + money(impuestosYRetenciones && impuestosYRetenciones.value)
                );
                total.value = computed.toFixed(2);
            }}

            [subtotal, descuento, impuestosYRetenciones].forEach(function(el) {{
                if (el) el.addEventListener('input', updateTotal);
            }});

            function hideNotice() {{
                if (!notice) return;
                notice.hidden = true;
                notice.textContent = '';
                notice.classList.remove('warn');
                notice.classList.add('info');
            }}

            function showNotice(message, isError) {{
                if (!notice) return;
                notice.hidden = false;
                notice.textContent = message;
                notice.classList.toggle('warn', !!isError);
                notice.classList.toggle('info', !isError);
            }}

            function applyAutofill(payload) {{
                if (concepto && payload.concepto) {{
                    concepto.value = payload.concepto;
                }}
                if (fecha && payload.fecha) {{
                    fecha.value = payload.fecha;
                }}
                if (numeroFactura && payload.numero_factura) {{
                    numeroFactura.value = payload.numero_factura;
                }}
                if (subtotal && payload.subtotal) {{
                    subtotal.value = payload.subtotal;
                }}
                if (descuento && payload.descuento !== undefined && payload.descuento !== '') {{
                    descuento.value = payload.descuento;
                }}
                if (
                    impuestosYRetenciones
                    && payload.impuestos_y_retenciones !== undefined
                    && payload.impuestos_y_retenciones !== ''
                ) {{
                    impuestosYRetenciones.value = payload.impuestos_y_retenciones;
                }}
                if (
                    total
                    && payload.total
                    && (!subtotal || !subtotal.value)
                ) {{
                    total.value = payload.total;
                }} else {{
                    updateTotal();
                }}
            }}

            async function requestAutofill(sourceInput, otherInput, deferToOther) {{
                if (!sourceInput || !sourceInput.files || !sourceInput.files.length) {{
                    hideNotice();
                    return;
                }}
                if (
                    deferToOther
                    && otherInput
                    && otherInput.files
                    && otherInput.files.length
                ) {{
                    return;
                }}

                const requestId = ++autofillRequestId;
                const formData = new FormData();
                formData.append(sourceInput.name, sourceInput.files[0]);

                showNotice('Leyendo CFDI para precargar campos…', false);

                try {{
                    const response = await fetch(apiUrl, {{
                        method: 'POST',
                        body: formData,
                        headers: {{ Accept: 'application/json' }},
                    }});
                    const payload = await response.json();
                    if (requestId !== autofillRequestId) {{
                        return;
                    }}
                    if (!response.ok || !payload.ok) {{
                        const errMsg = (
                            payload.error
                            || 'No se pudo leer el CFDI para precargar campos.'
                        );
                        showNotice(errMsg, true);
                        return;
                    }}
                    applyAutofill(payload.data || {{}});
                    showNotice(
                        'Campos precargados desde el CFDI. Revise antes de guardar.',
                        false
                    );
                }} catch (_err) {{
                    if (requestId === autofillRequestId) {{
                        showNotice(
                            'No se pudo leer el CFDI para precargar campos.',
                            true
                        );
                    }}
                }}
            }}

            if (xmlInput) {{
                xmlInput.addEventListener('change', function() {{
                    requestAutofill(xmlInput, pdfInput, false);
                }});
            }}
            if (pdfInput) {{
                pdfInput.addEventListener('change', function() {{
                    requestAutofill(pdfInput, xmlInput, true);
                }});
            }}

            updateTotal();
        }})();
    </script>
    """


def render_materialidades_file_picker_html(
    *,
    picker_id: str = "archivos_generales_picker",
    hidden_input_id: str = "archivos_generales",
    list_id: str = "archivos_generales_list",
    empty_id: str = "archivos_generales_empty",
) -> str:
    return f"""
        <div class="st-materialidades-picker">
            <input
                type="file"
                id="{escape(picker_id, quote=True)}"
                accept="{escape(MATERIALIDADES_FILE_ACCEPT, quote=True)}"
            >
            <small>Seleccione un archivo a la vez. Puede agregar varios antes de guardar.</small>
            <p id="{escape(empty_id, quote=True)}" class="st-materialidades-empty">Aún no hay archivos agregados.</p>
            <ul id="{escape(list_id, quote=True)}" class="st-materialidades-list" hidden></ul>
            <input
                type="file"
                name="archivos_generales"
                id="{escape(hidden_input_id, quote=True)}"
                multiple
                hidden
                tabindex="-1"
                aria-hidden="true"
            >
        </div>
    """


def render_materialidades_file_picker_script(
    *,
    picker_id: str = "archivos_generales_picker",
    hidden_input_id: str = "archivos_generales",
    list_id: str = "archivos_generales_list",
    empty_id: str = "archivos_generales_empty",
) -> str:
    return f"""
    <script>
        (function() {{
            const picker = document.getElementById({json.dumps(picker_id)});
            const hiddenInput = document.getElementById({json.dumps(hidden_input_id)});
            const listEl = document.getElementById({json.dumps(list_id)});
            const emptyEl = document.getElementById({json.dumps(empty_id)});
            const selectedFiles = [];

            function syncHiddenInput() {{
                if (!hiddenInput) {{
                    return;
                }}
                const dt = new DataTransfer();
                selectedFiles.forEach(function(file) {{
                    dt.items.add(file);
                }});
                hiddenInput.files = dt.files;
            }}

            function renderList() {{
                if (!listEl) {{
                    return;
                }}
                listEl.innerHTML = '';
                selectedFiles.forEach(function(file, index) {{
                    const item = document.createElement('li');
                    item.className = 'st-materialidades-item';

                    const name = document.createElement('span');
                    name.className = 'st-materialidades-name';
                    name.textContent = file.name || ('Archivo ' + (index + 1));

                    const removeBtn = document.createElement('button');
                    removeBtn.type = 'button';
                    removeBtn.className = 'button secondary st-materialidades-remove';
                    removeBtn.textContent = 'Quitar';
                    removeBtn.addEventListener('click', function() {{
                        selectedFiles.splice(index, 1);
                        renderList();
                        syncHiddenInput();
                    }});

                    item.appendChild(name);
                    item.appendChild(removeBtn);
                    listEl.appendChild(item);
                }});

                const hasFiles = selectedFiles.length > 0;
                listEl.hidden = !hasFiles;
                if (emptyEl) {{
                    emptyEl.hidden = hasFiles;
                }}
            }}

            function addFile(file) {{
                if (!file) {{
                    return;
                }}
                selectedFiles.push(file);
                renderList();
                syncHiddenInput();
            }}

            if (picker) {{
                picker.addEventListener('change', function() {{
                    const file = picker.files && picker.files[0];
                    if (file) {{
                        addFile(file);
                    }}
                    picker.value = '';
                }});
            }}

            const form = picker ? picker.closest('form') : null;
            if (form) {{
                form.addEventListener('submit', function() {{
                    syncHiddenInput();
                }});
            }}

            renderList();
            syncHiddenInput();
        }})();
    </script>
    """


def render_pdf_file_preview_script(
    *,
    input_id: str = "archivo_pdf",
    container_id: str = "archivo_pdf_preview",
    filename_id: str = "archivo_pdf_preview_name",
    frame_id: str = "archivo_pdf_preview_frame",
) -> str:
    return f"""
    <script>
        (function() {{
            const input = document.getElementById({json.dumps(input_id)});
            const container = document.getElementById({json.dumps(container_id)});
            const filenameEl = document.getElementById({json.dumps(filename_id)});
            const frame = document.getElementById({json.dumps(frame_id)});
            let currentObjectUrl = null;

            function resetPreview() {{
                if (currentObjectUrl) {{
                    URL.revokeObjectURL(currentObjectUrl);
                    currentObjectUrl = null;
                }}
                if (frame) {{
                    frame.removeAttribute('src');
                }}
                if (filenameEl) {{
                    filenameEl.textContent = 'Sin archivo seleccionado';
                }}
                if (container) {{
                    container.hidden = true;
                }}
            }}

            function syncPreview() {{
                if (!input || !input.files || !input.files.length) {{
                    resetPreview();
                    return;
                }}

                const file = input.files[0];
                if (currentObjectUrl) {{
                    URL.revokeObjectURL(currentObjectUrl);
                }}
                currentObjectUrl = URL.createObjectURL(file);

                if (filenameEl) {{
                    filenameEl.textContent = file.name || 'Archivo PDF';
                }}
                if (frame) {{
                    frame.src = currentObjectUrl;
                }}
                if (container) {{
                    container.hidden = false;
                }}
            }}

            if (input) {{
                input.addEventListener('change', syncPreview);
                resetPreview();
            }}
        }})();
    </script>
    """
