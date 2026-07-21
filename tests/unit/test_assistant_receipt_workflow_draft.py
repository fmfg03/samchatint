from types import SimpleNamespace

import pytest
from sqlalchemy.orm.attributes import get_history, set_committed_value

from devnous.gastos.models import AssistantConversation
from samchat.assistant.receipt_workflow_draft import (
    DRAFT_KEY,
    _explicit_followup_amount,
    advance_receipt_draft,
    start_receipt_draft,
)


class _Scalars:
    def all(self):
        return []


class _Result:
    def scalars(self):
        return _Scalars()


class _Session:
    def __init__(self):
        self.commits = 0

    async def execute(self, _statement):
        return _Result()

    async def commit(self):
        self.commits += 1


class _SequenceSession(_Session):
    def __init__(self, rows):
        super().__init__()
        self.rows = list(rows)

    async def execute(self, _statement):
        values = self.rows.pop(0) if self.rows else []

        class _SequenceScalars:
            def all(self):
                return values

        class _SequenceResult:
            def scalars(self):
                return _SequenceScalars()

        return _SequenceResult()


@pytest.mark.parametrize(
    ("raw_message", "expected"),
    [
        ("importe 1.00 MXN", "1.00"),
        ("monto $1,250.00", "1250.00"),
        ("importe 1.234", None),
        ("importe 0", None),
    ],
)
def test_explicit_followup_amount_fails_closed(
    raw_message: str, expected: str | None
) -> None:
    assert _explicit_followup_amount(raw_message) == expected


@pytest.mark.asyncio
async def test_receipt_draft_marks_json_metadata_dirty_when_collecting_inputs() -> None:
    conversation = AssistantConversation()
    set_committed_value(
        conversation,
        "metadata_",
        {
            DRAFT_KEY: {
                "draft_id": "receiptdraft-docint-1",
                "intake_id": "docint-1",
                "registry_hash": "registry-1",
                "evidence_sha256": "a" * 64,
                "media_id": "media-1",
                "amount": "1250.00",
                "date": "2026-07-20",
                "concept": "Material de oficina",
                "currency": "MXN",
                "payment_subject_type": None,
            }
        },
    )

    result = await advance_receipt_draft(
        raw_message="Es un gasto personal",
        conversation=conversation,
        employee_id="22222222-2222-2222-2222-222222222222",
        session=_Session(),
    )

    assert result is not None
    assert result.pending is None
    assert conversation.metadata_[DRAFT_KEY]["payment_subject_type"] == "personal"
    assert get_history(conversation, "metadata_").has_changes()


@pytest.mark.asyncio
async def test_receipt_draft_collects_explicit_followup_amount() -> None:
    evidence_hash = "a" * 64
    conversation = SimpleNamespace(
        metadata_={
            "assistant_last_media": {
                "id": "media-1",
                "evidence_sha256": evidence_hash,
            },
            "module_context": {
                "tournament_id": "11111111-1111-1111-1111-111111111111",
                "tournament_name": "Copa Telmex",
                "account_type": "local",
            },
        }
    )
    start_receipt_draft(
        conversation=conversation,
        intake={
            "intake_id": "docint-amount",
            "evidence_sha256": evidence_hash,
            "entities": {
                "date": "2026-07-20",
                "concept": "Material de oficina",
            },
        },
    )

    result = await advance_receipt_draft(
        raw_message="Es personal, importe 1.00 MXN",
        conversation=conversation,
        employee_id="22222222-2222-2222-2222-222222222222",
        session=_Session(),
    )

    assert result is not None
    assert result.pending is None
    assert "importe" not in result.message
    assert conversation.metadata_[DRAFT_KEY]["amount"] == "1.00"


