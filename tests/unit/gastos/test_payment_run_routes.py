from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from devnous.gastos.routes import admin_routes, dependencies
from devnous.gastos.services import documento_payment_service, payment_run_service
from devnous.gastos.services.payment_proof_review_service import PaymentProofReview


class _PaymentProofUpload:
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self.content = content
        self.content_type = "application/pdf"

    async def read(self) -> bytes:
        return self.content


def _payment_proof_conflict_document(document_id):
    return SimpleNamespace(
        id=document_id,
        estado="en_proceso_pago",
        numero_referencia="S-260099",
        monto_solicitado=Decimal("100.00"),
        monto_total=None,
        currency="MXN",
        beneficiario_empleado=None,
        proveedor_cliente=SimpleNamespace(nombre="Beneficiario Programado"),
    )


@pytest.mark.asyncio
async def test_payment_proof_review_returns_unpersisted_candidates(monkeypatch) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(
            id=document_id,
            estado="en_proceso_pago",
            monto_solicitado=Decimal("100.00"),
            monto_total=None,
            beneficiario_empleado=None,
            proveedor_cliente=SimpleNamespace(nombre="Proveedor Demo"),
        )
    )
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(admin_routes, "require_payment_run_payment_confirmation", lambda _: None)
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        lambda **_: PaymentProofReview(
            "match", date(2026, 9, 22), Decimal("100.00"), "MXN", "Proveedor Demo", "REF-1", ()
        ),
    )

    response = await admin_routes.admin_finance_payment_run_review_payment_proof(
        documento_id=document_id,
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        comprobante_pago=_PaymentProofUpload("proof.pdf", b"%PDF-1.4"),
    )

    assert response.status_code == 200
    assert b"2026-09-22" in response.body
    assert b"expected_amount" in response.body
    assert b"evidence_source" in response.body
    assert b"Proveedor Demo" in response.body
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_payment_proof_review_rejects_invalid_attachment_before_extraction(
    monkeypatch,
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(id=document_id, estado="en_proceso_pago")
    )
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(admin_routes, "require_payment_run_payment_confirmation", lambda _: None)
    monkeypatch.setattr(
        admin_routes,
        "validate_solicitud_terceros_attachment",
        lambda _: (_ for _ in ()).throw(
            admin_routes.SolicitudValidationError("invalid", "Archivo inválido")
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await admin_routes.admin_finance_payment_run_review_payment_proof(
            documento_id=document_id,
            session=session,
            current_empleado=SimpleNamespace(id=uuid4()),
            comprobante_pago=_PaymentProofUpload("proof.pdf", b"%PDF-1.4"),
        )

    assert exc.value.status_code == 400


def test_payment_run_bulk_proof_plan_requires_explicit_mapping() -> None:
    first_id = uuid4()
    second_id = uuid4()
    uploads = [
        SimpleNamespace(filename="spei-1.pdf"),
        SimpleNamespace(filename="spei-2.pdf"),
    ]

    plan = admin_routes._build_payment_proof_upload_plan(
        selected_document_ids=[first_id, second_id],
        proof_document_ids=[second_id, first_id],
        uploads=uploads,
        apply_one_to_all=False,
    )

    assert [(document_id, upload.filename) for document_id, upload in plan] == [
        (second_id, "spei-1.pdf"),
        (first_id, "spei-2.pdf"),
    ]


def test_payment_run_bulk_proof_form_ids_are_parsed_before_planning() -> None:
    document_id = uuid4()

    assert admin_routes._parse_payment_proof_document_ids(
        [str(document_id)], field="selected_document_ids"
    ) == [document_id]

    with pytest.raises(admin_routes.SolicitudValidationError) as exc:
        admin_routes._parse_payment_proof_document_ids(
            ["Dar formato al texto"], field="proof_document_ids"
    )

    assert exc.value.code == "payment_proof_form_invalid"
    assert "asignación" in str(exc.value)


def test_payment_proof_effective_dates_require_one_valid_date_per_payment() -> None:
    assert admin_routes._parse_effective_payment_dates(
        ["2026-09-22", "2026-09-23"], expected_count=2
    ) == [date(2026, 9, 22), date(2026, 9, 23)]

    with pytest.raises(admin_routes.SolicitudValidationError) as missing:
        admin_routes._parse_effective_payment_dates([], expected_count=1)
    assert missing.value.code == "effective_payment_date_required"

    with pytest.raises(admin_routes.SolicitudValidationError) as invalid:
        admin_routes._parse_effective_payment_dates(["22/09/2026"], expected_count=1)
    assert invalid.value.code == "effective_payment_date_invalid"

    with pytest.raises(admin_routes.SolicitudValidationError) as future:
        admin_routes._parse_effective_payment_dates(
            [(date.today() + timedelta(days=1)).isoformat()], expected_count=1
        )
    assert future.value.code == "effective_payment_date_future"


def test_payment_run_bulk_proof_plan_supports_one_proof_for_all_selected() -> None:
    first_id = uuid4()
    second_id = uuid4()
    shared_upload = SimpleNamespace(filename="spei-lote.pdf")

    plan = admin_routes._build_payment_proof_upload_plan(
        selected_document_ids=[first_id, second_id],
        proof_document_ids=[],
        uploads=[shared_upload],
        apply_one_to_all=True,
    )

    assert [(document_id, upload.filename) for document_id, upload in plan] == [
        (first_id, "spei-lote.pdf"),
        (second_id, "spei-lote.pdf"),
    ]


def test_payment_run_bulk_proof_plan_rejects_duplicate_mapping() -> None:
    first_id = uuid4()
    with pytest.raises(admin_routes.SolicitudValidationError) as exc:
        admin_routes._build_payment_proof_upload_plan(
            selected_document_ids=[first_id, uuid4()],
            proof_document_ids=[first_id, first_id],
            uploads=[
                SimpleNamespace(filename="spei-1.pdf"),
                SimpleNamespace(filename="spei-2.pdf"),
            ],
            apply_one_to_all=False,
        )

    assert exc.value.code == "duplicate_payment_proof_mapping"


@pytest.mark.parametrize(
    ("selected_ids", "mapped_ids", "uploads", "apply_one_to_all", "code"),
    [
        ([], [], [SimpleNamespace(filename="proof.pdf")], False, "payment_proof_selection_required"),
        ([uuid4(), uuid4()], [], [], False, "payment_proof_files_required"),
        ([uuid4(), uuid4()], [], [SimpleNamespace(filename="a.pdf"), SimpleNamespace(filename="b.pdf")], True, "single_proof_required"),
        ([uuid4()], [], [SimpleNamespace(filename="proof.pdf")], False, "payment_proof_mapping_required"),
    ],
)
def test_payment_run_bulk_proof_plan_rejects_incomplete_input(
    selected_ids, mapped_ids, uploads, apply_one_to_all, code
) -> None:
    with pytest.raises(admin_routes.SolicitudValidationError) as exc:
        admin_routes._build_payment_proof_upload_plan(
            selected_document_ids=selected_ids,
            proof_document_ids=mapped_ids,
            uploads=uploads,
            apply_one_to_all=apply_one_to_all,
        )
    assert exc.value.code == code


def test_payment_run_bulk_proof_plan_rejects_unselected_mapping() -> None:
    with pytest.raises(admin_routes.SolicitudValidationError) as exc:
        admin_routes._build_payment_proof_upload_plan(
            selected_document_ids=[uuid4()],
            proof_document_ids=[uuid4()],
            uploads=[SimpleNamespace(filename="proof.pdf")],
            apply_one_to_all=False,
        )
    assert exc.value.code == "payment_proof_mapping_invalid"


def test_payment_run_bulk_proof_plan_rejects_duplicate_selection() -> None:
    document_id = uuid4()
    with pytest.raises(admin_routes.SolicitudValidationError) as exc:
        admin_routes._build_payment_proof_upload_plan(
            selected_document_ids=[document_id, document_id],
            proof_document_ids=[],
            uploads=[SimpleNamespace(filename="proof.pdf")],
            apply_one_to_all=True,
        )
    assert exc.value.code == "duplicate_payment_proof_selection"


@pytest.mark.asyncio
@pytest.mark.parametrize("documento", [None, SimpleNamespace(id=uuid4(), estado="aprobado")])
async def test_payment_run_bulk_proof_upload_rolls_back_invalid_document(
    monkeypatch, documento
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(return_value=documento)
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(admin_routes, "require_payment_run_payment_confirmation", lambda _: None)
    response = await admin_routes.admin_finance_payment_run_upload_payment_proofs_bulk(
        request=SimpleNamespace(), session=session, current_empleado=SimpleNamespace(id=uuid4()),
        selected_document_ids=[document_id], proof_document_ids=[document_id],
        comprobantes_pago=[_PaymentProofUpload("proof.pdf", b"%PDF-1.4")],
        effective_payment_dates=["2026-09-22"],
        apply_one_to_all=False,
    )
    assert response.status_code == 303
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_run_bulk_proof_upload_redirects_invalid_form_values(monkeypatch) -> None:
    session = AsyncMock()
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(
        admin_routes, "require_payment_run_payment_confirmation", lambda _: None
    )

    response = await admin_routes.admin_finance_payment_run_upload_payment_proofs_bulk(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        selected_document_ids=[str(uuid4())],
        proof_document_ids=["Dar formato al texto"],
        comprobantes_pago=[_PaymentProofUpload("proof.pdf", b"%PDF-1.4")],
        effective_payment_dates=["2026-09-22"],
        apply_one_to_all=False,
    )

    assert response.status_code == 303
    assert "asignaci%C3%B3n" in response.headers["location"]
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["attachment", "payment"])
async def test_payment_run_single_proof_rolls_back_validation_failures(
    monkeypatch, failure
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(id=document_id, estado="en_proceso_pago")
    )
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(admin_routes, "require_payment_run_payment_confirmation", lambda _: None)
    if failure == "attachment":
        monkeypatch.setattr(
            admin_routes,
            "add_solicitud_documento_adjuntos",
            AsyncMock(side_effect=admin_routes.SolicitudValidationError("invalid", "invalid")),
        )
    else:
        monkeypatch.setattr(admin_routes, "add_solicitud_documento_adjuntos", AsyncMock())
        monkeypatch.setattr(
            documento_payment_service,
            "register_document_payment",
            AsyncMock(side_effect=documento_payment_service.DocumentoPaymentValidationError("invalid", "invalid")),
        )
    response = await admin_routes.admin_finance_payment_run_upload_payment_proof(
        documento_id=document_id, request=SimpleNamespace(), session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        comprobante_pago=_PaymentProofUpload("proof.pdf", b"%PDF-1.4"),
        fecha_pago_efectiva="2026-09-22",
    )
    assert response.status_code == 303
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_run_single_proof_validates_attachment_before_extraction(
    monkeypatch,
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(id=document_id, estado="en_proceso_pago")
    )
    review = MagicMock()
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(
        admin_routes, "require_payment_run_payment_confirmation", lambda _: None
    )
    monkeypatch.setattr(
        admin_routes,
        "validate_solicitud_terceros_attachment",
        lambda _: (_ for _ in ()).throw(
            admin_routes.SolicitudValidationError("invalid", "Archivo inválido")
        ),
    )
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        review,
    )

    response = await admin_routes.admin_finance_payment_run_upload_payment_proof(
        documento_id=document_id,
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        comprobante_pago=_PaymentProofUpload("proof.pdf", b"not a pdf"),
        fecha_pago_efectiva="2026-09-22",
    )

    assert response.status_code == 303
    review.assert_not_called()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_run_single_proof_conflict_shows_detected_and_programmed_values(
    monkeypatch,
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(return_value=_payment_proof_conflict_document(document_id))
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(
        admin_routes, "require_payment_run_payment_confirmation", lambda _: None
    )
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        lambda **_: PaymentProofReview(
            "conflict",
            date(2026, 9, 22),
            Decimal("100.00"),
            "MXN",
            "Beneficiario Detectado",
            "REF-1",
            ("El beneficiario detectado no coincide con el beneficiario programado.",),
        ),
    )

    response = await admin_routes.admin_finance_payment_run_upload_payment_proof(
        documento_id=document_id,
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        comprobante_pago=_PaymentProofUpload("proof.pdf", b"%PDF-1.4"),
        fecha_pago_efectiva="2026-09-22",
        payment_proof_resolution_reason=None,
    )

    assert response.status_code == 422
    assert b"Beneficiario Detectado" in response.body
    assert b"Beneficiario Programado" in response.body
    assert "Location" not in response.headers
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_run_blocks_santander_enviada_even_with_resolution_reason(
    monkeypatch,
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(return_value=_payment_proof_conflict_document(document_id))
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(
        admin_routes, "require_payment_run_payment_confirmation", lambda _: None
    )
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        lambda **_: PaymentProofReview(
            "revision_required",
            date(2026, 9, 22),
            Decimal("100.00"),
            "MXN",
            "Beneficiario Programado",
            "REF-1",
            ("El comprobante de Santander indica ENVIADA y no confirma un pago ejecutado.",),
            confirmation_blocked=True,
        ),
    )

    response = await admin_routes.admin_finance_payment_run_upload_payment_proof(
        documento_id=document_id,
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        comprobante_pago=_PaymentProofUpload("proof.pdf", b"%PDF-1.4"),
        fecha_pago_efectiva="2026-09-22",
        payment_proof_resolution_reason="La transferencia fue enviada.",
    )

    assert response.status_code == 422
    assert b"ENVIADA" in response.body
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_run_bulk_blocks_santander_enviada_even_with_resolution_reason(
    monkeypatch,
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(return_value=_payment_proof_conflict_document(document_id))
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(
        admin_routes, "require_payment_run_payment_confirmation", lambda _: None
    )
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        lambda **_: PaymentProofReview(
            "revision_required",
            date(2026, 9, 22),
            Decimal("100.00"),
            "MXN",
            "Beneficiario Programado",
            "REF-1",
            ("El comprobante de Santander indica ENVIADA y no confirma un pago ejecutado.",),
            confirmation_blocked=True,
        ),
    )

    response = await admin_routes.admin_finance_payment_run_upload_payment_proofs_bulk(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        selected_document_ids=[str(document_id)],
        proof_document_ids=[str(document_id)],
        comprobantes_pago=[_PaymentProofUpload("proof.pdf", b"%PDF-1.4")],
        effective_payment_dates=["2026-09-22"],
        payment_proof_resolution_reasons=["La transferencia fue enviada."],
        apply_one_to_all=False,
    )

    assert response.status_code == 422
    assert b"ENVIADA" in response.body
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_run_bulk_proof_conflict_shows_detected_and_programmed_values(
    monkeypatch,
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(return_value=_payment_proof_conflict_document(document_id))
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(
        admin_routes, "require_payment_run_payment_confirmation", lambda _: None
    )
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        lambda **_: PaymentProofReview(
            "conflict",
            None,
            Decimal("100.00"),
            "MXN",
            "Beneficiario Detectado",
            "REF-1",
            ("El beneficiario detectado no coincide con el beneficiario programado.",),
        ),
    )

    response = await admin_routes.admin_finance_payment_run_upload_payment_proofs_bulk(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        selected_document_ids=[str(document_id)],
        proof_document_ids=[str(document_id)],
        comprobantes_pago=[_PaymentProofUpload("proof.pdf", b"%PDF-1.4")],
        effective_payment_dates=["2026-09-22"],
        payment_proof_resolution_reasons=None,
        apply_one_to_all=False,
    )

    assert response.status_code == 422
    assert b"Beneficiario Detectado" in response.body
    assert b"Beneficiario Programado" in response.body
    assert "Location" not in response.headers
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_payment_run_single_proof_does_not_resolve_non_conflict_reason(
    monkeypatch,
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(id=document_id, estado="en_proceso_pago")
    )
    registered: dict[str, object] = {}
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(
        admin_routes, "require_payment_run_payment_confirmation", lambda _: None
    )
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    monkeypatch.setattr(admin_routes, "add_solicitud_documento_adjuntos", AsyncMock())
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        lambda **_: PaymentProofReview("match", None, None, None, None, None, ()),
    )

    async def register_payment(*_, **kwargs):
        registered.update(kwargs)
        return SimpleNamespace(
            documento=SimpleNamespace(id=document_id, numero_referencia="S-260001")
        )

    monkeypatch.setattr(documento_payment_service, "register_document_payment", register_payment)

    response = await admin_routes.admin_finance_payment_run_upload_payment_proof(
        documento_id=document_id,
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        comprobante_pago=_PaymentProofUpload("proof.pdf", b"%PDF-1.4"),
        fecha_pago_efectiva="2026-09-22",
        payment_proof_resolution_reason="texto no aplicable",
    )

    assert response.status_code == 303
    assert registered["payment_proof_review_status"] == "match"
    assert registered["payment_proof_resolution_reason"] is None


