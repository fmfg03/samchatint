"""Real owner authorization for picked invoices, without SAT network effects."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from devnous.gastos import models
from devnous.gastos.services import amex_direct_cfdi_search_service as search
from devnous.gastos.services import amex_direct_status_service as status
from devnous.gastos.services.access_control_service import empleado_area, empleado_role
from devnous.gastos.services.amex_direct_common import DirectAccessError, digest
from devnous.gastos.services.amex_direct_matching_service import cfdi_identity
from devnous.gastos.services.cfdi_ingestion_service import ingest_cfdi_xml

_spec = importlib.util.spec_from_file_location(
    "amex_selected_authority_fixture",
    Path(__file__).with_name("test_amex_sat_budget_direct_postgres.py"),
)
owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(owner)
direct_pg = owner.direct_pg


async def seed_selection(pg):
    async with pg.factory.begin() as session:
        cfdi = (
            await ingest_cfdi_xml(session, owner._synthetic_sat_xml(), source="sat")
        ).cfdi_report
    imported = await owner._direct_import(
        pg, b"Date,Description,Amount\n2026-10-02,ACME,-116.00\n"
    )
    async with pg.factory() as session:
        state = await session.get(
            models.AmexStatementDirectState, imported.statement.id
        )
        item = (
            await session.execute(
                select(models.AmexDirectItem).where(
                    models.AmexDirectItem.import_id == imported.statement.id
                )
            )
        ).scalar_one()
        args = dict(
            actor_id=pg.actor.id,
            import_id=imported.statement.id,
            item_id=item.id,
            cfdi_id=cfdi.id,
            expected_source_version=state.source_version,
            expected_item_version=item.source_version,
            expected_identity_hash=digest(cfdi_identity(cfdi)),
            request_key=str(uuid4()),
            consented=True,
        )
    return args


async def deny(pg, action):
    async with pg.factory.begin() as session:
        session.add(
            models.AccessControlRule(
                tool_key="amex_direct",
                action_key=action,
                role_key=empleado_role(pg.actor),
                area_key=empleado_area(pg.actor),
                allowed=False,
                active=True,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", ["vigencia", "ver", "editar"])
async def test_selected_status_actual_denial_prevents_external_call(direct_pg, denied):
    pg = direct_pg
    args = await seed_selection(pg)
    await deny(pg, denied)
    observed = owner.ObservedSAT("vigente")
    with pytest.raises(DirectAccessError):
        await status.consult_selected_direct_cfdi_status(
            pg.factory, **args, status_service=observed
        )
    assert len(observed.requests) == 0
    assert await owner._count(pg, models.AmexCFDIStatusReceipt) == 0


@pytest.mark.asyncio
async def test_conciliar_denial_does_not_silently_revoke_independent_vigencia(
    direct_pg,
):
    pg = direct_pg
    args = await seed_selection(pg)
    await deny(pg, "conciliar")
    observed = owner.ObservedSAT("vigente")
    result = await status.consult_selected_direct_cfdi_status(
        pg.factory, **args, status_service=observed
    )
    assert result.status == "observed" and len(observed.requests) == 1
    async with pg.factory() as session:
        with pytest.raises(DirectAccessError):
            await search.search_direct_cfdis(
                session,
                actor_id=pg.actor.id,
                import_id=args["import_id"],
                item_id=args["item_id"],
                navigation={},
            )


@pytest.mark.asyncio
async def test_status_permission_revoked_during_external_call_discards_receipt(
    direct_pg,
):
    pg = direct_pg
    args = await seed_selection(pg)

    class RevokingSAT(owner.ObservedSAT):
        async def consult_status(self, **kwargs):
            result = await super().consult_status(**kwargs)
            await deny(pg, "vigencia")
            return result

    observed = RevokingSAT("vigente")
    with pytest.raises(DirectAccessError):
        await status.consult_selected_direct_cfdi_status(
            pg.factory, **args, status_service=observed
        )
    assert len(observed.requests) == 1
    assert await owner._count(pg, models.AmexCFDIStatusReceipt) == 0