@pytest.mark.asyncio
async def test_receipt_draft_builds_bound_personal_preview_without_writing() -> None:
    evidence_hash = "a" * 64
    conversation = SimpleNamespace(
        metadata_={
            "assistant_last_media": {
                "id": "media-1",
                "evidence_sha256": evidence_hash,
            },
            "module_context": {
                "tournament_id": "11111111-1111-1111-1111-111111111111",
                "tournament_name": "Copa Telmex",
                "payment_method": "Transferencia",
                "account_type": "local",
            },
        }
    )
    start_receipt_draft(
        conversation=conversation,
        intake={
            "intake_id": "docint-1",
            "evidence_sha256": evidence_hash,
            "entities": {
                "amount": "1250.00",
                "date": "2026-07-20",
                "concept": "Material de oficina",
                "currency": "MXN",
            },
        },
    )

    result = await advance_receipt_draft(
        raw_message="Es personal",
        conversation=conversation,
        employee_id="22222222-2222-2222-2222-222222222222",
        session=_Session(),
    )

    assert result is not None
    assert result.pending is not None
    tool_name, tool_args, preview = result.pending
    assert tool_name == "assistant_canonical_action"
    assert tool_args["action"] == "expenses.create_personal_receipt_workflow"
    assert tool_args["payload"]["evidence_sha256"] == evidence_hash
    assert "Cuenta de Gastos" in preview
    assert "expenses." not in preview


def test_receipt_draft_rejects_mismatched_media_evidence() -> None:
    conversation = SimpleNamespace(
        metadata_={
            "assistant_last_media": {
                "id": "media-1",
                "evidence_sha256": "a" * 64,
            }
        }
    )

    with pytest.raises(ValueError, match="does not match"):
        start_receipt_draft(
            conversation=conversation,
            intake={
                "intake_id": "docint-1",
                "evidence_sha256": "b" * 64,
                "entities": {},
            },
        )


@pytest.mark.asyncio
async def test_disabled_writes_return_preview_without_pending_confirmation() -> None:
    evidence_hash = "a" * 64
    conversation = SimpleNamespace(
        metadata_={
            "assistant_last_media": {
                "id": "media-1",
                "evidence_sha256": evidence_hash,
            },
            "module_context": {
                "tournament_id": "11111111-1111-1111-1111-111111111111",
                "tournament_name": "Copa Telmex",
                "payment_method": "Transferencia",
                "account_type": "local",
            },
        }
    )
    start_receipt_draft(
        conversation=conversation,
        intake={
            "intake_id": "docint-1",
            "evidence_sha256": evidence_hash,
            "entities": {
                "amount": "1250.00",
                "date": "2026-07-20",
                "concept": "Material de oficina",
            },
        },
    )

    result = await advance_receipt_draft(
        raw_message="Es personal",
        conversation=conversation,
        employee_id="22222222-2222-2222-2222-222222222222",
        session=_Session(),
        writes_enabled=False,
    )

    assert result is not None
    assert result.pending is None
    assert "no está habilitado" in result.message
    assert "confirmo" not in result.message


@pytest.mark.asyncio
async def test_third_party_draft_requires_and_binds_exact_budget_concept() -> None:
    evidence_hash = "a" * 64
    tournament_id = "11111111-1111-1111-1111-111111111111"
    provider = SimpleNamespace(
        id="33333333-3333-3333-3333-333333333333",
        nombre="Proveedor Uno",
    )
    budget_concept = SimpleNamespace(
        id="44444444-4444-4444-4444-444444444444",
        concept_name="Hospedaje",
    )
    conversation = SimpleNamespace(
        metadata_={
            "assistant_last_media": {
                "id": "media-1",
                "evidence_sha256": evidence_hash,
            },
            "module_context": {
                "tournament_id": tournament_id,
                "tournament_name": "Copa Telmex",
            },
        }
    )
    start_receipt_draft(
        conversation=conversation,
        intake={
            "intake_id": "docint-1",
            "evidence_sha256": evidence_hash,
            "entities": {"amount": "1250.00", "concept": "Hotel"},
        },
    )

    result = await advance_receipt_draft(
        raw_message="Pago a tercero Proveedor Uno, partida Hospedaje",
        conversation=conversation,
        employee_id="22222222-2222-2222-2222-222222222222",
        session=_SequenceSession([[], [provider], [budget_concept]]),
    )

    assert result is not None
    assert result.pending is not None
    _, tool_args, preview = result.pending
    assert tool_args["action"] == "expenses.create_third_party_receipt_workflow"
    assert tool_args["payload"]["provider_id"] == str(provider.id)
    assert tool_args["payload"]["budget_concept_id"] == str(budget_concept.id)
    assert "Hospedaje" in preview


