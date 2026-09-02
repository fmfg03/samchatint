"""Read-only AR projection over income budgets and PSP CFDI income links."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

AR_DEFAULT_BANK_ACCOUNT_CODE = "1120-001-001"
AR_IVA_TRASLADADO_ACCOUNT_CODE = "2140-001-001"


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


def _date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _safe_str(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _due_date(value: Any, credit_days: int) -> Optional[str]:
    base = _date(value)
    if base is None:
        return None
    return (base + timedelta(days=max(0, min(int(credit_days or 0), 365)))).isoformat()


def _operational_status(
    *,
    status: str,
    collection_status: str,
    amount: float,
    collected_amount: float,
    due_date: Optional[str],
    as_of: date,
) -> str:
    if status == "planned":
        return "Presupuestado sin CFDI"
    if collection_status == "over_collected_review":
        return "Revisión sobrepago"
    if amount > 0 and collected_amount >= amount:
        return "Cobrado"
    if collected_amount > 0:
        if due_date and _date(due_date) and _date(due_date) < as_of:
            return "Vencido"
        return "Parcialmente cobrado"
    if due_date and _date(due_date) and _date(due_date) < as_of:
        return "Vencido"
    if status == "issued_unlinked":
        return "CFDI emitido sin vincular"
    if status in {"recognized", "issued_linked"}:
        return "Cobranza desconocida"
    return "CFDI vinculado"


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


def _receivable_account_code_for_ar_item(item: dict[str, Any]) -> str:
    concept = " ".join(
        _safe_str(item.get(key))
        for key in (
            "tournament_name",
            "concept_name",
            "account_code_final",
            "account_code_suggested",
        )
    ).lower()
    if (
        "patrocin" in concept
        or "intercambio" in concept
        or "4100-001-008" in concept
    ):
        return "1150-001-003"
    return "1150-001-001"


def _month_name(month: int) -> str:
    return {
        1: "ene",
        2: "feb",
        3: "mar",
        4: "abr",
        5: "may",
        6: "jun",
        7: "jul",
        8: "ago",
        9: "sep",
        10: "oct",
        11: "nov",
        12: "dic",
    }.get(month, "")


def _apply_collection_match(
    item: dict[str, Any],
    match: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if not match:
        return item
    accepted_amount = _safe_float(match.get("accepted_amount"))
    item_amount = _safe_float(
        item.get("issued_amount")
        or item.get("linked_income_amount")
        or item.get("amount")
        or item.get("expected_income_amount")
    )
    if accepted_amount > item_amount > 0:
        collection_status = "over_collected_review"
        outstanding_amount = 0.0
    elif accepted_amount >= item_amount > 0:
        collection_status = "matched_collected"
        outstanding_amount = 0.0
    else:
        collection_status = "partially_collected"
        outstanding_amount = max(item_amount - accepted_amount, 0.0)
    item.update(
        {
            "collection_status": collection_status,
            "collection_match_id": _safe_str(match.get("id")) or None,
            "collected_amount": accepted_amount,
            "collection_date": _iso(match.get("collection_date"))
            or _iso(match.get("accepted_at")),
            "outstanding_amount": _safe_float(outstanding_amount),
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
    credit_days_default: int = 0,
    as_of_date: Optional[date] = None,
    ensure_schema: bool = True,
) -> dict[str, Any]:
    """Build a read-only AR S1 projection without asserting collection state."""

    clean_version_id = _safe_str(budget_version_id)
    clean_tournament_id = _safe_str(tournament_id) or None
    clean_tournament_code = _safe_str(tournament_code) or None
    row_limit = max(1, min(int(limit or 500), 5000))
    credit_days = max(0, min(int(credit_days_default or 0), 365))
    as_of = as_of_date or date.today()

    income_lines = await list_budget_lines(
        session,
        version_id=clean_version_id,
        tournament_id=clean_tournament_id,
        tournament_code=clean_tournament_code,
        line_direction="income",
        limit=row_limit,
        ensure_schema=ensure_schema,
    )
    line_ids = [_safe_str(line.get("id")) for line in income_lines if line.get("id")]
    monthly_plan = await list_monthly_plan_for_lines(
        session,
        line_ids=line_ids,
        ensure_schema=ensure_schema,
    )
    links = await list_budget_cfdi_income_links(
        session,
        budget_version_id=clean_version_id,
        tournament_id=clean_tournament_id,
    )
    candidates = await list_psp_cfdi_income_candidates(
        session,
        budget_version_id=clean_version_id,
        limit=min(row_limit, 500),
        ensure_schema=ensure_schema,
    )
    collection_matches = await list_ar_collection_matches(
        session,
        budget_version_id=clean_version_id,
        include_reversed=False,
        ensure_schema=ensure_schema,
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
                "account_code_final": _safe_str(line.get("account_code_final"))
                or None,
                "account_code_suggested": _safe_str(
                    line.get("account_code_suggested")
                )
                or None,
                "expected_income_amount": _safe_float(expected_total),
                "issued_amount": _safe_float(linked_total),
                "collected_amount": 0.0,
                "balance_amount": None,
                "due_date": None,
                "monthly_plan": monthly_rows,
                "linked_income_amount": _safe_float(linked_total),
                "collection_status": "collection_unknown",
                "outstanding_amount": None,
                "outstanding_amount_status": "unknown",
                "status": "planned" if not line_links else "issued_linked",
                "operational_status": (
                    "Presupuestado sin CFDI"
                    if not line_links
                    else "CFDI vinculado"
                ),
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
                "account_code_final": _safe_str(link.get("account_code_final"))
                or None,
                "account_code_suggested": _safe_str(
                    link.get("account_code_suggested")
                )
                or None,
                "cfdi_report_id": _safe_str(link.get("cfdi_report_id")) or None,
                "cfdi_uuid": _safe_str(link.get("cfdi_uuid")) or None,
                "iva_amount": _safe_float(
                    link.get("total_impuestos_trasladados")
                ),
                "payer_rfc": payer_rfc,
                "payer_name": payer_name,
                "issued_amount": amount,
                "linked_income_amount": amount,
                "collected_amount": 0.0,
                "balance_amount": None,
                "issued_date": _iso(link.get("cfdi_fecha") or link.get("income_date")),
                "due_date": _due_date(
                    link.get("cfdi_fecha") or link.get("income_date"),
                    credit_days,
                ),
                "recognized_income_date": _iso(link.get("income_date")),
                "collection_status": "collection_unknown",
                "outstanding_amount": None,
                "outstanding_amount_status": "unknown",
            },
            matches_by_item.get(item_id),
        )
        linked_item["balance_amount"] = (
            _safe_float(
                amount - _safe_float(linked_item.get("collected_amount"))
            )
            if linked_item.get("outstanding_amount_status") == "known"
            else None
        )
        linked_item["operational_status"] = _operational_status(
            status=_safe_str(linked_item.get("status")),
            collection_status=_safe_str(linked_item.get("collection_status")),
            amount=amount,
            collected_amount=_safe_float(linked_item.get("collected_amount")),
            due_date=linked_item.get("due_date"),
            as_of=as_of,
        )
        issued_linked.append(linked_item)
        if linked_item.get("collection_status") not in {
            "matched_collected",
            "over_collected_review",
        }:
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
                "iva_amount": _safe_float(
                    candidate.get("total_impuestos_trasladados")
                ),
                "issued_amount": amount,
                "issued_date": _iso(candidate.get("fecha")),
                "due_date": _due_date(candidate.get("fecha"), credit_days),
                "emisor_rfc": _safe_str(candidate.get("emisor_rfc")) or None,
                "emisor_nombre": _safe_str(candidate.get("emisor_nombre")) or None,
                "payer_rfc": payer_rfc,
                "payer_name": payer_name,
                "collected_amount": 0.0,
                "balance_amount": None,
                "collection_status": "collection_unknown",
                "outstanding_amount": None,
                "outstanding_amount_status": "unknown",
            },
            matches_by_item.get(item_id),
        )
        unlinked_item["balance_amount"] = (
            _safe_float(
                amount - _safe_float(unlinked_item.get("collected_amount"))
            )
            if unlinked_item.get("outstanding_amount_status") == "known"
            else None
        )
        unlinked_item["operational_status"] = _operational_status(
            status=_safe_str(unlinked_item.get("status")),
            collection_status=_safe_str(unlinked_item.get("collection_status")),
            amount=amount,
            collected_amount=_safe_float(unlinked_item.get("collected_amount")),
            due_date=unlinked_item.get("due_date"),
            as_of=as_of,
        )
        issued_unlinked.append(unlinked_item)
        if unlinked_item.get("collection_status") not in {
            "matched_collected",
            "over_collected_review",
        }:
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
    collected_total = sum(
        _safe_float(item.get("collected_amount"))
        for item in issued_linked + issued_unlinked
    )
    balance_total = sum(
        _safe_float(item.get("issued_amount"))
        - _safe_float(item.get("collected_amount"))
        for item in issued_linked + issued_unlinked
    )
    overdue_total = sum(
        max(
            _safe_float(item.get("issued_amount"))
            - _safe_float(item.get("collected_amount")),
            0.0,
        )
        for item in issued_linked + issued_unlinked
        if item.get("operational_status") == "Vencido"
    )

    return {
        "ok": True,
        "read_only": True,
        "budget_version_id": clean_version_id,
        "tournament_id": clean_tournament_id,
        "tournament_code": clean_tournament_code,
        "collection_source": "unknown",
        "credit_days_default": credit_days,
        "outstanding_amount_status": (
            "mixed" if collected_total else "unknown"
        ),
        "summary": {
            "expected_income_count": len(expected_income),
            "expected_income_total": _safe_float(expected_total),
            "issued_linked_count": len(issued_linked),
            "linked_income_total": _safe_float(linked_total),
            "issued_unlinked_count": len(issued_unlinked),
            "issued_unlinked_total": _safe_float(unlinked_total),
            "invoiced_total": _safe_float(linked_total + unlinked_total),
            "collected_total": _safe_float(collected_total),
            "balance_total": _safe_float(max(balance_total, 0.0)),
            "overdue_total": _safe_float(overdue_total),
            "collection_gap_count": len(collection_gaps),
            "matching_gap_count": len(matching_gaps),
        },
        "expected_income": expected_income,
        "issued_linked": issued_linked,
        "issued_unlinked": issued_unlinked,
        "collection_gaps": collection_gaps,
        "matching_gaps": matching_gaps,
    }


def build_ar_operational_rows(
    payload: dict[str, Any],
    *,
    status_filter: str = "todos",
    search: str = "",
    sort_by: str = "issued_date",
    sort_dir: str = "desc",
) -> list[dict[str, Any]]:
    """Flatten AR read model rows for UI/export filtering and sorting."""

    rows: list[dict[str, Any]] = []
    for item in list(payload.get("expected_income") or []):
        if item.get("status") != "planned":
            continue
        rows.append(
            {
                "source": "expected_income",
                "ar_item_id": item.get("ar_item_id"),
                "tournament_name": item.get("tournament_name")
                or item.get("tournament_code"),
                "phase": item.get("phase"),
                "concept_name": item.get("concept_name"),
                "account_code_final": item.get("account_code_final"),
                "account_code_suggested": item.get("account_code_suggested"),
                "iva_amount": _safe_float(item.get("iva_amount")),
                "payer_name": None,
                "payer_rfc": None,
                "cfdi_uuid": None,
                "issued_date": None,
                "due_date": None,
                "expected_income_amount": _safe_float(
                    item.get("expected_income_amount")
                ),
                "issued_amount": 0.0,
                "linked_income_amount": _safe_float(
                    item.get("linked_income_amount")
                ),
                "collected_amount": 0.0,
                "balance_amount": None,
                "operational_status": item.get("operational_status")
                or "Presupuestado sin CFDI",
            }
        )
    for source, items in (
        ("issued_linked", payload.get("issued_linked") or []),
        ("issued_unlinked", payload.get("issued_unlinked") or []),
    ):
        for item in list(items):
            issued = _safe_float(item.get("issued_amount"))
            collected = _safe_float(item.get("collected_amount"))
            balance = max(issued - collected, 0.0)
            rows.append(
                {
                    "source": source,
                    "ar_item_id": item.get("ar_item_id"),
                    "tournament_name": item.get("tournament_name")
                    or item.get("tournament_code"),
                    "phase": item.get("phase"),
                    "concept_name": item.get("concept_name"),
                    "account_code_final": item.get("account_code_final"),
                    "account_code_suggested": item.get("account_code_suggested"),
                    "iva_amount": _safe_float(item.get("iva_amount")),
                    "payer_name": item.get("payer_name"),
                    "payer_rfc": item.get("payer_rfc"),
                    "cfdi_uuid": item.get("cfdi_uuid"),
                    "issued_date": item.get("issued_date"),
                    "due_date": item.get("due_date"),
                    "expected_income_amount": 0.0,
                    "issued_amount": issued,
                    "linked_income_amount": _safe_float(
                        item.get("linked_income_amount")
                    ),
                    "collected_amount": collected,
                    "balance_amount": balance,
                    "operational_status": item.get("operational_status")
                    or "Cobranza desconocida",
                }
            )

    clean_status = _safe_str(status_filter).lower()
    if clean_status and clean_status != "todos":
        rows = [
            row
            for row in rows
            if _safe_str(row.get("operational_status")).lower() == clean_status
        ]
    clean_search = _safe_str(search).lower()
    if clean_search:
        rows = [
            row
            for row in rows
            if clean_search
            in " ".join(_safe_str(value).lower() for value in row.values())
        ]

    allowed_sort = {
        "tournament_name",
        "phase",
        "concept_name",
        "payer_name",
        "payer_rfc",
        "cfdi_uuid",
        "issued_date",
        "due_date",
        "expected_income_amount",
        "issued_amount",
        "linked_income_amount",
        "collected_amount",
        "balance_amount",
        "operational_status",
    }
    key = sort_by if sort_by in allowed_sort else "issued_date"
    reverse = _safe_str(sort_dir).lower() == "desc"

    def _sort_value(row: dict[str, Any]) -> Any:
        value = row.get(key)
        if key.endswith("_amount"):
            return _safe_float(value)
        return _safe_str(value).lower()

    present_rows = [row for row in rows if row.get(key) not in (None, "")]
    missing_rows = [row for row in rows if row.get(key) in (None, "")]
    return sorted(present_rows, key=_sort_value, reverse=reverse) + missing_rows


def build_ar_billing_schedule(
    payload: dict[str, Any],
    *,
    as_of_date: Optional[date] = None,
    status_filter: str = "todos",
    search: str = "",
) -> list[dict[str, Any]]:
    """Build read-only expected-income rows that still need invoicing."""

    as_of = as_of_date or date.today()
    clean_status = _safe_str(status_filter).lower()
    if clean_status and clean_status != "todos":
        if clean_status != "presupuestado sin cfdi":
            return []
    clean_search = _safe_str(search).lower()
    rows: list[dict[str, Any]] = []
    priority_rank = {"alta": 0, "media": 1, "baja": 2}
    for item in list(payload.get("expected_income") or []):
        status = _safe_str(item.get("status"))
        operational_status = _safe_str(item.get("operational_status"))
        if (
            status != "planned"
            and operational_status != "Presupuestado sin CFDI"
        ):
            continue

        budgeted_amount = _safe_float(item.get("expected_income_amount"))
        linked_amount = _safe_float(
            item.get("linked_income_amount") or item.get("issued_amount")
        )
        remaining_to_invoice = _safe_float(max(budgeted_amount - linked_amount, 0.0))
        if remaining_to_invoice <= 0:
            continue

        month_entries: list[dict[str, Any]] = []
        for monthly in list(item.get("monthly_plan") or []):
            amount = _safe_float(monthly.get("expected_income_amount"))
            if amount <= 0:
                continue
            try:
                month = int(monthly.get("month") or 0)
            except (TypeError, ValueError):
                continue
            if 1 <= month <= 12:
                month_entries.append(
                    {
                        "month": month,
                        "label": _month_name(month),
                        "expected_income_amount": amount,
                    }
                )

        if any(entry["month"] < as_of.month for entry in month_entries):
            priority = "alta"
        elif any(entry["month"] == as_of.month for entry in month_entries):
            priority = "media"
        else:
            priority = "baja"

        months_label = ", ".join(
            (
                f"{entry['label']}: "
                f"${entry['expected_income_amount']:,.2f}"
            )
            for entry in month_entries
        ) or "sin mes claro"

        rows.append(
            {
                "priority": priority,
                "ar_item_id": item.get("ar_item_id"),
                "budget_line_id": item.get("budget_line_id"),
                "tournament_name": item.get("tournament_name")
                or item.get("tournament_code"),
                "phase": item.get("phase"),
                "concept_name": item.get("concept_name"),
                "budgeted_amount": budgeted_amount,
                "linked_amount": linked_amount,
                "remaining_to_invoice": remaining_to_invoice,
                "months": month_entries,
                "months_label": months_label,
            }
        )

    if clean_search:
        rows = [
            row
            for row in rows
            if clean_search
            in " ".join(_safe_str(value).lower() for value in row.values())
        ]

    return sorted(
        rows,
        key=lambda row: (
            priority_rank.get(str(row.get("priority")), 9),
            -_safe_float(row.get("remaining_to_invoice")),
            _safe_str(row.get("tournament_name")).lower(),
            _safe_str(row.get("concept_name")).lower(),
        ),
    )


def find_ar_operational_item(
    payload: dict[str, Any],
    ar_item_id: str,
) -> Optional[dict[str, Any]]:
    """Find one AR item in the read model by stable operational id."""

    clean_id = _safe_str(ar_item_id)
    if not clean_id:
        return None
    sections = (
        ("expected_income", payload.get("expected_income") or []),
        ("issued_linked", payload.get("issued_linked") or []),
        ("issued_unlinked", payload.get("issued_unlinked") or []),
    )
    for source, items in sections:
        for item in list(items):
            if _safe_str(item.get("ar_item_id")) != clean_id:
                continue
            enriched = dict(item)
            enriched["source"] = source
            return enriched
    return None


def build_ar_accounting_preview(
    item: dict[str, Any],
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build read-only expected CxC accounting entries for one AR item."""

    _ = payload
    source = _safe_str(item.get("source") or item.get("status"))
    issued_amount = _safe_float(
        item.get("issued_amount") or item.get("linked_income_amount")
    )
    collected_amount = _safe_float(item.get("collected_amount"))
    income_account = _safe_str(
        item.get("account_code_final") or item.get("account_code_suggested")
    )
    receivable_account = _safe_str(item.get("receivable_account_code"))
    if not receivable_account and source == "issued_linked":
        receivable_account = _receivable_account_code_for_ar_item(item)
    iva_amount = _safe_float(item.get("iva_amount"))
    income_base = max(issued_amount - iva_amount, 0.0)

    def _line(
        *,
        side: str,
        account: str,
        amount: float,
        label: str,
    ) -> dict[str, Any]:
        return {
            "side": side,
            "account_code": account,
            "amount": _safe_float(amount),
            "label": label,
        }

    invoice_gaps: list[str] = []
    invoice_warnings: list[str] = []
    invoice_lines: list[dict[str, Any]] = []
    if source != "issued_linked":
        invoice_status = "no_aplica"
        invoice_gaps.append("cfdi_not_linked")
    else:
        if issued_amount <= 0:
            invoice_gaps.append("missing_invoice_amount")
        if not receivable_account:
            invoice_gaps.append("missing_receivable_account")
        if not income_account:
            invoice_gaps.append("missing_income_account")
        if "iva_amount" not in item:
            invoice_warnings.append("iva_unknown")
        if not invoice_gaps:
            invoice_lines.append(
                _line(
                    side="debe",
                    account=receivable_account,
                    amount=issued_amount,
                    label="Cuentas por cobrar",
                )
            )
            invoice_lines.append(
                _line(
                    side="haber",
                    account=income_account,
                    amount=income_base,
                    label="Ingreso presupuestal",
                )
            )
            if iva_amount > 0:
                invoice_lines.append(
                    _line(
                        side="haber",
                        account=AR_IVA_TRASLADADO_ACCOUNT_CODE,
                        amount=iva_amount,
                        label="IVA trasladado",
                    )
                )
        invoice_status = "lista" if not invoice_gaps else "incompleta"

    collection_gaps: list[str] = []
    collection_lines: list[dict[str, Any]] = []
    if not item.get("collection_match_id"):
        collection_status = "sin match"
        collection_gaps.append("missing_collection_match")
    else:
        collection_amount = collected_amount or issued_amount
        if collection_amount <= 0:
            collection_gaps.append("missing_collection_amount")
        if not receivable_account:
            collection_gaps.append("missing_receivable_account")
        if not collection_gaps:
            collection_lines.append(
                _line(
                    side="debe",
                    account=AR_DEFAULT_BANK_ACCOUNT_CODE,
                    amount=collection_amount,
                    label="Bancos",
                )
            )
            collection_lines.append(
                _line(
                    side="haber",
                    account=receivable_account,
                    amount=collection_amount,
                    label="Cuentas por cobrar",
                )
            )
        collection_status = "lista" if not collection_gaps else "incompleta"

    return {
        "ar_item_id": item.get("ar_item_id"),
        "cfdi_uuid": item.get("cfdi_uuid"),
        "payer_name": item.get("payer_name"),
        "payer_rfc": item.get("payer_rfc"),
        "invoice_policy_preview": {
            "type": "factura",
            "status": invoice_status,
            "lines": invoice_lines,
            "gaps": invoice_gaps,
            "warnings": invoice_warnings,
        },
        "collection_policy_preview": {
            "type": "cobro",
            "status": collection_status,
            "lines": collection_lines,
            "gaps": collection_gaps,
            "warnings": [],
        },
    }


