"""UI summaries never grant accounting readiness or discard financial evidence."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from lxml import html

from devnous.gastos.routes import amex_workspace_ui as ui


def item(**changes):
    value = dict(
        id=uuid4(),
        accounting_status="pending",
        recognized_poliza_ids=[],
        movement_kind="debit",
        treatment_kind="benefit_expense",
        budget_status="assigned",
    )
    return NS(**(value | changes))


def preview(**changes):
    return NS(**(dict(status="pending", progress={}, cut=None) | changes))


def test_clean_individual_row_never_becomes_ready_under_a_global_blocker():
    row = item()
    state = preview(progress={"exceptions": ["fiscal_journal_unbalanced"]})
    assert ui.row_bucket(row, state) == "review"
    markup, counts = ui.workspace_summary([row], state)
    assert counts == {"review": 1}
    assert "Sin bloqueos individuales" in markup
    assert "Listos para póliza" not in markup
    assert "estado completo" in markup


@pytest.mark.parametrize("frozen", ["cut", "completion"])
def test_cut_or_completion_never_offers_new_ready_rows(frozen):
    state = preview(status="ready")
    if frozen == "cut":
        state.cut = NS(id=uuid4())
    else:
        state.progress = {"completion_receipt": {"id": str(uuid4())}}
    assert ui.row_bucket(item(), state) != "ready"


def test_only_owner_ready_can_present_a_new_debit_as_ready():
    row = item()
    assert ui.row_bucket(row, preview(status="ready")) == "ready"
    markup, counts = ui.workspace_summary([row], preview(status="ready"))
    assert counts["ready"] == 1 and "Listos para póliza" in markup


def test_sent_benefit_and_project_are_jp_handoff_not_all_budget_issues():
    state = preview()
    assert ui.row_bucket(item(budget_status="sent"), state) == "budget"
    assert (
        ui.row_bucket(
            item(budget_status="sent", treatment_kind="project_expense"), state
        )
        == "budget"
    )
    assert (
        ui.row_bucket(
            item(budget_status="sent", treatment_kind="partner_receivable"), state
        )
        == "review"
    )
    row = item(budget_status="classified")
    state.progress = {"issues": [{"item_id": str(row.id), "reason": "budget_required"}]}
    assert ui.row_bucket(row, state) == "pending"


def test_credit_unknown_and_prior_recognition_retain_their_dispositions():
    credit, unknown = item(movement_kind="credit"), item(movement_kind="unknown")
    state = preview(progress={"issues": [{"item_id": str(unknown.id)}]})
    assert ui.row_bucket(credit, state) == "credit"
    assert ui.row_bucket(unknown, state) == "pending"
    assert ui.row_bucket(item(accounting_status="recognized"), state) == "done"
    assert ui.row_bucket(item(recognized_poliza_ids=[uuid4()]), state) == "done"


def test_view_exposes_only_controlled_escaped_metadata_and_no_effects():
    root = html.fromstring(
        ui.workspace_view(
            '"><img src=x>',
            "<p>Source remains server controlled</p>",
            kind="statement",
            statement_url='/state?x="<bad>',
            source_version=7,
            pending_budget=3,
        )
    )
    assert root.get("data-amex-root") == "true"
    assert root.get("data-source-version") == "7"
    assert root.get("data-pending-budget") == "3"
    assert not root.xpath(".//img")
    assert root.get("data-amex-title") == '"><img src=x>'
    assert (
        html.fromstring(ui.workspace_view("Detail", "", kind="detail")).get(
            "data-amex-root"
        )
        == "false"
    )
    assert (
        html.fromstring(ui.workspace_view("Budget", "", kind="budget")).get(
            "data-amex-root"
        )
        == "false"
    )


def test_sources_retain_their_own_permission_fieldset_when_moved():
    source = html.fromstring(
        ui.dialog_source("project", '<form><input name="reason"></form>', disabled=True)
    )
    assert source.xpath("./fieldset[@disabled]/form/input[@name='reason']")
    assert source.get("data-amex-dialog-source") == "project"
    assert not html.fromstring(ui.dialog_source("journal", "<p>Read</p>")).xpath(
        "./fieldset"
    )


def test_finance_read_authority_and_frozen_controls_stay_separate():
    root = html.fromstring(
        ui.workspace_toolbar(
            classification=False, treatment=False, finance=True, frozen=False
        )
    )
    keys = root.xpath(".//button/@data-amex-open")
    assert keys == ["no-cfdi", "review"]
    assert (
        ui.workspace_toolbar(
            classification=True, treatment=True, finance=True, frozen=True
        )
        == ""
    )
    all_controls = html.fromstring(
        ui.workspace_toolbar(
            classification=True, treatment=True, finance=True, frozen=False
        )
    )
    assert (
        len(all_controls.xpath(".//button[@data-amex-selection-required][@disabled]"))
        == 4
    )


def test_steps_filters_and_assets_keep_the_existing_same_origin_surface():
    for values, current in (
        ({"intake": True}, "1"),
        ({}, "2"),
        ({"finalized": True}, "3"),
    ):
        root = html.fromstring(ui.workspace_steps(**values))
        assert root.xpath(".//li[@aria-current='step']/span/text()") == [current]
    filters = html.fromstring(ui.workspace_filters())
    assert filters.xpath(".//select[@data-amex-filter]/option/@value") == [
        "all",
        "pending",
        "budget",
        "review",
        "ready",
        "done",
        "credit",
    ]
    assets = ui.workspace_assets()
    assert "/static/amex_workspace.js?v=" in assets
    assert "/static/amex_workspace.css?v=" in assets
    assert "http:" not in assets and "https:" not in assets
