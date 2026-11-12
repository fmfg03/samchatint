"""Closed pending correction schema and ledger transaction on disposable PG16."""

import asyncio
import copy
from uuid import UUID, uuid4

import pytest
from test_ctt_batch_acceptance_postgres import operational_case
from test_ctt_full_runtime_postgres import full_pg as _full_pg

from devnous.copa_telmex.staff_batch_contracts import digest
from devnous.copa_telmex.staff_batch_store import BatchDenied

pytest_plugins = ("test_ctt_staff_security_postgres",)
full_pg = _full_pg


def test_structured_pending_edit_binds_card_actor_source_and_clears_qualification(
    full_pg, tmp_path
):
    async def check():
        resources = []
        try:
            case = await operational_case(full_pg, tmp_path, resources, team_count=1)
            store, admin, context = case["store"], case["admin"], case["context"]
            account, run = UUID(context["account_id"]), UUID(context["run_id"])
            workflow_role = await case["pools"]["workflow"].fetchval(
                "SELECT session_user"
            )
            for signature in (
                "batch_member_detail(uuid,uuid,uuid)",
                "batch_propose_member_corrections(uuid,uuid,uuid,bigint,jsonb)",
            ):
                await admin.execute(
                    "GRANT EXECUTE ON FUNCTION ctt_staff_private."
                    + signature
                    + ' TO "'
                    + workflow_role.replace('"', '""')
                    + '"'
                )
            await store.create_run(account, run, case["initial"])
            worker = uuid4()
            lease = await store.claim_analysis(worker)
            analyzed = copy.deepcopy(case["initial"])
            box = dict(page_index=1, x=10, y=10, width=20, height=20)
            card = dict(page_index=1, x=0, y=0, width=100, height=100)
            for m in analyzed["members"]:
                m.update(
                    status="exception",
                    issues=[{"code": "FIELD_DISAGREEMENT"}],
                    snapshot={
                        "editable_fields": [
                            {
                                "field_id": "players.1.birth_date",
                                "label": "Fecha",
                                "value": None,
                                "editable": True,
                                "source_page_index": 1,
                                "kind": "source_field",
                                "rectangle": box,
                                "card_bounds": card,
                            }
                        ]
                    },
                )
            analyzed["input_hash"] = digest(
                {k: v for k, v in analyzed.items() if k != "input_hash"}
            )
            prepared = await store.complete_preparation(
                account, run, 1, worker, lease["lease_epoch"], analyzed
            )
            member = UUID(analyzed["members"][0]["member_id"])
            detail = await store.member(account, run, member)
            assert detail["fields"][0]["card_bounds"] == card
            assert detail["fields"][0]["kind"] == "source_field"
            edit = {
                "field_id": "players.1.birth_date",
                "value": {
                    "kind": "source_field",
                    "value": "2000-01-01",
                    "rectangle": box,
                },
            }
            for rectangle in (
                {**box, "x": 99},
                {**box, "page_index": 2},
                {**box, "width": 1.2},
                {**box, "provided": True},
            ):
                bad = copy.deepcopy(edit)
                bad["value"]["rectangle"] = rectangle
                with pytest.raises(BatchDenied):
                    await store.propose_corrections(
                        account, run, member, prepared["version"], [bad]
                    )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.review_batch_corrections"
                )
                == 0
            )
            outcome = await store.propose_corrections(
                account, run, member, prepared["version"], [edit]
            )
            assert (
                outcome["status"] == "analysis_pending"
                and outcome["version"] == prepared["version"] + 1
            )
            row = await admin.fetchrow(
                "SELECT principal_id,source_pages::text,edits::text "
                "FROM ctt_staff_private.review_batch_corrections"
            )
            import json

            assert str(row["principal_id"]) == context["principal_id"]
            assert (
                json.loads(row["source_pages"])
                == analyzed["members"][0]["source_pages"]
            )
            assert json.loads(row["edits"]) == [edit]
            assert (
                await admin.fetchval(
                    "SELECT qualification_id FROM ctt_staff_private.review_batch_runs "
                    "WHERE run_id=$1",
                    run,
                )
                is None
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM ctt_staff_private.capture_results"
                )
                == 0
            )
            with pytest.raises(BatchDenied):
                await store.propose_corrections(
                    account, run, member, prepared["version"], [edit]
                )
        finally:
            for resource in reversed(resources):
                await resource.close()

    asyncio.run(check())
