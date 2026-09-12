from samchat.client_executive import ui


def test_account_breakdown_does_not_present_unallocated_actuals_as_zero() -> None:
    """Account rows show budget only until canonical account actuals exist."""
    rendered = ui._budget_detail(
        {
            "budget_source_status": "available",
            "budget_breakdowns": {
                "by_concept": [],
                "by_account": [
                    {
                        "label": "5300-012-018 · ALIMENTOS",
                        "budget_total": 100_000,
                        "actual_total": 0,
                        "committed_total": 0,
                    }
                ],
            },
        }
    )

    assert "5300-012-018" in rendered
    assert "$100,000.00" in rendered
    assert rendered.count("<td class='money'>No disponible</td>") == 2
    assert "<td>—</td>" in rendered
    assert "$0.00" not in rendered
    assert "aún no están acreditados por la fuente canónica" in rendered
