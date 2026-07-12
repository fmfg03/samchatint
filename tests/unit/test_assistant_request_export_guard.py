from samchat.assistant.request_intent import detect_request_intent
from samchat.assistant.request_reports import RequestReportResult
from samchat.assistant.request_response import build_request_trace
from samchat.assistant.request_router import route_request
from samchat.assistant.router import _maybe_append_export_prompt


def test_timeout_with_stale_exportable_trace_never_offers_export():
    stale_trace = [
        {
            "tool": "executive.realtime_report",
            "result": {"rows": [{"concepto": "Uniformes", "amount": 100}]},
        }
    ]
    message = (
        "El proveedor del asistente tardó demasiado en responder. "
        "No ejecuté acciones ni cambios; intenta de nuevo con una "
        "consulta más "
        "corta."
    )

    assert _maybe_append_export_prompt(message, stale_trace) == message


def test_successful_request_trace_with_rows_allows_export_prompt():
    intent = detect_request_intent("Compara gasto 2026 vs 2025 por concepto")
    route = route_request(intent)
    result = RequestReportResult(
        status="success",
        title="Comparación",
        summary="ok",
        columns=["concepto", "gasto_2025", "gasto_2026"],
        rows=[{"concepto": "Uniformes", "gasto_2025": 100, "gasto_2026": 150}],
        caveats=[],
        exportable=True,
        provider_called=False,
        actions_executed=[],
        canonical_action="finance.read_only_comparison",
    )

    message = "Comparación generada."
    trace = build_request_trace(intent=intent, route=route, result=result)

    assert "¿Quieres que te lo exporte ahora?" in _maybe_append_export_prompt(
        message,
        trace,
    )


def test_unavailable_request_trace_does_not_allow_export_prompt():
    intent = detect_request_intent("Compara gasto 2026 vs 2025 por concepto")
    route = route_request(intent)
    result = RequestReportResult(
        status="data_source_unavailable",
        title="Sin fuente",
        summary="No disponible",
        columns=[],
        rows=[],
        caveats=[],
        exportable=False,
        provider_called=False,
        actions_executed=[],
        canonical_action="finance.read_only_comparison",
    )

    message = (
        "No encontré una fuente de datos financiera disponible. "
        "No ejecuté cambios."
    )
    trace = build_request_trace(intent=intent, route=route, result=result)

    assert _maybe_append_export_prompt(message, trace) == message
