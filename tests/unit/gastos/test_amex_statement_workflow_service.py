"""Canonical ownership, atomicity and frozen export workflow boundaries."""

from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_statement_workflow_service as svc


class Nested:
    def __init__(self):
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, value, trace):
        self.rolled_back = kind is not None


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


@pytest.mark.parametrize("values", [[], ["bad"], [None], [str(uuid4())] * 2, None])
def test_explicit_selection_required(values):
    with pytest.raises(svc.WorkflowConflict):
        svc._selection(values)


def test_payload_order_stable_and_every_declaration_is_bound():
    a, b, statement, cut = uuid4(), uuid4(), uuid4(), uuid4()
    payload, digest = svc._payload(statement, cut, [a, b], " yes ", "confirm")
    assert (payload, digest) == svc._payload(statement, cut, [b, a], "yes", "confirm")
    assert digest != svc._payload(statement, cut, [a, b], "other", "confirm")[1]
    assert digest != svc._payload(statement, cut, [a, b], "yes", "incorporate")[1]
    assert payload["selected_informe_ids"] == sorted([str(a), str(b)])


@pytest.mark.asyncio
async def test_link_failure_rolls_back_financial_unit():
    nested = Nested()
    session = NS(begin_nested=lambda: nested)
    informe, cut, actor = uuid4(), NS(id=uuid4()), NS(id=uuid4())
    result = NS(status="created", cut=cut)
    with patch.object(svc, "_authorize", AsyncMock(return_value=actor)), patch.object(
        svc, "_statement", AsyncMock()
    ), patch.object(
        svc, "_sources", AsyncMock(return_value=[NS(id=informe)])
    ), patch.object(
        svc, "confirm_amex_approved_informe_cut", AsyncMock(return_value=result)
    ), patch.object(
        svc, "_link", AsyncMock(side_effect=svc.WorkflowConflict("receipt failure"))
    ):
        with pytest.raises(svc.WorkflowConflict):
            await svc.confirm_statement_informe(
                session,
                import_id=uuid4(),
                informe_id=informe,
                actor=actor,
                selected_informe_ids=[informe],
                confirmed=True,
            )
    assert nested.rolled_back


@pytest.mark.asyncio
async def test_pending_canonical_result_never_creates_link():
    nested = Nested()
    session = NS(begin_nested=lambda: nested)
    informe, actor = uuid4(), NS(id=uuid4())
    result = NS(status="pending", cut=None)
    with patch.object(svc, "_authorize", AsyncMock(return_value=actor)), patch.object(
        svc, "_statement", AsyncMock()
    ), patch.object(
        svc, "_sources", AsyncMock(return_value=[NS(id=informe)])
    ), patch.object(
        svc, "confirm_amex_approved_informe_cut", AsyncMock(return_value=result)
    ), patch.object(
        svc, "_link", AsyncMock()
    ) as link:
        assert (
            await svc.confirm_statement_informe(
                session,
                import_id=uuid4(),
                informe_id=informe,
                actor=actor,
                selected_informe_ids=[informe],
                confirmed=True,
            )
            is result
        )
        link.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_cannot_cross_selected_report():
    nested = Nested()
    session = NS(begin_nested=lambda: nested)
    with patch.object(svc, "_authorize", AsyncMock()), patch.object(
        svc, "_statement", AsyncMock()
    ), patch.object(
        svc, "_sources", AsyncMock(return_value=[NS(id=uuid4())])
    ), patch.object(
        svc, "confirm_amex_approved_informe_cut", AsyncMock()
    ) as owner:
        with pytest.raises(svc.WorkflowConflict):
            await svc.confirm_statement_informe(
                session,
                import_id=uuid4(),
                informe_id=uuid4(),
                actor=NS(id=uuid4()),
                selected_informe_ids=[uuid4()],
            )
        owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_insert_receipt_and_exact_replay_preserve_original_actor():
    actor = NS(id=uuid4())
    cut = NS(id=uuid4())
    statement, informe = uuid4(), uuid4()
    added = []
    session = NS(
        execute=AsyncMock(return_value=Result()), add=added.append, flush=AsyncMock()
    )
    first = await svc._link(
        session,
        import_id=statement,
        cut=cut,
        actor=actor,
        selected_informe_ids=[informe],
        reason="why",
        mode="confirm",
    )
    assert added == [first] and first.actor_id == actor.id
    session.execute.return_value = Result([first])
    replay = await svc._link(
        session,
        import_id=statement,
        cut=cut,
        actor=NS(id=uuid4()),
        selected_informe_ids=[informe],
        reason="why",
        mode="confirm",
    )
    assert replay is first and replay.actor_id == actor.id and len(added) == 1
    with pytest.raises(svc.WorkflowConflict):
        await svc._link(
            session,
            import_id=statement,
            cut=cut,
            actor=actor,
            selected_informe_ids=[informe],
            reason="different",
            mode="confirm",
        )


