"""Isolated QA reproduction of current multicharge AMEX export failure.

Run against an authorized disposable PostgreSQL only, never production:
TEST_AMEX_DATABASE_URL must satisfy the repository fixture's strict local guard.
Synthetic two-charge case only. It never reads activity.csv or live credentials.
This test intentionally remains red until the implementation is corrected.
"""
import importlib.util
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("AMEX_QA_REPO_ROOT", Path.cwd())).resolve()
OWNER = ROOT / "tests/acceptance/test_amex_sat_budget_direct_postgres.py"
if not OWNER.is_file():
    raise RuntimeError("Set AMEX_QA_REPO_ROOT to the inspected SamChat repository root")
spec = importlib.util.spec_from_file_location("amex_qa_acceptance_owner", OWNER)
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
direct_pg = owner.direct_pg
REPORT = Path("/tmp/amex-qa-isolated-minimal-reproduction-result.json")

def record(key, value):
    data = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    data[key] = value
    REPORT.write_text(json.dumps(data, indent=2, default=str))

@pytest.mark.asyncio
async def test_two_charge_coi_export_must_succeed_after_balanced_cut(direct_pg):
    from devnous.gastos.services import amex_direct_accounting_service as accounting
    pg=direct_pg
    current=await owner._cross_calendar_state(pg)
    args=await owner._cut_args(pg,current)
    args['accounting_date']=date(2026,10,15)
    result=await owner._confirm(pg,args)
    assert result.status=='recognized',result.reason
    debit=await pg.conn.fetchval('SELECT sum(debe) FROM accounting_poliza_lines')
    credit=await pg.conn.fetchval('SELECT sum(haber) FROM accounting_poliza_lines')
    assert debit==credit==348
    snapshot=await pg.conn.fetchval('SELECT snapshot_json FROM amex_direct_accounting_cuts')
    if isinstance(snapshot,str):snapshot=json.loads(snapshot)
    flat=[line for item in snapshot['partidas'] for line in item.get('journal_lines',[])]
    actual=snapshot['lines']
    def key(r):return (str(r['cuenta_contable_id']),r['cuenta_codigo'],Decimal(str(r['debe'])),Decimal(str(r['haber'])))
    from collections import Counter
    record('two_charge_export_minimal',dict(debits=2,polizas=1,journal_balanced=True,total='348.00',global_line_count=len(actual),flattened_line_count=len(flat),ordered_identity_equal=[key(r) for r in flat]==[key(r) for r in actual],multiset_identity_equal=Counter(key(r) for r in flat)==Counter(key(r) for r in actual),global_movements=['debit' if Decimal(str(r['debe'])) else 'credit' for r in actual],flattened_movements=['debit' if Decimal(str(r['debe'])) else 'credit' for r in flat],synthetic=True))
    errors={}
    for fmt in ('xlsx','csv','zip'):
        try:
            async with pg.factory() as session:
                await accounting.export_direct_cut(session,actor_id=pg.actor.id,import_id=current.statement.id,format=fmt)
        except Exception as exc:errors[fmt]=dict(type=type(exc).__name__,message=str(exc))
    record('two_charge_export_errors',errors)
    assert not errors,errors
