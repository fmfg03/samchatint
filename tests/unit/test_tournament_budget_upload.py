from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openpyxl import Workbook

from samchat.budgets import tournament_upload as upload


def workbook(rows=None, layout="LTTB", year=2026):
    book = Workbook()
    sheet = book.active
    sheet.title = f"{layout} {year}"
    sheet.append(
        ["Fase", "Partida", "Monto"]
        if layout == "LTTB"
        else ["tipo", "sub_proyecto", "partida", "monto"]
    )
    for row in rows or [["Estatal", "Pelotas", 10], ["Nacional", "Pelotas", 20]]:
        sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def parsed(rows=None):
    return upload.parse_tournament_budget(
        file_bytes=workbook(rows), filename="budget.xlsx", edition_year=2026
    )


def plan(data=None, concepts=None, lines=None, **kwargs):
    return upload.build_upload_plan(
        data or parsed(),
        tournament={"id": "t", "name": "Liga Telmex Telcel de Béisbol", "active": True},
        version={"id": "v", "edition_year": 2026, "status": "draft"},
        concepts=concepts or [],
        lines=lines or [],
        currency="MXN",
        tax_basis="includes_vat",
        **kwargs,
    )


def test_preserves_distinct_phases_and_evidence():
    p = plan()
    assert p["total"] == "30.00"
    assert len({r["concept_key"] for r in p["rows"]}) == 2
    assert p["blockers"] == []
    assert p["by_phase"] == {"Estatal": "10.00", "Nacional": "20.00"}
    assert p["rows"][0]["row"] == 2
    assert len(p["source_sha256"]) == 64
    assert p["read_only"] is True


@pytest.mark.parametrize("value", [None, "abc", -1, float("inf")])
def test_rejects_bad_amounts(value):
    with pytest.raises(ValueError):
        parsed([["Estatal", "Pelotas", value]])


def test_rejects_duplicate_same_phase():
    with pytest.raises(ValueError, match="duplicada"):
        parsed([["Estatal", "Pelotas", 10], ["Estatal", "Pelotas", 20]])


def test_rejects_wrong_edition():
    with pytest.raises(ValueError, match="edición"):
        upload.parse_tournament_budget(
            file_bytes=workbook(year=2025),
            filename="budget2026.xlsx",
            edition_year=2026,
        )


def test_rounding_requires_explicit_review():
    p = plan(parsed([["Estatal", "Pelotas", 1.005]]))
    assert p["total"] == "1.01"
    assert p["rows"][0]["original_amount"] == "1.005"
    assert any("redondeo" in b for b in p["blockers"])
    assert not plan(parsed([["Estatal", "Pelotas", 1.005]]), confirmed_rounding=True)[
        "blockers"
    ]


def test_existing_exact_line_is_idempotent_and_changes_conflict():
    data = parsed([["Estatal", "Pelotas", 10]])
    concepts = [
        {"id": "c", "tournament_id": "t", "concept_key": data["rows"][0]["concept_key"]}
    ]
    lines = [
        {
            "id": "l",
            "tournament_id": "t",
            "budget_version_id": "v",
            "budget_concept_id": "c",
            "phase": "Estatal",
            "budget_amount": 10,
            "metadata": {
                "upload_source": {
                    "source_sha256": data["source_sha256"],
                    "currency": "MXN",
                    "tax_basis": "includes_vat",
                }
            },
        }
    ]
    p = plan(data, concepts, lines)
    assert p["counts"] == {"unchanged": 1}
    lines[0]["budget_amount"] = 11
    changed = plan(data, concepts, lines)
    assert changed["counts"] == {"update": 1}
    assert changed["plan_sha256"] != p["plan_sha256"]
    assert changed["blockers"]


def test_legacy_lines_and_wrong_identity_block_instead_of_duplicating():
    p = plan(lines=[{"id": "l", "concept_name": "Pelotas", "budget_amount": 99}])
    assert p["blockers"]
    assert all(op["action"] == "conflict" for op in p["operations"])


def test_currency_tax_and_tournament_are_required():
    data = parsed()
    p = upload.build_upload_plan(
        data,
        tournament={"id": "t", "name": "Otro torneo", "active": True},
        version={"id": "v", "edition_year": 2025, "status": "approved"},
        concepts=[],
        lines=[],
    )
    assert len(p["blockers"]) == 5


