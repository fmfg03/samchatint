from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from samchat.ar.service import (
    build_ar_accounting_preview,
    build_ar_actionable_gaps,
    build_ar_billing_schedule,
    build_ar_operational_rows,
    build_ar_read_model,
    find_ar_operational_item,
)


@pytest.fixture(autouse=True)
def no_active_collection_matches():
    with patch(
        "samchat.ar.service.list_ar_collection_matches",
        new=AsyncMock(return_value=[]),
    ):
        yield


@pytest.mark.asyncio
async def test_ar_read_model_groups_expected_income_lines():
    session = object()
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "line-1",
                        "budget_version_id": "version-1",
                        "budget_concept_id": "concept-1",
                        "tournament_id": "tournament-1",
                        "tournament_code": "COPA",
                        "tournament_name": "Copa",
                        "phase": "Nacional",
                        "concept_name": "Patrocinio",
                        "budget_amount": 1200,
                    }
                ]
            ),
        ) as list_lines,
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(
                return_value={
                    "line-1": {
                        1: {"expected_income_amount": 500},
                        2: {"expected_income_amount": 700},
                    }
                }
            ),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_ar_read_model(
            session,
            budget_version_id="version-1",
            tournament_id="tournament-1",
        )

    list_lines.assert_awaited_once_with(
        session,
        version_id="version-1",
        tournament_id="tournament-1",
        tournament_code=None,
        line_direction="income",
        limit=500,
        ensure_schema=True,
    )
    assert result["read_only"] is True
    assert result["expected_income"][0]["expected_income_amount"] == 1200.0
    assert result["expected_income"][0]["monthly_plan"] == [
        {"month": 1, "expected_income_amount": 500.0},
        {"month": 2, "expected_income_amount": 700.0},
    ]
    assert result["summary"]["expected_income_total"] == 1200.0


@pytest.mark.asyncio
async def test_ar_read_model_marks_linked_cfdi_as_collection_unknown():
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[{"id": "line-1", "budget_amount": 2000}]),
        ),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "link-1",
                        "cfdi_report_id": "cfdi-1",
                        "budget_line_id": "line-1",
                        "budget_version_id": "version-1",
                        "amount": 1500,
                        "income_date": datetime(2026, 1, 15),
                        "cfdi_uuid": "uuid-1",
                        "receptor_rfc": "CLI010101AAA",
                        "receptor_nombre": "Cliente SA",
                        "concept_name": "Patrocinio",
                    }
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    linked = result["issued_linked"][0]
    assert linked["status"] == "recognized"
    assert linked["collection_status"] == "collection_unknown"
    assert linked["outstanding_amount"] is None
    assert linked["outstanding_amount_status"] == "unknown"
    assert result["collection_gaps"][0]["item_id"] == "linked:link-1"
    assert result["summary"]["linked_income_total"] == 1500.0


@pytest.mark.asyncio
async def test_ar_read_model_includes_unlinked_psp_candidates():
    with (
        patch("samchat.ar.service.list_budget_lines", new=AsyncMock(return_value=[])),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "cfdi-2",
                        "cfdi_uuid": "uuid-2",
                        "fecha": datetime(2026, 2, 1),
                        "total": 900,
                        "emisor_rfc": "PSP010101AAA",
                        "emisor_nombre": "PSP",
                        "receptor_rfc": "CLI020202BBB",
                        "receptor_nombre": "Cliente Dos",
                    }
                ]
            ),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    candidate = result["issued_unlinked"][0]
    assert candidate["status"] == "issued_unlinked"
    assert candidate["collection_status"] == "collection_unknown"
    assert candidate["outstanding_amount_status"] == "unknown"
    assert any(
        gap["reason"] == "missing_budget_income_link"
        for gap in result["matching_gaps"]
    )