@pytest.mark.asyncio
async def test_export_invalid_second_cut_fails_without_partial_file():
    ids = [uuid4(), uuid4()]
    informe = uuid4()
    statement = uuid4()
    cuts = [NS(id=value, informe_id=informe) for value in ids]
    links = []
    for value in ids:
        payload, digest = svc._payload(statement, value, [informe], "", "confirm")
        links.append(
            NS(cut_id=value, request_payload_json=payload, request_payload_hash=digest)
        )
    session = NS(
        no_autoflush=nullcontext(),
        execute=AsyncMock(side_effect=[Result(links), Result(cuts)]),
    )
    with patch.object(svc, "_authorize", AsyncMock()), patch.object(
        svc, "_statement", AsyncMock()
    ), patch.object(
        svc, "cut_expense_cfdis", side_effect=[[NS()], ValueError("invalid")]
    ), patch.object(
        svc, "generate_coi_poliza_csv"
    ) as generator:
        with pytest.raises(ValueError):
            await svc.export_statement(
                session, import_id=uuid4(), actor=NS(id=uuid4()), export_format="csv"
            )
        generator.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored", [None, NS(activo=False, rol="finanzas"), NS(activo=True, rol="empleado")]
)
async def test_inactive_or_nonfinance_actor_denied(stored):
    with pytest.raises(svc.WorkflowConflict):
        await svc._authorize(NS(get=AsyncMock(return_value=stored)), NS(id=uuid4()))


@pytest.mark.asyncio
async def test_missing_statement_is_explicit():
    with pytest.raises(svc.WorkflowConflict):
        await svc._statement(NS(execute=AsyncMock(return_value=Result())), uuid4())


@pytest.mark.asyncio
async def test_missing_selected_report_is_not_a_fabricated_source():
    with pytest.raises(svc.WorkflowConflict):
        await svc._sources(NS(execute=AsyncMock(return_value=Result())), [uuid4()])


@pytest.mark.asyncio
async def test_no_confirmation_never_incorporates():
    with patch.object(svc, "_authorize", AsyncMock()):
        with pytest.raises(svc.WorkflowConflict):
            await svc.incorporate_statement_cut(
                NS(),
                import_id=uuid4(),
                cut_id=uuid4(),
                actor=NS(id=uuid4()),
                selected_informe_ids=[uuid4()],
                confirmed=False,
            )


@pytest.mark.asyncio
async def test_export_rejects_a_corrupt_navigation_receipt_before_generator():
    statement, cut_id, informe = uuid4(), uuid4(), uuid4()
    payload, digest = svc._payload(statement, cut_id, [informe], "", "confirm")
    link = NS(
        cut_id=cut_id, request_payload_json=payload, request_payload_hash="f" * 64
    )
    session = NS(
        no_autoflush=nullcontext(),
        execute=AsyncMock(
            side_effect=[Result([link]), Result([NS(id=cut_id, informe_id=informe)])]
        ),
    )
    with patch.object(svc, "_authorize", AsyncMock()), patch.object(
        svc, "_statement", AsyncMock()
    ), patch.object(svc, "generate_coi_poliza_csv") as generator:
        with pytest.raises(svc.WorkflowConflict):
            await svc.export_statement(
                session, import_id=statement, actor=NS(id=uuid4()), export_format="csv"
            )
        generator.assert_not_called()