@pytest.mark.asyncio
async def test_payment_run_bulk_proof_does_not_resolve_non_conflict_reason(
    monkeypatch,
) -> None:
    document_id = uuid4()
    session = AsyncMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(id=document_id, estado="en_proceso_pago")
    )
    registered: dict[str, object] = {}
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(
        admin_routes, "require_payment_run_payment_confirmation", lambda _: None
    )
    monkeypatch.setattr(admin_routes, "validate_solicitud_terceros_attachment", lambda _: None)
    monkeypatch.setattr(admin_routes, "add_solicitud_documento_adjuntos", AsyncMock())
    monkeypatch.setattr(
        "devnous.gastos.services.payment_proof_review_service.review_payment_proof",
        lambda **_: PaymentProofReview("match", None, None, None, None, None, ()),
    )

    async def register_payment(*_, **kwargs):
        registered.update(kwargs)
        return SimpleNamespace(
            documento=SimpleNamespace(id=document_id, numero_referencia="S-260001")
        )

    monkeypatch.setattr(documento_payment_service, "register_document_payment", register_payment)
    monkeypatch.setattr(
        documento_payment_service,
        "_schedule_solicitud_paid_telegram_notifications",
        lambda **_: None,
    )

    response = await admin_routes.admin_finance_payment_run_upload_payment_proofs_bulk(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=uuid4()),
        selected_document_ids=[document_id],
        proof_document_ids=[document_id],
        comprobantes_pago=[_PaymentProofUpload("proof.pdf", b"%PDF-1.4")],
        effective_payment_dates=["2026-09-22"],
        payment_proof_resolution_reasons=["texto no aplicable"],
        apply_one_to_all=False,
    )

    assert response.status_code == 303
    assert registered["payment_proof_review_status"] == "match"
    assert registered["payment_proof_resolution_reason"] is None