def build_ar_actionable_gaps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a prioritized CxC action queue from the AR read model."""

    operational_by_id = {
        str(row.get("ar_item_id") or ""): row
        for row in build_ar_operational_rows(payload)
    }
    gaps: list[dict[str, Any]] = []

    def _add_gap(
        *,
        item: dict[str, Any],
        gap_type: str,
        priority: str,
        suggested_action: str,
        amount: Any = None,
    ) -> None:
        gaps.append(
            {
                "priority": priority,
                "gap_type": gap_type,
                "ar_item_id": item.get("ar_item_id"),
                "operational_status": item.get("operational_status"),
                "payer_name": item.get("payer_name"),
                "payer_rfc": item.get("payer_rfc"),
                "tournament_name": item.get("tournament_name"),
                "phase": item.get("phase"),
                "concept_name": item.get("concept_name"),
                "amount": _safe_float(
                    amount if amount is not None else item.get("issued_amount")
                ),
                "suggested_action": suggested_action,
            }
        )

    for item in operational_by_id.values():
        status = _safe_str(item.get("operational_status"))
        if status == "Revisión sobrepago":
            _add_gap(
                item=item,
                gap_type="sobrepago",
                priority="alta",
                suggested_action="Revisar excedente manualmente en contabilidad.",
            )
        elif status == "Vencido":
            _add_gap(
                item=item,
                gap_type="vencido",
                priority="alta",
                suggested_action="Confirmar cobranza o aceptar match bancario.",
            )
        elif status == "CFDI emitido sin vincular":
            _add_gap(
                item=item,
                gap_type="cfdi_sin_partida",
                priority="media",
                suggested_action="Vincular CFDI a partida de ingreso.",
            )
        elif status == "Cobranza desconocida":
            _add_gap(
                item=item,
                gap_type="cobranza_no_comprobada",
                priority="alta",
                suggested_action="Buscar/aceptar match de cobranza.",
            )
        elif status == "Presupuestado sin CFDI":
            _add_gap(
                item=item,
                gap_type="presupuesto_sin_cfdi",
                priority="baja",
                suggested_action="Dar seguimiento a emisión de CFDI.",
                amount=item.get("expected_income_amount"),
            )
        if (
            item.get("issued_amount")
            and not item.get("payer_name")
            and not item.get("payer_rfc")
        ):
            _add_gap(
                item=item,
                gap_type="cliente_rfc_faltante",
                priority="media",
                suggested_action="Completar cliente/RFC antes de seguimiento.",
            )
    for raw_gap in list(payload.get("matching_gaps") or []):
        item_id = str(raw_gap.get("item_id") or "")
        item = operational_by_id.get(item_id)
        if not item:
            continue
        reason = _safe_str(raw_gap.get("reason"))
        if reason == "missing_budget_income_link":
            _add_gap(
                item=item,
                gap_type="cfdi_sin_partida",
                priority="alta",
                suggested_action="Vincular CFDI a partida de ingreso.",
            )
        elif reason == "payer_gap":
            _add_gap(
                item=item,
                gap_type="cliente_rfc_faltante",
                priority="media",
                suggested_action="Completar cliente/RFC.",
            )

    priority_rank = {"alta": 0, "media": 1, "baja": 2}
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for gap in gaps:
        key = (str(gap.get("ar_item_id") or ""), str(gap.get("gap_type") or ""))
        existing = unique.get(key)
        if existing is None:
            unique[key] = gap
            continue
        if priority_rank.get(str(gap.get("priority")), 9) < priority_rank.get(
            str(existing.get("priority")),
            9,
        ):
            unique[key] = gap
    return sorted(
        unique.values(),
        key=lambda item: (
            priority_rank.get(str(item.get("priority")), 9),
            -_safe_float(item.get("amount")),
            _safe_str(item.get("gap_type")),
        ),
    )