@pytest.mark.asyncio
async def test_preview_never_provisions_or_writes(monkeypatch):
    class Result:
        def mappings(self):
            return self

        def first(self):
            return {"id": "t", "name": "Liga Telmex Telcel de Béisbol", "active": True}

    session = SimpleNamespace(execute=AsyncMock(return_value=Result()))
    monkeypatch.setattr(
        upload.service,
        "ensure_budget_schema",
        AsyncMock(side_effect=AssertionError("DDL")),
    )
    monkeypatch.setattr(
        upload.service,
        "get_budget_version",
        AsyncMock(return_value={"id": "v", "edition_year": 2026, "status": "draft"}),
    )
    monkeypatch.setattr(
        upload.service, "list_budget_concepts", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(upload.service, "list_budget_lines", AsyncMock(return_value=[]))
    p = await upload.preview_tournament_upload(
        session,
        tournament_id="t",
        version_id="v",
        edition_year=2026,
        file_bytes=workbook(),
        filename="b.xlsx",
    )
    assert p["read_only"]
    assert all(
        str(c.args[0]).lstrip().startswith("SELECT")
        for c in session.execute.call_args_list
    )
    assert upload.service.get_budget_version.call_args.kwargs["ensure_schema"] is False
    assert all(
        c.kwargs["ensure_schema"] is False
        for c in upload.service.list_budget_lines.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stale", "blocked", "write_failure"])
async def test_apply_rolls_back_and_never_commits_failed_plan(monkeypatch, failure):
    p = plan()
    if failure == "blocked":
        p["blockers"] = ["pending"]
    session = SimpleNamespace(
        execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock()
    )
    monkeypatch.setattr(upload, "preview_tournament_upload", AsyncMock(return_value=p))
    monkeypatch.setattr(
        upload.service,
        "create_budget_concept",
        AsyncMock(side_effect=ValueError("failed")),
    )
    with pytest.raises(ValueError):
        await upload.apply_tournament_upload(
            session,
            actor_empleado_id="a",
            confirmed=True,
            approved_plan_sha256="stale" if failure == "stale" else p["plan_sha256"],
            tournament_id="t",
            version_id="v",
        )
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_delegates_with_evidence_and_no_schema_bootstrap(monkeypatch):
    p = plan()
    session = SimpleNamespace(
        execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock()
    )
    monkeypatch.setattr(upload, "preview_tournament_upload", AsyncMock(return_value=p))
    monkeypatch.setattr(
        upload.service, "create_budget_concept", AsyncMock(return_value={"id": "c"})
    )
    monkeypatch.setattr(upload.service, "create_budget_line", AsyncMock())
    monkeypatch.setattr(upload.service, "_audit_budget_event", AsyncMock())
    result = await upload.apply_tournament_upload(
        session,
        actor_empleado_id="a",
        confirmed=True,
        approved_plan_sha256=p["plan_sha256"],
        tournament_id="t",
        version_id="v",
    )
    assert result["applied"]
    for call in upload.service.create_budget_line.call_args_list:
        assert call.kwargs["ensure_schema"] is False
        assert call.kwargs["commit"] is False
        assert call.kwargs["tournament_id"] == "t"
        assert (
            call.kwargs["extra_metadata"]["upload_source"]["source_sha256"]
            == p["source_sha256"]
        )
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_noop_repeat_creates_nothing(monkeypatch):
    p = plan()
    for op in p["operations"]:
        op["action"] = "unchanged"
    session = SimpleNamespace(
        execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock()
    )
    monkeypatch.setattr(upload, "preview_tournament_upload", AsyncMock(return_value=p))
    monkeypatch.setattr(upload.service, "create_budget_concept", AsyncMock())
    monkeypatch.setattr(upload.service, "create_budget_line", AsyncMock())
    monkeypatch.setattr(upload.service, "_audit_budget_event", AsyncMock())
    await upload.apply_tournament_upload(
        session,
        actor_empleado_id="a",
        confirmed=True,
        approved_plan_sha256=p["plan_sha256"],
        tournament_id="t",
        version_id="v",
    )
    upload.service.create_budget_line.assert_not_awaited()
    upload.service.create_budget_concept.assert_not_awaited()


def test_hwc_layout_and_precision_evidence():
    data = upload.parse_tournament_budget(
        file_bytes=workbook([["egreso", "Hospedaje", "Hotel", 1.255]], layout="HWC"),
        filename="HWC.xlsx",
        edition_year=2026,
    )
    assert data["layout"] == "HWC"
    assert data["total"] == "1.26"
    assert data["rows"][0]["original_amount"] == "1.255"


@pytest.mark.parametrize(
    "payload,filename",
    [
        (b"bad", "x.xlsx"),
        (b"x", "x.xls"),
        (b"", "x.xlsx"),
        (b"x" * (upload.MAX_UPLOAD_BYTES + 1), "x.xlsx"),
    ],
)
def test_bad_or_oversized_files_are_rejected(payload, filename):
    with pytest.raises(ValueError):
        upload.parse_tournament_budget(
            file_bytes=payload, filename=filename, edition_year=2026
        )


def test_reuses_existing_phase_identity_and_blocks_catalog_ambiguity():
    data = parsed([["Estatal", "Pelotas", 10]])
    c = {
        "id": "legacy",
        "tournament_id": "t",
        "concept_name": "Pelotas",
        "concept_key": "legacy-key",
        "metadata": {"applicable_phase_labels": ["Estatal"]},
    }
    p = plan(data, concepts=[c])
    assert p["operations"][0]["concept_id"] == "legacy"
    assert p["operations"][0]["action"] == "create"
    c2 = {**c, "id": "duplicate"}
    assert plan(data, concepts=[c, c2])["operations"][0]["action"] == "conflict"


@pytest.mark.asyncio
async def test_update_uses_canonical_owner_and_preserves_source(monkeypatch):
    p = plan()
    p["operations"][0].update(action="update", line_id="l", concept_id="c")
    p["operations"] = p["operations"][:1]
    session = SimpleNamespace(
        execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock()
    )
    monkeypatch.setattr(upload, "preview_tournament_upload", AsyncMock(return_value=p))
    monkeypatch.setattr(upload.service, "update_budget_line", AsyncMock())
    monkeypatch.setattr(upload.service, "_audit_budget_event", AsyncMock())
    await upload.apply_tournament_upload(
        session,
        actor_empleado_id="a",
        confirmed=True,
        approved_plan_sha256=p["plan_sha256"],
        tournament_id="t",
        version_id="v",
    )
    args = upload.service.update_budget_line.call_args.kwargs
    assert args["line_id"] == "l"
    assert args["updates"] == {"budget_amount": "10.00", "phase": "Estatal"}
    assert args["ensure_schema"] is False and args["commit"] is False
    assert args["extra_metadata"]["upload_source"]["row"] == 2


@pytest.mark.asyncio
async def test_canonical_creation_uses_exact_concept_and_only_links_new_line(
    monkeypatch,
):
    owner = upload.service
    monkeypatch.setattr(
        owner, "ensure_budget_schema", AsyncMock(side_effect=AssertionError("DDL"))
    )
    monkeypatch.setattr(
        owner, "get_budget_version", AsyncMock(return_value={"status": "draft"})
    )
    monkeypatch.setattr(
        owner,
        "get_budget_concept",
        AsyncMock(
            return_value={
                "id": "c",
                "active": True,
                "tournament_id": "t",
                "tournament_code": "LTTB",
                "tournament_name": "Liga Telmex Telcel de Béisbol",
                "concept_name": "Pelotas",
                "budget_direction": "expense",
            }
        ),
    )
    monkeypatch.setattr(
        owner,
        "resolve_budget_concept",
        AsyncMock(side_effect=AssertionError("global lookup")),
    )
    monkeypatch.setattr(
        owner,
        "_link_budget_lines_to_tournaments",
        AsyncMock(side_effect=AssertionError("global relink")),
    )
    monkeypatch.setattr(owner, "_audit_budget_event", AsyncMock())
    monkeypatch.setattr(owner.uuid, "uuid4", lambda: "new-line")
    monkeypatch.setattr(
        owner, "list_budget_lines", AsyncMock(return_value=[{"id": "new-line"}])
    )
    session = SimpleNamespace(
        execute=AsyncMock(), flush=AsyncMock(), commit=AsyncMock()
    )
    await owner.create_budget_line(
        session,
        version_id="v",
        budget_concept_id="c",
        tournament_id="t",
        concept_name="Pelotas",
        budget_amount="10",
        ensure_schema=False,
        commit=False,
        extra_metadata={"upload_source": {"row": 2}},
    )
    writes = session.execute.call_args_list
    assert "WHERE id = :line_id" in str(writes[1].args[0])
    assert writes[1].args[1] == {"tournament_id": "t", "line_id": "new-line"}
    assert owner.list_budget_lines.call_args.kwargs["tournament_id"] == "t"
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_canonical_update_retains_metadata_and_never_provisions(monkeypatch):
    owner = upload.service
    current = {
        key: None
        for key in (
            "account_code_final",
            "account_code_suggested",
            "priority",
            "owner_name",
            "criteria_note",
            "observations",
        )
    }
    current.update(
        id="l",
        tournament_id="t",
        budget_version_id="v",
        version_status="draft",
        budget_amount=10,
        reference_amount=0,
        budget_concept_id="c",
        tournament_code="LTTB",
        concept_name="Pelotas",
        line_direction="expense",
        phase="Estatal",
        metadata={"retained": True},
    )

    class Result:
        def mappings(self):
            return self

        def first(self):
            return current

    session = SimpleNamespace(
        execute=AsyncMock(return_value=Result()), flush=AsyncMock(), commit=AsyncMock()
    )
    monkeypatch.setattr(
        owner, "ensure_budget_schema", AsyncMock(side_effect=AssertionError("DDL"))
    )
    monkeypatch.setattr(owner, "_audit_budget_event", AsyncMock())
    monkeypatch.setattr(
        owner, "list_budget_lines", AsyncMock(return_value=[{"id": "l"}])
    )
    await owner.update_budget_line(
        session,
        line_id="l",
        tournament_id="t",
        updates={"budget_amount": "20"},
        ensure_schema=False,
        commit=False,
        extra_metadata={"upload_source": {"row": 2}},
    )
    import json

    payload = session.execute.call_args_list[1].args[1]
    assert json.loads(payload["metadata"]) == {
        "retained": True,
        "upload_source": {"row": 2},
    }
    assert payload["budget_amount"] == 20
    assert owner.list_budget_lines.call_args.kwargs["tournament_id"] == "t"
    session.commit.assert_not_awaited()


def test_multiphase_catalog_identity_is_blocked_instead_of_reused_twice():
    c = {
        "id": "shared",
        "tournament_id": "t",
        "concept_name": "Pelotas",
        "concept_key": "legacy",
        "metadata": {"applicable_phase_labels": ["Estatal", "Nacional"]},
    }
    p = plan(concepts=[c])
    assert p["counts"] == {"conflict": 2}
    assert any("identidad compartida" in b for b in p["blockers"])


def test_phase_update_is_explicit_in_preview_and_rollback_evidence():
    data = parsed([["Estatal", "Pelotas", 10]])
    c = {"id": "c", "tournament_id": "t", "concept_key": data["rows"][0]["concept_key"]}
    line = {
        "id": "l",
        "tournament_id": "t",
        "budget_version_id": "v",
        "budget_concept_id": "c",
        "budget_amount": 10,
        "phase": None,
        "metadata": {"upload_source": {"original": "source"}},
    }
    p = plan(data, concepts=[c], lines=[line], confirmed_updates=True)
    op = p["operations"][0]
    assert op["action"] == "update"
    assert op["phase"] == "Estatal" and op["before_phase"] is None
    assert op["before_upload_source"] == {"original": "source"}
    assert op["had_upload_source"] is True


def test_scoped_key_respects_canonical_column_size():
    with pytest.raises(ValueError, match="200 caracteres"):
        parsed([["b" * 30, "a" * 180, 10]])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "confirmed,actor,hash_value",
    [(False, "a", "hash"), (True, "", "hash"), (True, "a", "")],
)
async def test_missing_apply_authority_receipt_does_not_start_writes(
    confirmed, actor, hash_value
):
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError, match="actor, confirmación"):
        await upload.apply_tournament_upload(
            session,
            actor_empleado_id=actor,
            approved_plan_sha256=hash_value,
            confirmed=confirmed,
        )
    session.execute.assert_not_awaited()