@pytest.mark.asyncio
async def test_ar_read_model_reports_matching_gaps_for_missing_payer_or_line():
    with (
        patch("samchat.ar.service.list_budget_lines", new=AsyncMock(return_value=[])),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "link-2",
                        "budget_line_id": "missing-line",
                        "amount": 100,
                        "receptor_rfc": "",
                        "receptor_nombre": "",
                    }
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    reasons = {gap["reason"] for gap in result["matching_gaps"]}
    assert "payer_gap" in reasons
    assert "budget_line_not_in_scope" in reasons


@pytest.mark.asyncio
async def test_ar_read_model_never_sets_outstanding_without_collection_source():
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[{"id": "line-1", "budget_amount": 100}]),
        ),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {"id": "link-1", "budget_line_id": "line-1", "amount": 100}
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[{"id": "cfdi-1", "total": 50}]),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    assert result["collection_source"] == "unknown"
    assert result["outstanding_amount_status"] == "unknown"
    all_items = (
        result["expected_income"]
        + result["issued_linked"]
        + result["issued_unlinked"]
        + result["collection_gaps"]
    )
    assert all(item["outstanding_amount"] is None for item in all_items)
    assert all(
        item["outstanding_amount_status"] == "unknown" for item in all_items
    )


@pytest.mark.asyncio
async def test_ar_read_model_is_read_only_shape():
    with (
        patch("samchat.ar.service.list_budget_lines", new=AsyncMock(return_value=[])),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    assert result == {
        "ok": True,
        "read_only": True,
        "budget_version_id": "version-1",
        "tournament_id": None,
        "tournament_code": None,
        "collection_source": "unknown",
        "credit_days_default": 0,
        "outstanding_amount_status": "unknown",
        "summary": {
            "expected_income_count": 0,
            "expected_income_total": 0.0,
            "issued_linked_count": 0,
            "linked_income_total": 0.0,
            "issued_unlinked_count": 0,
            "issued_unlinked_total": 0.0,
            "invoiced_total": 0.0,
            "collected_total": 0.0,
            "balance_total": 0.0,
            "overdue_total": 0.0,
            "collection_gap_count": 0,
            "matching_gap_count": 0,
        },
        "expected_income": [],
        "issued_linked": [],
        "issued_unlinked": [],
        "collection_gaps": [],
        "matching_gaps": [],
    }


@pytest.mark.asyncio
async def test_ar_read_model_marks_active_match_as_collected():
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[{"id": "line-1", "budget_amount": 100}]),
        ),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "link-1",
                        "budget_line_id": "line-1",
                        "budget_version_id": "version-1",
                        "amount": 100,
                        "receptor_rfc": "CLI010101AAA",
                        "receptor_nombre": "Cliente",
                    }
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_ar_collection_matches",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "match-1",
                        "ar_item_id": "linked:link-1",
                        "accepted_amount": 100,
                        "collection_date": "2026-01-16T00:00:00",
                    }
                ]
            ),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    linked = result["issued_linked"][0]
    assert linked["collection_status"] == "matched_collected"
    assert linked["collected_amount"] == 100.0
    assert linked["outstanding_amount"] == 0.0
    assert linked["outstanding_amount_status"] == "known"
    assert result["collection_gaps"] == []


@pytest.mark.asyncio
async def test_ar_read_model_marks_partial_collection_and_balance():
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[{"id": "line-1", "budget_amount": 100}]),
        ),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "link-1",
                        "budget_line_id": "line-1",
                        "budget_version_id": "version-1",
                        "amount": 100,
                        "cfdi_fecha": datetime(2026, 1, 15),
                    }
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_ar_collection_matches",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "match-1",
                        "ar_item_id": "linked:link-1",
                        "accepted_amount": 40,
                        "collection_date": "2026-01-16T00:00:00",
                    }
                ]
            ),
        ),
    ):
        result = await build_ar_read_model(
            object(),
            budget_version_id="version-1",
            as_of_date=datetime(2026, 1, 15).date(),
        )

    linked = result["issued_linked"][0]
    assert linked["collection_status"] == "partially_collected"
    assert linked["collected_amount"] == 40.0
    assert linked["balance_amount"] == 60.0
    assert linked["operational_status"] == "Parcialmente cobrado"
    assert result["summary"]["collected_total"] == 40.0
    assert result["summary"]["balance_total"] == 60.0