@pytest.mark.asyncio
async def test_payment_run_bulk_proof_upload_commits_one_explicitly_mapped_batch(
    monkeypatch,
) -> None:
    actor_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    first_document = SimpleNamespace(id=first_id, estado="en_proceso_pago")
    second_document = SimpleNamespace(id=second_id, estado="en_proceso_pago")
    session = AsyncMock()
    session.get = AsyncMock(side_effect=[first_document, second_document])
    monkeypatch.setattr(admin_routes, "require_payment_run_access", lambda _: None)
    monkeypatch.setattr(
        admin_routes, "require_payment_run_payment_confirmation", lambda _: None
    )
    monkeypatch.setattr(
        admin_routes, "validate_solicitud_terceros_attachment", lambda _: None
    )
    attach_mock = AsyncMock()
    monkeypatch.setattr(admin_routes, "add_solicitud_documento_adjuntos", attach_mock)

    registered_effective_dates = []

    async def register_payment(*_, documento_id, **kwargs):
        registered_effective_dates.append(kwargs["fecha_pago_efectiva"])
        return SimpleNamespace(
            documento=SimpleNamespace(
                id=documento_id, numero_referencia=f"S-{str(documento_id)[:8]}"
            )
        )

    notifications = []
    monkeypatch.setattr(documento_payment_service, "register_document_payment", register_payment)
    monkeypatch.setattr(
        documento_payment_service,
        "_schedule_solicitud_paid_telegram_notifications",
        lambda **kwargs: notifications.append(kwargs),
    )

    response = await admin_routes.admin_finance_payment_run_upload_payment_proofs_bulk(
        request=SimpleNamespace(),
        session=session,
        current_empleado=SimpleNamespace(id=actor_id),
        selected_document_ids=[first_id, second_id],
        proof_document_ids=[second_id, first_id],
        comprobantes_pago=[
            _PaymentProofUpload("spei-2.pdf", b"%PDF-1.4 second"),
            _PaymentProofUpload("spei-1.pdf", b"%PDF-1.4 first"),
        ],
        effective_payment_dates=["2026-09-22", "2026-09-23"],
        apply_one_to_all=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("#comprobantes-pendientes")
    assert "vista=comprobantes" in response.headers["location"]
    assert attach_mock.await_count == 2
    session.commit.assert_awaited_once()
    assert [value.isoformat() for value in registered_effective_dates] == [
        "2026-09-22",
        "2026-09-23",
    ]
    assert [item["documento_id"] for item in notifications] == [second_id, first_id]


def test_payment_run_amount_issue_is_visible_and_not_selectable() -> None:
    document_id = uuid4()
    html = admin_routes._render_payment_run_items(
        [
            {
                "id": document_id,
                "numero_referencia": "S-260005",
                "concepto_pago": "Reembolso de saldo a favor",
                "monto": Decimal("0.00"),
                "currency": "MXN",
                "status": "programada",
                "can_close": False,
                "amount_issue": "Reembolso sin monto_total; requiere conciliacion.",
            }
        ]
    )

    assert "requiere conciliacion" in html
    assert f'name="document_ids" value="{document_id}"' not in html


def test_single_payment_proof_form_has_review_hooks() -> None:
    document_id = uuid4()
    html = admin_routes._render_payment_run_items(
        [{"id": document_id, "numero_referencia": "S-260099", "status": "en proceso de pago", "can_upload_payment_proof": True}],
        can_confirm_payment=True,
    )

    assert 'data-payment-proof-form' in html
    assert 'data-documento-id="' + str(document_id) + '"' in html
    assert 'data-payment-proof-effective-date' in html


def test_payment_proof_form_waits_for_review_before_submit() -> None:
    source = (
        Path(__file__).resolve().parents[3]
        / "src/devnous/gastos/routes/admin_routes.py"
    ).read_text(encoding="utf-8")

    assert "target.dataset.reviewStatus = 'checking'" in source
    assert "Espera a que termine la validación del comprobante." in source
    assert "Espera a que termine la validación de cada comprobante." in source


@pytest.mark.asyncio
async def test_payment_run_closure_preserves_empty_snapshotted_beneficiary(monkeypatch) -> None:
    closure_id = uuid4()
    header_result = MagicMock()
    header_result.mappings.return_value.first.return_value = {
        "id": closure_id,
        "total_amount": Decimal("100.00"),
    }
    items_result = MagicMock()
    items_result.mappings.return_value.all.return_value = [
        {
            "monto": Decimal("100.00"),
            "snapshot": {
                "payment_beneficiario": None,
                "payment_banco": "Banco de prueba",
                "payment_cuenta_bancaria": "1234567890",
                "payment_cuenta_clabe": None,
            },
            "beneficiario_nombre": "No debe usarse",
            "proveedor_nombre": "Tampoco debe usarse",
        }
    ]
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[header_result, items_result])
    monkeypatch.setattr(
        payment_run_service, "ensure_payment_run_schema", AsyncMock()
    )

    closure = await payment_run_service.get_payment_run_closure(
        session, closure_id=closure_id
    )

    assert closure is not None
    assert closure["items"][0]["beneficiario"] is None
    assert closure["items"][0]["payment_data_status"].startswith(
        "Falta beneficiario"
    )