@pytest.mark.parametrize("phase", ["estatal", " ESTATAL ", "Estátal"])
def test_legacy_phase_variants_block_duplicate_creation(phase):
    line = {
        "id": "legacy",
        "tournament_id": "t",
        "budget_version_id": "v",
        "concept_name": "Pelotas",
        "phase": phase,
        "budget_amount": 10,
    }
    p = plan(parsed([["Estatal", "Pelotas", 10]]), lines=[line])
    assert p["counts"] == {"conflict": 1}


def test_inactive_catalog_identity_blocks_preview_without_reactivation():
    data = parsed([["Estatal", "Pelotas", 10]])
    concept = {
        "id": "inactive",
        "tournament_id": "t",
        "active": False,
        "concept_key": data["rows"][0]["concept_key"],
        "concept_name": "Pelotas",
    }
    p = plan(data, concepts=[concept])
    assert p["counts"] == {"conflict": 1}
    assert p["operations"][0]["inactive_concept_ids"] == ["inactive"]
    assert any("inactiva" in b for b in p["blockers"])


def catalog_pair(data):
    scope = {"applicable_phase_labels": ["Estatal"]}
    return [
        {
            "id": "active",
            "tournament_id": "t",
            "active": True,
            "concept_key": "canonical",
            "concept_name": "Pelotas",
            "cuenta_contable_id": "expense-account",
            "pasivo_cuenta_contable_id": "liability",
            "metadata": scope,
        },
        {
            "id": "retired",
            "tournament_id": "t",
            "active": False,
            "concept_key": data["rows"][0]["concept_key"],
            "concept_name": "Pelotas",
            "cuenta_contable_id": "expense-account",
            "pasivo_cuenta_contable_id": "liability",
            "metadata": scope,
        },
    ]


