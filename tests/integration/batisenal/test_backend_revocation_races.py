"""A5 native lock order and post-commit release; no synthetic pass counters."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from test_backend_case_authority import app as app_fixture
from test_backend_case_authority import authorized as authorized_fixture
from test_backend_case_authority import graph as graph_fixture
from test_backend_case_operations import (
    context,
    invoke,
    proposal,
)
from test_backend_case_operations import service as service_fixture

from devnous.gastos.services.batisenal_service import BatisenalError

app, graph, authorized, service = (
    app_fixture,
    graph_fixture,
    authorized_fixture,
    service_fixture,
)


async def wait_for_native_lock(raw_engine):
    for _ in range(200):
        with raw_engine.connect() as observer:
            waiting = observer.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE"
                    " datname=current_database() AND wait_event_type='Lock' AND query"
                    " LIKE '%support_batisenal_v01_begin%' AND pid<>pg_backend_pid()"
                )
            ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("native writer never waited on protocol lock")


@pytest.mark.parametrize(
    "change,authorized", [("revoke", 3600), ("expire", 8)], indirect=["authorized"]
)
async def test_revocation_commits_before_waiting_submit_rechecks_authority(
    service, authorized, change
):
    snapshot = await context(service, authorized)
    body = proposal(snapshot)
    with authorized.graph.engine.connect() as controller:
        tx = controller.begin()
        task = asyncio.create_task(
            invoke(service, authorized, "submit_case_draft", body)
        )
        try:
            await wait_for_native_lock(authorized.graph.raw)
            if change == "revoke":
                controller.execute(text("UPDATE private_oauth_grants SET revoked=true"))
            else:
                # The full immutable hour window was initially inserted near its end.
                # Hold the real lock until PostgreSQL's clock crosses that deadline.
                for _ in range(120):
                    expired = controller.execute(
                        text(
                            "SELECT clock_timestamp() >= to_timestamp(expires_at) "
                            "FROM support_batisenal_pilot_fixtures"
                        )
                    ).scalar_one()
                    if expired:
                        break
                    await asyncio.sleep(0.1)
                assert expired
            tx.commit()
            with pytest.raises(BatisenalError):
                await task
        finally:
            if tx.is_active:
                tx.rollback()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    with authorized.graph.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_v01_operations")
            ).scalar_one()
            == 0
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_agent_drafts")
            ).scalar_one()
            == 0
        )


async def test_submit_commits_before_queued_revoke_but_never_reveals_after_revocation(
    service, authorized, monkeypatch
):
    snapshot = await context(service, authorized)
    body = proposal(snapshot)
    inserted, release = asyncio.Event(), asyncio.Event()
    revoked = asyncio.Event()
    original_submit, original_authority = service.submit, service.authority
    calls = 0

    async def held_submit(*args, **kwargs):
        result = await original_submit(*args, **kwargs)
        inserted.set()
        await release.wait()
        return result

    async def final_check(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            await revoked.wait()
        return await original_authority(*args, **kwargs)

    monkeypatch.setattr(service, "submit", held_submit)
    monkeypatch.setattr(service, "authority", final_check)
    submit_task = asyncio.create_task(
        invoke(service, authorized, "submit_case_draft", body)
    )
    await asyncio.wait_for(inserted.wait(), 5)

    def revoke():
        with authorized.graph.raw.connect() as c:
            tx = c.begin()
            # Blocking native guard, unlike the sync event-loop NOWAIT adapter.
            c.execute(
                text(
                    "SELECT * FROM support_batisenal_v01_begin(CAST(:case AS"
                    " uuid),false)"
                ),
                {"case": authorized.graph.case_id},
            )
            c.execute(text("UPDATE private_oauth_grants SET revoked=true"))
            tx.commit()

    executor = ThreadPoolExecutor(max_workers=1)
    future = asyncio.get_running_loop().run_in_executor(executor, revoke)
    try:
        await wait_for_native_lock(authorized.graph.raw)
        release.set()
        await asyncio.wait_for(future, 5)
        revoked.set()
        with pytest.raises(BatisenalError):
            await asyncio.wait_for(submit_task, 5)
    finally:
        release.set()
        revoked.set()
        if not submit_task.done():
            submit_task.cancel()
        await asyncio.gather(submit_task, return_exceptions=True)
        executor.shutdown(wait=True)
    with authorized.graph.engine.begin() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_v01_operations")
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM support_batisenal_agent_drafts")
            ).scalar_one()
            == 1
        )