@pytest.mark.asyncio
async def test_payment_run_page_rejects_non_finance_non_manager() -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_routes.admin_finance_payment_run(
            request=SimpleNamespace(query_params={}),
            session=AsyncMock(),
            current_empleado=SimpleNamespace(id=uuid4(), rol="operaciones", departamento="Operaciones"),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_payment_run_page_renders_fecha_pago_close_without_payment_proof_for_manager(
    monkeypatch,
) -> None:
    empleado_id = uuid4()
    documento_id = uuid4()
    monkeypatch.setenv(
        "SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS",
        str(empleado_id),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_items",
        AsyncMock(
            side_effect=[
                [
                    {
                        "id": documento_id,
                        "numero_referencia": "S-26000048",
                        "solicitante_nombre": "Benjamin",
                        "beneficiario_nombre": "Proveedor Demo",
                        "concepto_pago": "Uniformes",
                        "fecha_pago": None,
                        "monto": Decimal("1200.00"),
                        "currency": "MXN",
                        "status": "programada",
                        "can_edit_fecha_pago": True,
                        "can_close": True,
                        "can_upload_payment_proof": False,
                    },
                ],
                [
                    {
                        "id": uuid4(),
                        "numero_referencia": "S-26000049",
                        "solicitante_nombre": "Jacquie",
                        "beneficiario_nombre": "Proveedor Pago",
                        "concepto_pago": "Hospedaje",
                        "fecha_pago": None,
                        "monto": Decimal("900.00"),
                        "currency": "MXN",
                        "status": "en proceso de pago",
                        "can_edit_fecha_pago": False,
                        "can_close": False,
                        "can_upload_payment_proof": True,
                    },
                ],
            ]
        ),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_prestamo_payment_run_items",
        AsyncMock(side_effect=[[], []]),
    )

    response = await admin_routes.admin_finance_payment_run(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=empleado_id,
            rol="finanzas",
            nombre="Benjamin",
        ),
        status="pendientes",
        date_from=None,
        date_to=None,
        q=None,
    )
    html = response.body.decode("utf-8")

    assert "Programa de pagos" in html
    assert "Comprobantes pendientes - En Proceso de Pago" in html
    assert "/admin/finanzas/payment-run/documentos/" in html
    assert 'name="fecha_pago"' in html
    assert 'form="payment-run-close-form"' in html
    assert "/admin/finanzas/payment-run/pay" not in html
    assert "En Proceso de Pago" in html
    assert "Comprobante de pago" in html
    assert "comprobante-pago" not in html
    assert "Subir comprobante y marcar pagado" not in html
    assert "sin registrar pago" not in html
    assert "Finanzas ajusta la fecha de pago y cierra el corte operativo" in html
    assert "Contabilidad o un usuario autorizado adjunta el comprobante" in html
    assert "Benjamín ajusta fecha_pago" not in html
    assert "Dani, Sebas, Jacquie" not in html