@pytest.mark.asyncio
async def test_ar_read_model_marks_overpayment_for_manual_review():
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[{"id": "line-1", "budget_amount": 100}]),
        ),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "link-1",
                        "budget_line_id": "line-1",
                        "budget_version_id": "version-1",
                        "amount": 100,
                    }
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_ar_collection_matches",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "match-1",
                        "ar_item_id": "linked:link-1",
                        "accepted_amount": 120,
                    }
                ]
            ),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    linked = result["issued_linked"][0]
    assert linked["collection_status"] == "over_collected_review"
    assert linked["operational_status"] == "Revisión sobrepago"
    assert result["collection_gaps"] == []


def test_build_ar_operational_rows_filters_and_sorts():
    payload = {
        "expected_income": [
            {
                "ar_item_id": "expected:1",
                "status": "planned",
                "concept_name": "Patrocinio B",
                "expected_income_amount": 100,
            }
        ],
        "issued_linked": [
            {
                "ar_item_id": "linked:1",
                "concept_name": "Patrocinio A",
                "payer_name": "Cliente Z",
                "issued_amount": 200,
                "collected_amount": 50,
                "operational_status": "Parcialmente cobrado",
            }
        ],
        "issued_unlinked": [],
    }

    rows = build_ar_operational_rows(
        payload,
        search="patrocinio",
        sort_by="concept_name",
        sort_dir="asc",
    )

    assert [row["concept_name"] for row in rows] == ["Patrocinio A", "Patrocinio B"]
    filtered = build_ar_operational_rows(
        payload,
        status_filter="Parcialmente cobrado",
    )
    assert [row["ar_item_id"] for row in filtered] == ["linked:1"]


def test_build_ar_operational_rows_keeps_missing_dates_last_on_desc_sort():
    payload = {
        "expected_income": [
            {"ar_item_id": "expected:1", "status": "planned", "budget_amount": 100}
        ],
        "issued_linked": [
            {
                "ar_item_id": "linked:old",
                "issued_date": "2026-01-01T00:00:00",
                "issued_amount": 100,
            },
            {
                "ar_item_id": "linked:new",
                "issued_date": "2026-02-01T00:00:00",
                "issued_amount": 100,
            },
        ],
        "issued_unlinked": [],
    }

    rows = build_ar_operational_rows(payload)

    assert [row["ar_item_id"] for row in rows] == [
        "linked:new",
        "linked:old",
        "expected:1",
    ]


def test_build_ar_billing_schedule_includes_planned_rows_with_remaining():
    payload = {
        "expected_income": [
            {
                "ar_item_id": "expected:line-1",
                "status": "planned",
                "tournament_name": "Copa",
                "phase": "Nacional",
                "concept_name": "Patrocinio",
                "expected_income_amount": 1200,
                "linked_income_amount": 500,
                "monthly_plan": [
                    {"month": 8, "expected_income_amount": 1200},
                ],
            }
        ]
    }

    rows = build_ar_billing_schedule(
        payload,
        as_of_date=datetime(2026, 9, 2).date(),
    )

    assert rows == [
        {
            "priority": "alta",
            "ar_item_id": "expected:line-1",
            "budget_line_id": None,
            "tournament_name": "Copa",
            "phase": "Nacional",
            "concept_name": "Patrocinio",
            "budgeted_amount": 1200.0,
            "linked_amount": 500.0,
            "remaining_to_invoice": 700.0,
            "months": [
                {
                    "month": 8,
                    "label": "ago",
                    "expected_income_amount": 1200.0,
                }
            ],
            "months_label": "ago: $1,200.00",
        }
    ]


