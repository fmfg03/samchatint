"""Read-only AR projection over income budgets and PSP CFDI income links."""

from __future__ import annotations

from typing import Any, Optional


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = _safe_str(value)
    return text or None


async def list_budget_lines(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    from samchat.budgets.service import list_budget_lines as _list_budget_lines

    return await _list_budget_lines(*args, **kwargs)


async def list_monthly_plan_for_lines(
    *args: Any, **kwargs: Any
) -> dict[str, dict[int, dict[str, float]]]:
    from samchat.budgets.service import (
        list_monthly_plan_for_lines as _list_monthly_plan_for_lines,
    )

    return await _list_monthly_plan_for_lines(*args, **kwargs)


async def list_budget_cfdi_income_links(
    *args: Any, **kwargs: Any
) -> list[dict[str, Any]]:
    from devnous.gastos.services.cfdi_income_bridge_service import (
        list_budget_cfdi_income_links as _list_budget_cfdi_income_links,
    )

    return await _list_budget_cfdi_income_links(*args, **kwargs)


async def list_psp_cfdi_income_candidates(
    *args: Any, **kwargs: Any
) -> list[dict[str, Any]]:
    from devnous.gastos.services.cfdi_income_bridge_service import (
        list_psp_cfdi_income_candidates as _list_psp_cfdi_income_candidates,
    )

    return await _list_psp_cfdi_income_candidates(*args, **kwargs)


async def list_ar_collection_matches(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    from samchat.ar.collection_matches import (
        list_ar_collection_matches as _list_ar_collection_matches,
    )

    return await _list_ar_collection_matches(*args, **kwargs)


def _month_rows(monthly: dict[int, dict[str, float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for month, values in sorted((monthly or {}).items()):
        rows.append(
            {
                "month": int(month),
                "expected_income_amount": _safe_float(
                    values.get("expected_income_amount")
                ),
            }
        )
    return rows


def _matching_gap(
    *,
    item_id: str,
    source: str,
    reason: str,
    severity: str = "medium",
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "source": source,
        "reason": reason,
        "severity": severity,
    }


def _collection_gap(
    *,
    item_id: str,
    source: str,
    amount: float,
    payer_name: Optional[str],
    payer_rfc: Optional[str],
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "source": source,
        "collection_status": "collection_unknown",
        "amount": _safe_float(amount),
        "payer_name": payer_name,
        "payer_rfc": payer_rfc,
        "outstanding_amount": None,
        "outstanding_amount_status": "unknown",
    }


def _apply_collection_match(
    item: dict[str, Any],
    match: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if not match:
        return item
    accepted_amount = _safe_float(match.get("accepted_amount"))
    item.update(
        {
            "collection_status": "matched_collected",
            "collection_match_id": _safe_str(match.get("id")) or None,
            "collected_amount": accepted_amount,
            "collection_date": _iso(match.get("collection_date"))
            or _iso(match.get("accepted_at")),
            "outstanding_amount": 0.0,
            "outstanding_amount_status": "known",
        }
    )
    return item


async def build_ar_read_model(
    session: Any,
    *,
    budget_version_id: str,
    tournament_id: Optional[str] = None,
    tournament_code: Optional[str] = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Build a read-only AR S1 projection without asserting collection state."""

    clean_version_id = _safe_str(budget_version_id)
    clean_tournament_id = _safe_str(tournament_id) or None
    clean_tournament_code = _safe_str(tournament_code) or None
    row_limit = max(1, min(int(limit or 500), 5000))

    income_lines = await list_budget_lines(
        session,
        version_id=clean_version_id,
        tournament_id=clean_tournament_id,
        tournament_code=clean_tournament_code,
        line_direction="income",
        limit=row_limit,
    )
    line_ids = [_safe_str(line.get("id")) for line in income_lines if line.get("id")]
    monthly_plan = await list_monthly_plan_for_lines(session, line_ids=line_ids)
    links = await list_budget_cfdi_income_links(
        session,
        budget_version_id=clean_version_id,
        tournament_id=clean_tournament_id,
    )
    candidates = await list_psp_cfdi_income_candidates(
        session,
        budget_version_id=clean_version_id,
        limit=min(row_limit, 500),
    )
    collection_matches = await list_ar_collection_matches(
        session,
        budget_version_id=clean_version_id,
        include_reversed=False,
    )
    matches_by_item = {
        _safe_str(match.get("ar_item_id")): match for match in collection_matches
    }

    linked_by_line: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        linked_by_line.setdefault(_safe_str(link.get("budget_line_id")), []).append(
            link
        )

    expected_income: list[dict[str, Any]] = []
    issued_linked: list[dict[str, Any]] = []
    issued_unlinked: list[dict[str, Any]] = []
    collection_gaps: list[dict[str, Any]] = []
    matching_gaps: list[dict[str, Any]] = []

    for line in income_lines:
        line_id = _safe_str(line.get("id"))
        line_monthly = monthly_plan.get(line_id, {})
        monthly_rows = _month_rows(line_monthly)
        expected_total = (
            sum(row["expected_income_amount"] for row in monthly_rows)
            if monthly_rows
            else _safe_float(line.get("budget_amount"))
        )
        line_links = linked_by_line.get(line_id, [])
        linked_total = sum(_safe_float(link.get("amount")) for link in line_links)
        expected_income.append(
            {
                "ar_item_id": f"expected:{line_id}",
                "budget_version_id": _safe_str(line.get("budget_version_id"))
                or clean_version_id,
                "budget_line_id": line_id,
                "budget_concept_id": _safe_str(line.get("budget_concept_id")) or None,
                "tournament_id": _safe_str(line.get("tournament_id")) or None,
                "tournament_code": _safe_str(line.get("tournament_code")) or None,
                "tournament_name": _safe_str(line.get("tournament_name")) or None,
                "phase": _safe_str(line.get("phase")) or None,
                "concept_name": _safe_str(line.get("concept_name")) or None,
                "expected_income_amount": _safe_float(expected_total),
                "monthly_plan": monthly_rows,
                "linked_income_amount": _safe_float(linked_total),
                "collection_status": "collection_unknown",
                "outstanding_amount": None,
                "outstanding_amount_status": "unknown",
                "status": "planned" if not line_links else "issued_linked",
            }
        )
        if not line_id:
            matching_gaps.append(
                _matching_gap(
                    item_id="expected:missing-line-id",
                    source="expected_income",
                    reason="missing_budget_line_id",
                    severity="high",
                )
            )

    known_line_ids = {line_id for line_id in line_ids if line_id}
    for link in links:
        link_id = _safe_str(link.get("id")) or _safe_str(link.get("cfdi_report_id"))
        item_id = f"linked:{link_id}"
        payer_rfc = _safe_str(link.get("receptor_rfc")) or None
        payer_name = _safe_str(link.get("receptor_nombre")) or None
        amount = _safe_float(link.get("amount"))
        linked_item = _apply_collection_match(
            {
                "ar_item_id": item_id,
                "status": "recognized",
                "budget_version_id": _safe_str(link.get("budget_version_id"))
                or clean_version_id,
                "budget_line_id": _safe_str(link.get("budget_line_id")) or None,
                "tournament_id": _safe_str(link.get("tournament_id")) or None,
                "phase": _safe_str(link.get("phase")) or None,
                "budget_concept_id": _safe_str(link.get("budget_concept_id")) or None,
                "concept_name": _safe_str(link.get("concept_name")) or None,
                "cfdi_report_id": _safe_str(link.get("cfdi_report_id")) or None,
                "cfdi_uuid": _safe_str(link.get("cfdi_uuid")) or None,
                "payer_rfc": payer_rfc,
                "payer_name": payer_name,
                "issued_amount": amount,
                "linked_income_amount": amount,
                "recognized_income_date": _iso(link.get("income_date")),
                "collection_status": "collection_unknown",
                "outstanding_amount": None,
                "outstanding_amount_status": "unknown",
            },
            matches_by_item.get(item_id),
        )
        issued_linked.append(linked_item)
        if linked_item.get("collection_status") != "matched_collected":
            collection_gaps.append(
                _collection_gap(
                    item_id=item_id,
                    source="issued_linked",
                    amount=amount,
                    payer_name=payer_name,
                    payer_rfc=payer_rfc,
                )
            )
        if not payer_rfc and not payer_name:
            matching_gaps.append(
                _matching_gap(
                    item_id=item_id,
                    source="issued_linked",
                    reason="payer_gap",
                )
            )
        if _safe_str(link.get("budget_line_id")) not in known_line_ids:
            matching_gaps.append(
                _matching_gap(
                    item_id=item_id,
                    source="issued_linked",
                    reason="budget_line_not_in_scope",
                )
            )

    for candidate in candidates:
        candidate_id = _safe_str(candidate.get("id"))
        item_id = f"candidate:{candidate_id}"
        payer_rfc = _safe_str(candidate.get("receptor_rfc")) or None
        payer_name = _safe_str(candidate.get("receptor_nombre")) or None
        amount = _safe_float(candidate.get("total"))
        unlinked_item = _apply_collection_match(
            {
                "ar_item_id": item_id,
                "status": "issued_unlinked",
                "cfdi_report_id": candidate_id or None,
                "cfdi_uuid": _safe_str(candidate.get("cfdi_uuid")) or None,
                "issued_amount": amount,
                "issued_date": _iso(candidate.get("fecha")),
                "emisor_rfc": _safe_str(candidate.get("emisor_rfc")) or None,
                "emisor_nombre": _safe_str(candidate.get("emisor_nombre")) or None,
                "payer_rfc": payer_rfc,
                "payer_name": payer_name,
                "collection_status": "collection_unknown",
                "outstanding_amount": None,
                "outstanding_amount_status": "unknown",
            },
            matches_by_item.get(item_id),
        )
        issued_unlinked.append(unlinked_item)
        if unlinked_item.get("collection_status") != "matched_collected":
            collection_gaps.append(
                _collection_gap(
                    item_id=item_id,
                    source="issued_unlinked",
                    amount=amount,
                    payer_name=payer_name,
                    payer_rfc=payer_rfc,
                )
            )
        matching_gaps.append(
            _matching_gap(
                item_id=item_id,
                source="issued_unlinked",
                reason="missing_budget_income_link",
            )
        )
        if not payer_rfc and not payer_name:
            matching_gaps.append(
                _matching_gap(
                    item_id=item_id,
                    source="issued_unlinked",
                    reason="payer_gap",
                )
            )

    expected_total = sum(
        _safe_float(item.get("expected_income_amount")) for item in expected_income
    )
    linked_total = sum(
        _safe_float(item.get("linked_income_amount")) for item in issued_linked
    )
    unlinked_total = sum(
        _safe_float(item.get("issued_amount")) for item in issued_unlinked
    )

    return {
        "ok": True,
        "read_only": True,
        "budget_version_id": clean_version_id,
        "tournament_id": clean_tournament_id,
        "tournament_code": clean_tournament_code,
        "collection_source": "unknown",
        "outstanding_amount_status": "unknown",
        "summary": {
            "expected_income_count": len(expected_income),
            "expected_income_total": _safe_float(expected_total),
            "issued_linked_count": len(issued_linked),
            "linked_income_total": _safe_float(linked_total),
            "issued_unlinked_count": len(issued_unlinked),
            "issued_unlinked_total": _safe_float(unlinked_total),
            "collection_gap_count": len(collection_gaps),
            "matching_gap_count": len(matching_gaps),
        },
        "expected_income": expected_income,
        "issued_linked": issued_linked,
        "issued_unlinked": issued_unlinked,
        "collection_gaps": collection_gaps,
        "matching_gaps": matching_gaps,
    }