@pytest.mark.asyncio
async def test_payment_run_page_queries_approved_and_in_process_sections(
    monkeypatch,
) -> None:
    list_mock = AsyncMock(side_effect=[[], []])
    monkeypatch.setattr(admin_routes, "list_payment_run_items", list_mock)
    loan_list_mock = AsyncMock(side_effect=[[], []])
    monkeypatch.setattr(admin_routes, "list_prestamo_payment_run_items", loan_list_mock)
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )

    await admin_routes.admin_finance_payment_run(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=uuid4(),
            rol="usuario",
            departamento="Contabilidad",
            nombre="Dani",
        ),
        status="pendientes",
        date_from=None,
        date_to=None,
        q="S-26000146",
    )

    assert list_mock.await_args_list[0].kwargs["status_filter"] == "pendientes"
    assert list_mock.await_args_list[1].kwargs["status_filter"] == "cerradas"
    assert list_mock.await_args_list[0].kwargs["query"] == "S-26000146"
    assert list_mock.await_args_list[1].kwargs["query"] == "S-26000146"
    assert loan_list_mock.await_args_list[0].kwargs["status_filter"] == "pendientes"
    assert loan_list_mock.await_args_list[1].kwargs["status_filter"] == "cerradas"
    assert loan_list_mock.await_args_list[0].kwargs["query"] == "S-26000146"
    assert loan_list_mock.await_args_list[1].kwargs["query"] == "S-26000146"


