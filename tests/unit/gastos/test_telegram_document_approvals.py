from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from devnous.gastos.models import Documento
from devnous.gastos.services import documento_telegram


def test_telegram_approval_guard_allows_assigned_approver_without_admin_role():
    approver_id = uuid4()
    empleado = SimpleNamespace(id=approver_id, rol="empleado")
    documento = SimpleNamespace(
        estado="enviado",
        empleado=SimpleNamespace(id=uuid4(), aprobador_id=uuid4()),
        beneficiario_empleado=SimpleNamespace(id=uuid4(), aprobador_id=approver_id),
    )

    assert (
        documento_telegram.approver_can_see_document_in_queue(empleado, documento)
        is True
    )


def test_telegram_approval_guard_blocks_stale_buttons_after_state_moves():
    approver_id = uuid4()
    empleado = SimpleNamespace(id=approver_id, rol="empleado")
    documento = SimpleNamespace(
        estado="aprobado",
        empleado=SimpleNamespace(id=uuid4(), aprobador_id=uuid4()),
        beneficiario_empleado=SimpleNamespace(id=uuid4(), aprobador_id=approver_id),
    )

    assert (
        documento_telegram.approver_can_see_document_in_queue(empleado, documento)
        is False
    )


def test_telegram_pending_command_does_not_require_admin_role():
    source = Path("src/devnous/gastos/services/telegram_document_runtime.py").read_text(
        encoding="utf-8"
    )
    start = source.index("async def send_pendientes")
    end = source.index("async def send_mis_solicitudes", start)
    send_pendientes = source[start:end]

    assert "APPROVER_QUEUE_ROLES" not in send_pendientes
    assert "query_pending_documentos_for_approver" in send_pendientes


def test_partial_telegram_approval_requires_web_review_with_comments():
    document_id = uuid4()
    keyboard = documento_telegram.approval_inline_keyboard(document_id, partial=True)
    button = keyboard["inline_keyboard"][0][0]
    assert button["url"] == f"https://sam.chat/documentos/{document_id}"
    assert "comentarios" in button["text"]
    assert "callback_data" not in button


def test_partial_telegram_summary_shows_applicant_reason_and_lot_amount():
    document = Documento(
        id=uuid4(),
        tipo="INFORME",
        numero_referencia="I-575016-C1",
        estado="enviado",
        informe_origen_id=uuid4(),
        motivo_comprobacion_parcial="Faltan comprobantes del viaje",
        creado_en=None,
        enviado_en=None,
        aprobado_en=None,
        empleado_id=None,
        beneficiario_empleado_id=None,
        beneficiario_proveedor_cliente_id=None,
        beneficiario_alterno_tipo=None,
    )
    summary = documento_telegram.format_documento_resumen_es(
        document,
        context={"solicitante": "Carlos Lozano", "monto_line": "$13,912.50"},
        include_actions_hint=True,
    )
    assert "Faltan comprobantes del viaje" in summary
    assert "13,912.50" in summary
    assert "permanece abierto" in summary
    assert "registrar comentarios" in summary
