from devnous.gastos.status_semantics import (
    document_status_visual,
    payment_run_status_visual,
)
from devnous.gastos.routes import user_routes


def test_document_statuses_distinguish_approval_from_confirmed_payment():
    assert document_status_visual("rechazado").semantic == "danger"
    assert document_status_visual("aprobado").semantic == "attention"
    assert document_status_visual("aprobado").background == "#fef3c7"
    assert document_status_visual("pagado").semantic == "success"
    assert document_status_visual("pagado").background == "#dcfce7"


def test_payment_run_statuses_use_the_shared_semaphore():
    assert payment_run_status_visual("programada").semantic == "attention"
    assert payment_run_status_visual("vencida").semantic == "danger"
    assert payment_run_status_visual("cerrada").semantic == "progress"
    assert payment_run_status_visual("pagada").semantic == "success"


def test_unknown_status_is_visible_but_neutral():
    visual = document_status_visual("pendiente_de_revision_manual")

    assert visual.label == "Pendiente de revision manual"
    assert visual.semantic == "neutral"
    assert visual.note == "Revisar estado"


def test_document_badge_exposes_text_and_semantic_not_color_alone():
    html = user_routes._documento_human_status_badge("aprobado")

    assert "Aprobado" in html
    assert 'data-status-semantic="attention"' in html
    assert "background:#fef3c7" in html


def test_document_detail_chip_uses_the_shared_visual_colors():
    html = user_routes._documento_status_chip_html("en_proceso_pago")

    assert "En proceso de pago" in html
    assert 'data-status-semantic="progress"' in html
    assert "background:#ede9fe" in html