def test_payment_run_navigation_is_available_to_payment_confirmer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empleado_id = uuid4()
    monkeypatch.setenv(
        "SAMCHAT_PAYMENT_RUN_PAYMENT_CONFIRMER_EMPLOYEE_IDS",
        str(empleado_id),
    )

    navigation = admin_routes.render_admin_navigation(
        SimpleNamespace(
            id=empleado_id,
            nombre="Dani",
            rol="empleado",
            departamento="Contabilidad",
            visible_tool_keys={"panel.home"},
        )
    )

    assert 'href="/admin/finanzas/payment-run"' in navigation
    assert 'href="/admin/finanzas/payment-history"' not in navigation


@pytest.mark.asyncio
async def test_payment_run_gateway_allows_authorized_confirmer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empleado_id = uuid4()
    empleado = SimpleNamespace(id=empleado_id, activo=True)
    request = SimpleNamespace(
        session={"empleado_id": str(empleado_id)},
        url=SimpleNamespace(path="/admin/finanzas/payment-run"),
        method="GET",
    )
    monkeypatch.setattr(
        dependencies,
        "_load_empleado_proxy_by_id",
        AsyncMock(return_value=empleado),
    )
    monkeypatch.setattr(
        dependencies,
        "visible_tools_for",
        AsyncMock(return_value={"panel.home"}),
    )
    monkeypatch.setattr(
        dependencies,
        "can_access_path",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(dependencies, "can_access_payment_run", lambda _: True)

    resolved = await dependencies.get_current_empleado(request, AsyncMock())

    assert resolved is empleado
    assert resolved.can_access_path is True


@pytest.mark.asyncio
async def test_payment_run_hides_payment_date_editor_from_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documento_id = uuid4()
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_items",
        AsyncMock(
            side_effect=[
                [
                    {
                        "id": documento_id,
                        "numero_referencia": "S-26000048",
                        "solicitante_nombre": "Benjamin",
                        "beneficiario_nombre": "Proveedor Demo",
                        "concepto_pago": "Uniformes",
                        "fecha_pago": None,
                        "monto": Decimal("1200.00"),
                        "currency": "MXN",
                        "status": "programada",
                        "can_edit_fecha_pago": True,
                        "can_close": True,
                        "can_upload_payment_proof": False,
                    }
                ],
                [],
            ]
        ),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_prestamo_payment_run_items",
        AsyncMock(side_effect=[[], []]),
    )

    response = await admin_routes.admin_finance_payment_run(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=uuid4(),
            rol="empleado",
            departamento="Contabilidad",
            nombre="Dani",
        ),
        status="pendientes",
        date_from=None,
        date_to=None,
        q=None,
    )
    html = response.body.decode("utf-8")

    assert 'name="fecha_pago"' not in html
    assert 'name="document_ids"' not in html
    assert "Cerrar corte" not in html


@pytest.mark.asyncio
async def test_payment_run_close_uses_close_service(monkeypatch) -> None:
    empleado_id = uuid4()
    close_mock = AsyncMock(
        return_value=SimpleNamespace(
            item_count=2,
            total_amount=Decimal("1500.00"),
        )
    )
    monkeypatch.setenv(
        "SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS",
        str(empleado_id),
    )
    monkeypatch.setattr(admin_routes, "close_payment_run", close_mock)

    response = await admin_routes.admin_finance_payment_run_close(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(id=empleado_id, rol="finanzas"),
        document_ids=["doc-1", "doc-2"],
        notes="corte semanal",
        run_date="2026-07-31",
    )

    assert response.status_code == 303
    close_mock.assert_awaited_once()
    assert close_mock.await_args.kwargs["document_ids"] == [
        "doc-1",
        "doc-2",
    ]