def test_build_ar_billing_schedule_priority_media_for_current_month():
    rows = build_ar_billing_schedule(
        {
            "expected_income": [
                {
                    "ar_item_id": "expected:current",
                    "status": "planned",
                    "expected_income_amount": 100,
                    "monthly_plan": [
                        {"month": 9, "expected_income_amount": 100},
                    ],
                }
            ]
        },
        as_of_date=datetime(2026, 9, 2).date(),
    )

    assert rows[0]["priority"] == "media"


def test_build_ar_billing_schedule_priority_baja_for_future_or_unclear_months():
    rows = build_ar_billing_schedule(
        {
            "expected_income": [
                {
                    "ar_item_id": "expected:future",
                    "status": "planned",
                    "expected_income_amount": 100,
                    "monthly_plan": [
                        {"month": 10, "expected_income_amount": 100},
                    ],
                },
                {
                    "ar_item_id": "expected:unclear",
                    "status": "planned",
                    "expected_income_amount": 80,
                    "monthly_plan": [],
                },
            ]
        },
        as_of_date=datetime(2026, 9, 2).date(),
    )

    assert [row["priority"] for row in rows] == ["baja", "baja"]
    assert rows[1]["months_label"] == "sin mes claro"


def test_build_ar_billing_schedule_excludes_completed_or_non_planned_rows():
    rows = build_ar_billing_schedule(
        {
            "expected_income": [
                {
                    "ar_item_id": "expected:complete",
                    "status": "planned",
                    "expected_income_amount": 100,
                    "linked_income_amount": 100,
                },
                {
                    "ar_item_id": "expected:linked",
                    "status": "issued_linked",
                    "operational_status": "CFDI vinculado",
                    "expected_income_amount": 100,
                },
            ]
        },
        as_of_date=datetime(2026, 9, 2).date(),
    )

    assert rows == []


def test_build_ar_billing_schedule_sorts_priority_then_remaining_desc():
    payload = {
        "expected_income": [
            {
                "ar_item_id": "expected:media",
                "status": "planned",
                "expected_income_amount": 900,
                "monthly_plan": [{"month": 9, "expected_income_amount": 900}],
            },
            {
                "ar_item_id": "expected:alta-small",
                "status": "planned",
                "expected_income_amount": 100,
                "monthly_plan": [{"month": 8, "expected_income_amount": 100}],
            },
            {
                "ar_item_id": "expected:alta-large",
                "status": "planned",
                "expected_income_amount": 500,
                "monthly_plan": [{"month": 8, "expected_income_amount": 500}],
            },
        ]
    }

    rows = build_ar_billing_schedule(
        payload,
        as_of_date=datetime(2026, 9, 2).date(),
    )

    assert [row["ar_item_id"] for row in rows] == [
        "expected:alta-large",
        "expected:alta-small",
        "expected:media",
    ]


def test_build_ar_billing_schedule_respects_status_and_search_filters():
    payload = {
        "expected_income": [
            {
                "ar_item_id": "expected:copa",
                "status": "planned",
                "tournament_name": "Copa Nacional",
                "expected_income_amount": 100,
            }
        ]
    }

    assert (
        build_ar_billing_schedule(
            payload,
            status_filter="Vencido",
            search="copa",
        )
        == []
    )
    rows = build_ar_billing_schedule(
        payload,
        status_filter="Presupuestado sin CFDI",
        search="nacional",
    )

    assert [row["ar_item_id"] for row in rows] == ["expected:copa"]


