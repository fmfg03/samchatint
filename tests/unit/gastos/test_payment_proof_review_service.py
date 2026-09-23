from decimal import Decimal

from devnous.gastos.services.payment_proof_review_service import review_payment_proof


def test_payment_proof_review_blocks_known_amount_conflict(monkeypatch):
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.extract_document_text_from_bytes",
        lambda **_: "Fecha: 2026-09-22\nMonto: 99.00\nBeneficiario: Proveedor Demo\nReferencia: REF123",
    )
    review = review_payment_proof(raw=b"pdf", filename="proof.pdf", mime_type="application/pdf", expected_amount=Decimal("100.00"), expected_beneficiary="Proveedor Demo")
    assert review.status == "conflict"
    assert review.detected_date.isoformat() == "2026-09-22"


def test_payment_proof_review_keeps_unreadable_proof_manual(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.extract_document_text_from_bytes",
        lambda **_: (_ for _ in ()).throw(HTTPException(status_code=400, detail="no")),
    )
    review = review_payment_proof(raw=b"pdf", filename="proof.pdf", mime_type="application/pdf", expected_amount=Decimal("100.00"), expected_beneficiary="Proveedor Demo")
    assert review.status == "revision_required"


def test_payment_proof_review_marks_multiple_dates_for_manual_review(monkeypatch):
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.extract_document_text_from_bytes",
        lambda **_: "Fecha: 2026-09-22\nGenerado: 2026-09-23\nMonto: 100.00\nBeneficiario: Proveedor Demo\nReferencia: REF123",
    )
    review = review_payment_proof(raw=b"pdf", filename="proof.pdf", mime_type="application/pdf", expected_amount=Decimal("100.00"), expected_beneficiary="Proveedor Demo")
    assert review.status == "revision_required"
    assert review.detected_date is None


def test_payment_proof_review_blocks_declared_currency_conflict(monkeypatch):
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.extract_document_text_from_bytes",
        lambda **_: "Fecha: 2026-09-22\nMonto: 100.00 USD\nBeneficiario: Proveedor Demo\nReferencia: REF123",
    )
    review = review_payment_proof(raw=b"pdf", filename="proof.pdf", mime_type="application/pdf", expected_amount=Decimal("100.00"), expected_beneficiary="Proveedor Demo", expected_currency="MXN")
    assert review.status == "conflict"


def test_payment_proof_review_accepts_one_labeled_local_date(monkeypatch):
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.extract_document_text_from_bytes",
        lambda **_: "Referencia: REF123\nMonto: 100.00\nFecha: 22/09/2026\nBeneficiario: Proveedor Demo",
    )
    review = review_payment_proof(raw=b"pdf", filename="proof.pdf", mime_type="application/pdf", expected_amount=Decimal("100.00"), expected_beneficiary="Proveedor Demo")
    assert review.status == "match"
    assert review.template_id == "spei_transferencia_v1"
    assert review.detected_date.isoformat() == "2026-09-22"


def test_payment_proof_review_keeps_multiple_local_dates_manual(monkeypatch):
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.extract_document_text_from_bytes",
        lambda **_: "Fecha: 22/09/2026\nGenerado: 23/09/2026\nMonto: 100.00\nBeneficiario: Proveedor Demo\nReferencia: REF123",
    )
    review = review_payment_proof(raw=b"pdf", filename="proof.pdf", mime_type="application/pdf", expected_amount=Decimal("100.00"), expected_beneficiary="Proveedor Demo")
    assert review.status == "revision_required"
    assert review.detected_date is None


def test_payment_proof_review_requires_recognized_bank_template(monkeypatch):
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.extract_document_text_from_bytes",
        lambda **_: "Fecha: 22/09/2026\nMonto: 100.00\nBeneficiario: Proveedor Demo",
    )
    review = review_payment_proof(raw=b"pdf", filename="proof.pdf", mime_type="application/pdf", expected_amount=Decimal("100.00"), expected_beneficiary="Proveedor Demo")
    assert review.status == "revision_required"
    assert review.template_id is None


def test_payment_proof_review_rejects_reordered_template_markers(monkeypatch):
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.extract_document_text_from_bytes",
        lambda **_: "Fecha: 22/09/2026\nMonto: 100.00\nBeneficiario: Proveedor Demo\nReferencia: REF123",
    )
    review = review_payment_proof(raw=b"pdf", filename="proof.pdf", mime_type="application/pdf", expected_amount=Decimal("100.00"), expected_beneficiary="Proveedor Demo")
    assert review.status == "revision_required"
    assert review.template_id is None