@pytest.mark.asyncio
async def test_payment_run_order_export_requires_manager_and_returns_workbook(monkeypatch) -> None:
    manager_id = uuid4()
    closure = {"id": uuid4(), "items": [], "total_amount": Decimal("0.00")}
    monkeypatch.setenv("SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS", str(manager_id))
    monkeypatch.setattr(
        admin_routes,
        "get_payment_run_closure",
        AsyncMock(return_value=closure),
    )
    monkeypatch.setattr(
        admin_routes,
        "generate_payment_run_order_xlsx",
        lambda *, closure: b"payment-order",
    )

    response = await admin_routes.admin_finance_payment_run_closure_order_export(
        closure_id=str(closure["id"]),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(id=manager_id, rol="finanzas"),
    )

    assert response.status_code == 200
    assert response.body == b"payment-order"
    assert "orden_pago_corte_" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_payment_run_order_export_rejects_accounting_user() -> None:
    with pytest.raises(HTTPException) as exc:
        await admin_routes.admin_finance_payment_run_closure_order_export(
            closure_id=str(uuid4()),
            session=AsyncMock(),
            current_empleado=SimpleNamespace(
                id=uuid4(), rol="usuario", departamento="Contabilidad"
            ),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_payment_run_closure_detail_hides_full_bank_instructions(monkeypatch) -> None:
    manager_id = uuid4()
    closure_id = uuid4()
    monkeypatch.setenv("SAMCHAT_PAYMENT_RUN_MANAGER_EMPLOYEE_IDS", str(manager_id))
    monkeypatch.setattr(
        admin_routes,
        "get_payment_run_closure",
        AsyncMock(
            return_value={
                "id": closure_id,
                "item_count": 1,
                "total_amount": Decimal("1200.00"),
                "closed_at": "2026-09-07T10:00:00",
                "missing_payment_data_count": 0,
                "items": [
                    {
                        "numero_referencia": "S-260007",
                        "fecha_pago": "2026-09-08",
                        "monto": Decimal("1200.00"),
                        "currency": "MXN",
                        "estado_documento": "en_proceso_pago",
                        "pagado_en": None,
                        "cuenta_clabe": "012345678901234567",
                    }
                ],
            }
        ),
    )

    response = await admin_routes.admin_finance_payment_run_closure_detail(
        closure_id=str(closure_id),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=manager_id, rol="finanzas", nombre="Benjamin"
        ),
    )
    html = response.body.decode("utf-8")

    assert "012345678901234567" not in html
    assert "Descargar orden de pago" in html


@pytest.mark.asyncio
async def test_payment_run_page_renders_payment_proof_for_accounting(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_items",
        AsyncMock(
            side_effect=[
                [],
                [
                    {
                        "id": uuid4(),
                        "numero_referencia": "S-26000100",
                        "solicitante_nombre": "Dani",
                        "beneficiario_nombre": "Proveedor Pago",
                        "concepto_pago": "Hospedaje",
                        "fecha_pago": None,
                        "monto": Decimal("900.00"),
                        "currency": "MXN",
                        "status": "en proceso de pago",
                        "can_edit_fecha_pago": False,
                        "can_close": False,
                        "can_upload_payment_proof": True,
                    },
                ],
            ]
        ),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_prestamo_payment_run_items",
        AsyncMock(side_effect=[[], []]),
    )

    response = await admin_routes.admin_finance_payment_run(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=uuid4(),
            rol="usuario",
            departamento="Contabilidad",
            nombre="Dani",
        ),
        status="cerradas",
        date_from=None,
        date_to=None,
        q=None,
    )
    html = response.body.decode("utf-8")

    assert "Comprobantes pendientes - En Proceso de Pago" in html
    assert "comprobante-pago" in html
    assert "Subir comprobante y marcar pagado" in html
    assert "Programa de pagos" in html
    assert "Carga por lote de comprobantes" in html
    assert 'name="selected_document_ids"' in html
    assert "/admin/finanzas/payment-run/comprobantes-pago/lote" in html
    assert 'id="payment-proof-selected-inputs"' in html
    assert 'id="payment-proof-form-error"' in html
    assert "form.addEventListener('submit'" in html
    assert "checkbox.disabled = true" in html
    assert "uuidPattern.test(select.value)" in html
    assert 'aria-current="page">Comprobantes pendientes</a>' in html
    assert '<input type="hidden" name="vista" value="comprobantes">' in html
    assert '<section id="programa-de-pagos" class="workspace-card payment-run-view" style="margin-bottom:18px;" hidden>' in html


@pytest.mark.asyncio
async def test_payment_run_page_renders_approved_and_in_process_loans(
    monkeypatch,
) -> None:
    approved_id = uuid4()
    proof_id = uuid4()
    monkeypatch.setattr(admin_routes, "list_payment_run_items", AsyncMock(side_effect=[[], []]))
    monkeypatch.setattr(
        admin_routes,
        "list_prestamo_payment_run_items",
        AsyncMock(
            side_effect=[
                [
                    {
                        "id": approved_id,
                        "entity_type": "prestamo",
                        "numero_referencia": "PRE-26000010",
                        "solicitante_nombre": "Sebas",
                        "beneficiario_nombre": "Sebas",
                        "concepto_pago": "Prestamo viaje",
                        "fecha_pago": None,
                        "monto": Decimal("2000.00"),
                        "currency": "MXN",
                        "status": "programada",
                        "can_edit_fecha_pago": False,
                        "can_close": True,
                        "can_upload_payment_proof": False,
                    },
                ],
                [
                    {
                        "id": proof_id,
                        "entity_type": "prestamo",
                        "numero_referencia": "PRE-26000011",
                        "solicitante_nombre": "Dani",
                        "beneficiario_nombre": "Dani",
                        "concepto_pago": "Prestamo apoyo",
                        "fecha_pago": None,
                        "monto": Decimal("900.00"),
                        "currency": "MXN",
                        "status": "en proceso de pago",
                        "can_edit_fecha_pago": False,
                        "can_close": False,
                        "can_upload_payment_proof": True,
                    },
                ],
            ]
        ),
    )
    monkeypatch.setattr(
        admin_routes,
        "list_payment_run_closures",
        AsyncMock(return_value=[]),
    )

    response = await admin_routes.admin_finance_payment_run(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=uuid4(),
            rol="superadmin",
            departamento="Contabilidad",
            nombre="Superadmin",
        ),
        status="pendientes",
        date_from=None,
        date_to=None,
        q=None,
    )
    html = response.body.decode("utf-8")

    assert "PRE-26000010" in html
    assert "PRE-26000011" in html
    assert f'action="/prestamos/{approved_id}/programar-pago"' in html
    assert f'action="/prestamos/{proof_id}/comprobante-pago"' in html
    assert "Préstamo" in html