def test_reuses_unique_active_account_match_preserving_retired_concept():
    from copy import deepcopy

    data = parsed([["Estatal", "Pelotas", 10]])
    concepts = catalog_pair(data)
    before = deepcopy(concepts)
    p = plan(data, concepts=concepts)
    assert not p["blockers"]
    assert p["operations"][0]["concept_id"] == "active"
    assert p["operations"][0]["retired_matching_concept_ids"] == ["retired"]
    assert concepts == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("cuenta_contable_id", "other"),
        ("pasivo_cuenta_contable_id", "other"),
        ("cuenta_contable_id", None),
        ("concept_name", "Otro"),
        ("budget_direction", "income"),
    ],
)
def test_incompatible_retired_identity_still_blocks(field, value):
    data = parsed([["Estatal", "Pelotas", 10]])
    concepts = catalog_pair(data)
    concepts[1][field] = value
    assert plan(data, concepts=concepts)["counts"] == {"conflict": 1}


def test_multiple_active_matches_with_retired_identity_still_block():
    data = parsed([["Estatal", "Pelotas", 10]])
    concepts = catalog_pair(data)
    concepts.append({**concepts[0], "id": "second"})
    assert plan(data, concepts=concepts)["counts"] == {"conflict": 1}


def test_reuse_updates_existing_line_and_records_source_before_state():
    data = parsed([["Estatal", "Pelotas", 10]])
    concepts = catalog_pair(data)
    line = {
        "id": "line",
        "tournament_id": "t",
        "budget_version_id": "v",
        "budget_concept_id": "active",
        "budget_amount": 7,
        "phase": "Estatal",
        "metadata": {},
    }
    p = plan(data, concepts=concepts, lines=[line], confirmed_updates=True)
    assert not p["blockers"]
    op = p["operations"][0]
    assert op["action"] == "update" and op["line_id"] == "line"
    assert op["before_amount"] == "7.00"