def test_build_ar_actionable_gaps_prioritizes_high_medium_low():
    payload = {
        "expected_income": [
            {
                "ar_item_id": "expected:1",
                "status": "planned",
                "tournament_name": "Copa",
                "concept_name": "Inscripciones",
                "expected_income_amount": 100,
                "operational_status": "Presupuestado sin CFDI",
            }
        ],
        "issued_linked": [
            {
                "ar_item_id": "linked:overpaid",
                "payer_name": "Cliente A",
                "payer_rfc": "AAA010101AAA",
                "issued_amount": 100,
                "collected_amount": 120,
                "operational_status": "Revisión sobrepago",
            }
        ],
        "issued_unlinked": [
            {
                "ar_item_id": "candidate:1",
                "payer_name": "Cliente B",
                "payer_rfc": "BBB010101BBB",
                "issued_amount": 80,
                "operational_status": "CFDI emitido sin vincular",
            }
        ],
        "matching_gaps": [],
    }

    gaps = build_ar_actionable_gaps(payload)

    assert [gap["priority"] for gap in gaps] == ["alta", "media", "baja"]
    assert [gap["gap_type"] for gap in gaps] == [
        "sobrepago",
        "cfdi_sin_partida",
        "presupuesto_sin_cfdi",
    ]


def test_build_ar_actionable_gaps_deduplicates_and_keeps_highest_priority():
    payload = {
        "expected_income": [],
        "issued_linked": [],
        "issued_unlinked": [
            {
                "ar_item_id": "candidate:1",
                "payer_name": "Cliente",
                "payer_rfc": "CLI010101AAA",
                "issued_amount": 100,
                "operational_status": "CFDI emitido sin vincular",
            }
        ],
        "matching_gaps": [
            {
                "item_id": "candidate:1",
                "reason": "missing_budget_income_link",
            }
        ],
    }

    gaps = build_ar_actionable_gaps(payload)

    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "cfdi_sin_partida"
    assert gaps[0]["priority"] == "alta"


def test_build_ar_actionable_gaps_marks_expected_income_without_cfdi_low_only():
    payload = {
        "expected_income": [
            {
                "ar_item_id": "expected:1",
                "status": "planned",
                "expected_income_amount": 100,
                "operational_status": "Presupuestado sin CFDI",
            }
        ],
        "issued_linked": [],
        "issued_unlinked": [],
        "matching_gaps": [],
    }

    gaps = build_ar_actionable_gaps(payload)

    assert [(gap["priority"], gap["gap_type"]) for gap in gaps] == [
        ("baja", "presupuesto_sin_cfdi")
    ]


def test_build_ar_actionable_gaps_marks_missing_client_or_rfc_as_medium():
    payload = {
        "expected_income": [],
        "issued_linked": [
            {
                "ar_item_id": "linked:1",
                "issued_amount": 100,
                "operational_status": "Cobrado",
            }
        ],
        "issued_unlinked": [],
        "matching_gaps": [],
    }

    gaps = build_ar_actionable_gaps(payload)

    assert gaps[0]["priority"] == "media"
    assert gaps[0]["gap_type"] == "cliente_rfc_faltante"


def test_build_ar_accounting_preview_for_linked_invoice_is_ready():
    item = {
        "source": "issued_linked",
        "ar_item_id": "linked:1",
        "issued_amount": 116,
        "iva_amount": 16,
        "account_code_final": "4100-001-004",
    }

    preview = build_ar_accounting_preview(item)

    invoice = preview["invoice_policy_preview"]
    assert invoice["status"] == "lista"
    assert invoice["gaps"] == []
    assert [
        (line["side"], line["account_code"], line["amount"])
        for line in invoice["lines"]
    ] == [
        ("debe", "1150-001-001", 116.0),
        ("haber", "4100-001-004", 100.0),
        ("haber", "2140-001-001", 16.0),
    ]


def test_build_ar_accounting_preview_marks_missing_income_account():
    preview = build_ar_accounting_preview(
        {
            "source": "issued_linked",
            "ar_item_id": "linked:1",
            "issued_amount": 100,
            "iva_amount": 0,
        }
    )

    invoice = preview["invoice_policy_preview"]
    assert invoice["status"] == "incompleta"
    assert "missing_income_account" in invoice["gaps"]
    assert invoice["lines"] == []