@pytest.mark.asyncio
async def test_payment_run_legacy_pay_endpoint_is_blocked() -> None:
    response = await admin_routes.admin_finance_payment_run_pay(
        request=SimpleNamespace(query_params={}),
        session=AsyncMock(),
        current_empleado=SimpleNamespace(
            id=uuid4(),
            rol="usuario",
            departamento="Contabilidad",
            nombre="Dani",
        ),
        document_ids=[str(uuid4())],
        year=None,
        month=None,
    )

    assert response.status_code == 303
    assert "/admin/finanzas/payment-run?error_msg=" in response.headers["location"]
    assert "comprobante" in response.headers["location"]


@pytest.mark.asyncio
async def test_payment_run_upload_requires_payment_proof() -> None:
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(estado="en_proceso_pago")

    response = await admin_routes.admin_finance_payment_run_upload_payment_proof(
        documento_id=uuid4(),
        request=SimpleNamespace(query_params={}),
        session=session,
        current_empleado=SimpleNamespace(
            id=uuid4(),
            rol="superadmin",
            departamento="Contabilidad",
            nombre="Superadmin",
        ),
        comprobante_pago=None,
    )

    assert response.status_code == 303
    assert "Selecciona%20el%20comprobante%20de%20pago" in response.headers["location"]
    assert "vista=comprobantes" in response.headers["location"]
    assert response.headers["location"].endswith("#comprobantes-pendientes")


def test_payment_run_upload_payment_proof_is_atomic() -> None:
    source = open("src/devnous/gastos/routes/admin_routes.py", encoding="utf-8").read()
    start = source.index("async def admin_finance_payment_run_upload_payment_proof")
    end = source.index("@router.get(\"/admin/finanzas/payment-run/closures", start)
    block = source[start:end]

    assert "commit=False" in block
    assert "await register_document_payment(" in block
    assert "actor=current_empleado" in block
    assert "comprobante" in block
    assert "testigo" not in block


def test_payment_run_bulk_payment_proof_is_atomic_and_keeps_mapping() -> None:
    source = open("src/devnous/gastos/routes/admin_routes.py", encoding="utf-8").read()
    start = source.index("async def admin_finance_payment_run_upload_payment_proofs_bulk")
    end = source.index("@router.get(\n    \"/admin/finanzas/payment-run/closures", start)
    block = source[start:end]

    assert "validate_solicitud_terceros_attachment(attachment)" in block
    assert "commit=False" in block
    assert "notify=False" in block
    assert "await session.commit()" in block
    assert "anchor=\"comprobantes-pendientes\"" in block


def test_accounting_profile_can_create_employee_beneficiary_requests() -> None:
    preset = admin_routes._PROFILE_PRESETS["contabilidad"]

    assert "finance.employee_beneficiary.request" in preset["permissions"]
    assert "contabilidad.pagos.marcar_pagado" in preset["permissions"]


def test_finance_dashboard_no_longer_renders_mass_mark_paid_form() -> None:
    source = open("src/devnous/gastos/routes/admin_routes.py", encoding="utf-8").read()
    start = source.index("async def admin_finance_platform")
    end = source.index("@router.get(\"/admin/finanzas/payment-run\"", start)
    block = source[start:end]

    assert "/admin/finanzas/payment-run/pay" not in block
    assert "Registrar seleccionados como pagados" not in block


def test_payment_run_tables_are_sortable_by_operational_reference() -> None:
    source = open("src/devnous/gastos/routes/admin_routes.py", encoding="utf-8").read()
    helper = source[
        source.index("def _admin_sortable_table_assets"):
        source.index("def _payment_run_badge")
    ]
    payment_run = source[
        source.index("@router.get(\"/admin/finanzas/payment-run\""):
        source.index("def _render_payment_history_rows")
    ]
    history_start = source.index("@router.get(\"/admin/finanzas/payment-history\"")
    payment_history = source[
        history_start:
        source.index("@router.post(\"/admin/finanzas/payment-run/documentos", history_start)
    ]

    assert "table[data-sortable-table]" in helper
    assert "header.cellIndex" in helper
    assert "_payment_run_sort_key" in source
    assert "_payment_run_ref_number" in source
    assert "for row in sorted(rows, key=_payment_run_sort_key)" in source
    for block in (payment_run, payment_history):
        assert "data-sortable-table" in block
        assert 'data-default-sort-dir="desc"' in block
        assert 'data-sort-key="referencia_operaciones"' in block
        assert 'data-sort-type="number"' in block
        assert "_admin_sortable_table_assets()" in block