@pytest.mark.asyncio
async def test_receipt_draft_resolves_unique_tournament_from_bi_scope() -> None:
    evidence_hash = "a" * 64
    tournament = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        name="Copa Telmex Telcel de Fútbol",
    )
    conversation = SimpleNamespace(
        metadata_={
            "assistant_last_media": {
                "id": "media-1",
                "evidence_sha256": evidence_hash,
            }
        }
    )
    start_receipt_draft(
        conversation=conversation,
        intake={
            "intake_id": "docint-scope",
            "evidence_sha256": evidence_hash,
            "entities": {
                "amount": "1250.00",
                "date": "2026-07-20",
                "concept": "Material de oficina",
            },
        },
    )

    result = await advance_receipt_draft(
        raw_message="Es personal, cuenta local, pagado por transferencia",
        conversation=conversation,
        employee_id="22222222-2222-2222-2222-222222222222",
        session=_SequenceSession([[tournament]]),
        writes_enabled=False,
        bi_year=2026,
        bi_scope="copa-telmex",
    )

    assert result is not None
    assert result.pending is None
    assert "Copa Telmex Telcel de Fútbol" in result.message
    assert "no está habilitado" in result.message
    assert "confirmo" not in result.message
    assert "assistant_receipt_workflow_draft" not in conversation.metadata_


@pytest.mark.asyncio
async def test_receipt_draft_does_not_guess_ambiguous_scope_tournament() -> None:
    evidence_hash = "a" * 64
    tournaments = [
        SimpleNamespace(
            id="11111111-1111-1111-1111-111111111111",
            name="Copa Telmex Zona Norte",
        ),
        SimpleNamespace(
            id="22222222-2222-2222-2222-222222222222",
            name="Copa Telmex Zona Sur",
        ),
    ]
    conversation = SimpleNamespace(
        metadata_={
            "assistant_last_media": {
                "id": "media-1",
                "evidence_sha256": evidence_hash,
            }
        }
    )
    start_receipt_draft(
        conversation=conversation,
        intake={
            "intake_id": "docint-ambiguous",
            "evidence_sha256": evidence_hash,
            "entities": {
                "amount": "1250.00",
                "date": "2026-07-20",
                "concept": "Material de oficina",
            },
        },
    )

    result = await advance_receipt_draft(
        raw_message="Es personal, cuenta local, pagado por transferencia",
        conversation=conversation,
        employee_id="33333333-3333-3333-3333-333333333333",
        session=_SequenceSession([tournaments]),
        writes_enabled=False,
        bi_year=2026,
        bi_scope="copa-telmex",
    )

    assert result is not None
    assert result.pending is None
    assert "torneo/proyecto exacto" in result.message
    draft = conversation.metadata_["assistant_receipt_workflow_draft"]
    assert draft["tournament_id"] is None


@pytest.mark.asyncio
async def test_receipt_draft_prefers_matching_year_within_scope() -> None:
    evidence_hash = "a" * 64
    tournaments = [
        SimpleNamespace(
            id="11111111-1111-1111-1111-111111111111",
            name="Copa Telmex 2025",
        ),
        SimpleNamespace(
            id="22222222-2222-2222-2222-222222222222",
            name="Copa Telmex 2026",
        ),
    ]
    conversation = SimpleNamespace(
        metadata_={
            "assistant_last_media": {
                "id": "media-1",
                "evidence_sha256": evidence_hash,
            }
        }
    )
    start_receipt_draft(
        conversation=conversation,
        intake={
            "intake_id": "docint-year",
            "evidence_sha256": evidence_hash,
            "entities": {
                "amount": "1250.00",
                "date": "2026-07-20",
                "concept": "Material de oficina",
            },
        },
    )

    result = await advance_receipt_draft(
        raw_message="Es personal, cuenta local, pagado por transferencia",
        conversation=conversation,
        employee_id="33333333-3333-3333-3333-333333333333",
        session=_SequenceSession([tournaments]),
        writes_enabled=False,
        bi_year=2026,
        bi_scope="copa-telmex",
    )

    assert result is not None
    assert result.pending is None
    assert "Copa Telmex 2026" in result.message
    assert "Copa Telmex 2025" not in result.message