def test_build_ar_accounting_preview_marks_collection_without_match():
    preview = build_ar_accounting_preview(
        {
            "source": "issued_linked",
            "ar_item_id": "linked:1",
            "issued_amount": 100,
            "account_code_final": "4100-001-004",
        }
    )

    collection = preview["collection_policy_preview"]
    assert collection["status"] == "sin match"
    assert collection["gaps"] == ["missing_collection_match"]
    assert collection["lines"] == []


def test_build_ar_accounting_preview_for_accepted_collection_is_ready():
    preview = build_ar_accounting_preview(
        {
            "source": "issued_linked",
            "ar_item_id": "linked:1",
            "issued_amount": 100,
            "collected_amount": 100,
            "collection_match_id": "match-1",
            "account_code_final": "4100-001-004",
        }
    )

    collection = preview["collection_policy_preview"]
    assert collection["status"] == "lista"
    assert [
        (line["side"], line["account_code"], line["amount"])
        for line in collection["lines"]
    ] == [
        ("debe", "1120-001-001", 100.0),
        ("haber", "1150-001-001", 100.0),
    ]


def test_find_ar_operational_item_finds_item_across_sections():
    payload = {
        "expected_income": [{"ar_item_id": "expected:1"}],
        "issued_linked": [{"ar_item_id": "linked:1"}],
        "issued_unlinked": [{"ar_item_id": "candidate:1"}],
    }

    item = find_ar_operational_item(payload, "linked:1")

    assert item == {"ar_item_id": "linked:1", "source": "issued_linked"}
    assert find_ar_operational_item(payload, "missing") is None


@pytest.mark.asyncio
async def test_ar_read_model_allows_collected_status_for_unlinked_cfdi_with_match():
    with (
        patch("samchat.ar.service.list_budget_lines", new=AsyncMock(return_value=[])),
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "cfdi-1",
                        "cfdi_uuid": "uuid-1",
                        "fecha": datetime(2026, 1, 15),
                        "total": 100,
                    }
                ]
            ),
        ),
        patch(
            "samchat.ar.service.list_ar_collection_matches",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "match-1",
                        "ar_item_id": "candidate:cfdi-1",
                        "accepted_amount": 100,
                    }
                ]
            ),
        ),
    ):
        result = await build_ar_read_model(object(), budget_version_id="version-1")

    candidate = result["issued_unlinked"][0]
    assert candidate["status"] == "issued_unlinked"
    assert candidate["collection_status"] == "matched_collected"
    assert candidate["operational_status"] == "Cobrado"


@pytest.mark.asyncio
async def test_ar_read_model_can_skip_schema_ensure_for_render_paths():
    session = object()
    with (
        patch(
            "samchat.ar.service.list_budget_lines",
            new=AsyncMock(return_value=[]),
        ) as list_lines,
        patch(
            "samchat.ar.service.list_monthly_plan_for_lines",
            new=AsyncMock(return_value={}),
        ) as list_monthly,
        patch(
            "samchat.ar.service.list_budget_cfdi_income_links",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "samchat.ar.service.list_psp_cfdi_income_candidates",
            new=AsyncMock(return_value=[]),
        ) as list_candidates,
        patch(
            "samchat.ar.service.list_ar_collection_matches",
            new=AsyncMock(return_value=[]),
        ) as list_matches,
    ):
        await build_ar_read_model(
            session,
            budget_version_id="version-1",
            ensure_schema=False,
        )

    assert list_lines.await_args.kwargs["ensure_schema"] is False
    assert list_monthly.await_args.kwargs["ensure_schema"] is False
    assert list_candidates.await_args.kwargs["ensure_schema"] is False
    assert list_matches.await_args.kwargs["ensure_schema"] is False