def test_retired_exact_key_does_not_override_conflicting_scope_evidence():
    data = parsed([["Estatal", "Pelotas", 10]])
    concepts = catalog_pair(data)
    concepts[1]["metadata"] = {"applicable_phase_labels": ["Nacional"]}
    assert plan(data, concepts=concepts)["counts"] == {"conflict": 1}


def test_retired_exact_key_without_scope_evidence_stays_blocked():
    data = parsed([["Estatal", "Pelotas", 10]])
    concepts = catalog_pair(data)
    concepts[1]["metadata"] = {}
    assert plan(data, concepts=concepts)["counts"] == {"conflict": 1}


def test_missing_liability_accounts_are_not_evidence_of_compatibility():
    data = parsed([["Estatal", "Pelotas", 10]])
    concepts = catalog_pair(data)
    for concept in concepts:
        concept["pasivo_cuenta_contable_id"] = None
    assert plan(data, concepts=concepts)["counts"] == {"conflict": 1}


@pytest.mark.parametrize("active_line", [False, True])
def test_retired_line_blocks_reuse_without_migrating_or_doubling(active_line):
    from copy import deepcopy

    data = parsed([["Estatal", "Pelotas", 10]])
    concepts = catalog_pair(data)
    retired = {
        "id": "old-line",
        "tournament_id": "t",
        "budget_version_id": "v",
        "budget_concept_id": "retired",
        "budget_amount": 7,
        "phase": "Estatal",
        "metadata": {},
    }
    lines = [retired]
    if active_line:
        lines.append({**retired, "id": "active-line", "budget_concept_id": "active"})
    before = deepcopy(lines)
    p = plan(data, concepts=concepts, lines=lines, confirmed_updates=True)
    assert p["counts"] == {"conflict": 1}
    assert lines == before
