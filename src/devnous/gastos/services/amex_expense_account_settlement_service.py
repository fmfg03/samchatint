"""Explicit historical-credit selection and append-only DIRECT settlement.

No commit, fiscal reassignment or budget mutation. SQL owns residual concurrency.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from ..models import (
    AmexAccountingCut,
    AmexDirectAccountingCut,
    AmexDirectCutItem,
    AmexDirectEvent,
    AmexDirectItem,
    AmexRecognitionConsumption,
    AmexRecognitionRepresentation,
    AmexStatementImport,
    CFDIReport,
)
from .amex_direct_common import (
    DirectConflict,
    DirectResult,
    actor_scope,
    digest,
    load_statement,
    money,
    normalize_rfc,
    record_event,
    require_unfrozen,
)
from .amex_direct_effective_source_service import effective_source_for_item
from .amex_direct_navigation import save_navigation_cursor

SELECTION_ACTION = "expense_account_source_selected"
IDENTITY_KEYS = (
    "origin_poliza_id",
    "origin_line_id",
    "origin_expense_id",
    "origin_document_id",
    "origin_cuenta_gastos_id",
    "origin_account_id",
)


async def selected_origin(session: Any, item: Any) -> dict[str, Any] | None:
    event = (
        await session.execute(
            select(AmexDirectEvent)
            .where(
                AmexDirectEvent.item_id == item.id,
                AmexDirectEvent.action == SELECTION_ACTION,
            )
            .order_by(
                AmexDirectEvent.source_version.desc(), AmexDirectEvent.created_at.desc()
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if not event:
        return None
    if event.source_version != item.source_version:
        raise DirectConflict("expense_account_selection_stale")
    return dict(event.payload_json)


def _scope_allows(row: Any, config: dict[str, Any], actor_id: UUID) -> bool:
    scope = actor_scope(config, actor_id) or {}
    tournament = str(row.get("tournament_id") or "")
    project = str(row.get("project_code") or "")
    year = row.get("edition_year")
    years = set(scope.get("edition_years", [])) & set(config.get("edition_years", []))
    if year not in years:
        return False
    if tournament:
        return tournament in (
            set(scope.get("tournament_ids", [])) & set(config.get("tournament_ids", []))
        )
    return project in (
        set(scope.get("project_codes", [])) & set(config.get("project_codes", []))
    )


async def credit_source(
    session: Any,
    *,
    origin_line_id: UUID,
    context: Any,
    config: dict[str, Any],
    actor_id: UUID,
    amount: Any,
    locked: bool = False,
    card_id: Any = None,
) -> dict[str, Any]:
    if locked:
        # Source lines have already been sorted/locked by the statement caller.
        await session.execute(
            text("SELECT id FROM accounting_poliza_lines " "WHERE id=:id FOR UPDATE"),
            {"id": origin_line_id},
        )
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('amex-settlement-source-seal-v1',0))"
            )
        )
    row = (
        (
            await session.execute(
                text(
                    (
                        "SELECT * FROM amex_expense_account_credit_sources "
                        "WHERE origin_line_id=:id"
                    )
                ),
                {"id": origin_line_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    kind = "settlement"
    if row is None:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM amex_direct_prior_sources "
                        "WHERE origin_line_id=:id"
                    ),
                    {"id": origin_line_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        kind = "prior"
    if row is None:
        raise DirectConflict("expense_account_credit_not_proven")
    row = dict(row)
    row["coverage_kind"] = kind
    original_cfdi = await session.get(
        CFDIReport, row["origin_cfdi_id"], populate_existing=True
    )
    identity = historical_cfdi_identity(original_cfdi)
    if (
        identity["uuid"] != str(row["cfdi_uuid"]).upper()
        or identity["rfc_receptor"] != context.receiver_rfc
        or digest(original_cfdi.xml_raw.encode()) != row["origin_xml_hash"]
    ):
        raise DirectConflict("expense_account_original_fiscal_identity_changed")
    if kind == "prior" and (
        str(row["card_account_id"]) != str(card_id)
        or money(row["credit_amount"]) != money(amount)
    ):
        raise DirectConflict("recognized_source_identity_changed")
    if (
        row["receiver_rfc"] != context.receiver_rfc
        or row["currency"] != context.currency
        or not _scope_allows(row, config, actor_id)
    ):
        raise DirectConflict("expense_account_source_scope_mismatch")
    if row["ambiguous_liquidation"]:
        raise DirectConflict("expense_account_residual_not_attributable")
    if money(amount) <= 0 or money(row["residual_amount"]) < money(amount):
        raise DirectConflict("expense_account_credit_exhausted")
    if row["prior_amex_recognition"]:
        raise DirectConflict("expense_account_already_amex_recognized")
    return row


async def list_settlement_sources(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    limit: int = 25,
    navigation: dict | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    with session.no_autoflush:
        statement, state, context, config = await load_statement(
            session, actor_id, import_id, "conciliar", finance=True
        )
        item = await session.get(AmexDirectItem, item_id)
        if not item or item.import_id != statement.id or not context:
            raise DirectConflict("direct_item_missing")
        source = await effective_source_for_item(session, item)
        if source.movement_kind != "debit":
            raise DirectConflict("expense_account_debit_required")
        if not 1 <= limit <= 100:
            raise DirectConflict("invalid_expense_account_page_size")
        scope = actor_scope(config, actor_id) or {}
        fingerprint = digest(
            [
                actor_id,
                import_id,
                item_id,
                context.payload_hash,
                context.version,
                state.source_version,
                item.source_version,
                item.source_hash,
                config,
                scope,
                limit,
            ]
        )
        navigation = navigation if navigation is not None else {}
        positions = dict(navigation.get("amex_settlement_source_cursors") or {})
        position = positions.get(cursor) if cursor else None
        if cursor and (not position or position.get("fingerprint") != fingerprint):
            raise DirectConflict("invalid_expense_account_source_cursor")
        columns = ",".join(
            [
                *IDENTITY_KEYS,
                "origin_account_code",
                "credit_amount",
                "residual_amount",
                "receiver_rfc",
                "currency",
                "cfdi_uuid",
                "concepto",
                "employee_id",
                "employee_name",
                "tournament_id",
                "project_code",
                "edition_year",
            ]
        )
        query = (
            "SELECT * FROM (SELECT " + columns + ", 'settlement'::text coverage_kind "
        )
        query += (
            "FROM amex_expense_account_credit_sources WHERE receiver_rfc=:receiver "
        )
        query += "AND currency=:currency AND residual_amount>=:amount "
        query += (
            "AND NOT ambiguous_liquidation AND NOT "
            "prior_amex_recognition UNION ALL SELECT "
        )
        query += (
            columns + ", 'prior'::text coverage_kind FROM amex_direct_prior_sources "
        )
        query += (
            "WHERE receiver_rfc=:receiver AND currency=:currency "
            "AND card_account_id=:card "
        )
        query += "AND credit_amount=:amount) candidates WHERE edition_year=ANY(:years) "
        query += "AND (tournament_id=ANY(CAST(:tournaments AS uuid[])) OR "
        query += "(tournament_id IS NULL AND project_code=ANY(:projects))) "
        params = {
            "receiver": context.receiver_rfc,
            "currency": context.currency,
            "card": statement.card_account_id,
            "amount": abs(money(source.raw_signed_amount)),
            "years": sorted(
                set(scope.get("edition_years", []))
                & set(config.get("edition_years", []))
            ),
            "tournaments": sorted(
                set(scope.get("tournament_ids", []))
                & set(config.get("tournament_ids", []))
            ),
            "projects": sorted(
                set(scope.get("project_codes", []))
                & set(config.get("project_codes", []))
            ),
            "limit": limit + 1,
        }
        if position:
            query += "AND (coverage_kind,origin_poliza_id,origin_line_id) > "
            query += "(:kind,CAST(:poliza AS uuid),CAST(:line AS uuid)) "
            params.update(
                kind=position["kind"], poliza=position["poliza"], line=position["line"]
            )
        query += "ORDER BY coverage_kind,origin_poliza_id,origin_line_id LIMIT :limit"
        rows = (await session.execute(text(query), params)).mappings().all()
        candidates = [dict(row) for row in rows[:limit]]
        has_more = len(rows) > limit
        next_cursor = None
        if has_more:
            last = candidates[-1]
            next_cursor = save_navigation_cursor(
                navigation,
                "amex_settlement_source_cursors",
                {
                    "fingerprint": fingerprint,
                    "kind": last["coverage_kind"],
                    "poliza": str(last["origin_poliza_id"]),
                    "line": str(last["origin_line_id"]),
                },
            )
        return {
            "candidates": candidates,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "item_version": item.source_version,
            "source_version": state.source_version,
            "selected": await selected_origin(session, item),
        }


async def require_no_account_treatment(session: Any, item: Any) -> None:
    """Historical selection cannot supersede an active explicit new destination."""
    from .amex_direct_account_treatment_service import (
        active_account_treatment,
        load_account_treatment_heads,
    )

    if active_account_treatment(
        item, await load_account_treatment_heads(session, [item])
    ):
        raise DirectConflict("account_treatment_historical_source_conflict")


async def select_settlement_source(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    origin_poliza_id: UUID,
    origin_line_id: UUID,
    origin_expense_id: UUID,
    origin_document_id: UUID,
    origin_cuenta_gastos_id: UUID,
    origin_account_id: UUID,
    expected_item_version: int,
    expected_source_version: int,
    reason: str,
    confirmed: bool,
) -> DirectResult:
    statement, state, context, config = await load_statement(
        session, actor_id, import_id, "conciliar", finance=True, locked=True
    )
    await require_unfrozen(session, import_id)
    item = await session.get(
        AmexDirectItem, item_id, with_for_update=True, populate_existing=True
    )
    if not item or item.import_id != statement.id or not context:
        raise DirectConflict("direct_item_missing")
    await require_no_account_treatment(session, item)
    if (
        item.source_version != expected_item_version
        or state.source_version != expected_source_version
    ):
        raise DirectConflict("stale_direct_source")
    if not confirmed or not reason.strip():
        raise DirectConflict("expense_account_selection_confirmation_required")
    source = await effective_source_for_item(session, item)
    if source.movement_kind != "debit":
        raise DirectConflict("expense_account_debit_required")
    row = await credit_source(
        session,
        origin_line_id=origin_line_id,
        context=context,
        config=config,
        actor_id=actor_id,
        amount=abs(money(source.raw_signed_amount)),
        card_id=statement.card_account_id,
    )
    expected = locals()
    if any(str(row[key]) != str(expected[key]) for key in IDENTITY_KEYS):
        raise DirectConflict("expense_account_origin_identity_mismatch")
    if (
        await session.execute(
            select(AmexRecognitionConsumption.id).where(
                AmexRecognitionConsumption.imported_expense_id
                == source.imported_expense_id
            )
        )
    ).first():
        raise DirectConflict("expense_account_bank_already_recognized")
    payload = {key: str(row[key]) for key in IDENTITY_KEYS}
    payload.update(
        coverage_kind=row["coverage_kind"],
        origin_account_code=row["origin_account_code"],
        reason=reason.strip(),
        proof_hash=digest(row),
        amount=str(abs(money(source.raw_signed_amount))),
    )
    async with session.begin_nested():
        await record_event(session, item, state, actor_id, SELECTION_ACTION, payload)
        await session.flush()
    return DirectResult(
        "selected",
        statement=statement,
        context=context,
        items=[item],
        source_version=state.source_version,
        progress={"selected": payload, "item_version": item.source_version},
    )


async def prepare_settlement(
    session: Any,
    *,
    item: Any,
    context: Any,
    config: dict[str, Any],
    actor_id: UUID,
    source: Any,
    locked: bool = False,
) -> dict[str, Any] | None:
    selection = await selected_origin(session, item)
    if not selection:
        return None
    row = await credit_source(
        session,
        origin_line_id=UUID(selection["origin_line_id"]),
        context=context,
        config=config,
        actor_id=actor_id,
        amount=abs(money(source.raw_signed_amount)),
        locked=locked,
        card_id=(
            await session.get(AmexStatementImport, item.import_id)
        ).card_account_id,
    )
    if any(str(row[k]) != selection[k] for k in IDENTITY_KEYS):
        raise DirectConflict("expense_account_origin_identity_mismatch")
    if selection["proof_hash"] != digest(row):
        raise DirectConflict("expense_account_origin_changed")
    row["selection"] = selection
    return row


def settlement_lines(
    proof: dict[str, Any], *, expense_id: UUID, amount: Any, liability: Any
) -> list[dict[str, Any]]:
    metadata = {
        "source_kind": "amex_direct",
        "coverage_kind": "settlement",
        "expense_id": str(expense_id),
        "origin_line_id": str(proof["origin_line_id"]),
        "cuenta_gastos_id": str(proof["origin_cuenta_gastos_id"]),
        "documento_id": str(proof["origin_document_id"]),
        "empleado_id": str(proof["employee_id"]),
        "empleado_nombre": proof["employee_name"],
    }
    common = {
        "concepto": "Cancelación cuenta de gastos contra AMEX",
    }
    return [
        {
            **common,
            "cuenta_codigo": proof["origin_account_code"],
            "cuenta_contable_id": proof["origin_account_id"],
            "debe": money(amount),
            "haber": money(0),
            "raw_row_json": {**metadata, "movement": "debe_cuenta_gastos"},
        },
        {
            **common,
            "cuenta_codigo": liability.codigo,
            "cuenta_contable_id": liability.id,
            "debe": money(0),
            "haber": money(amount),
            "raw_row_json": {**metadata, "movement": "haber_amex"},
        },
    ]


async def prior_source(
    session: Any,
    *,
    item: Any,
    source: Any,
    context: Any,
    card: Any,
    config: dict[str, Any],
    actor_id: UUID,
) -> dict[str, Any] | None:
    """Only explicit bank representation/association, never CFDI/name matching."""
    selection = await selected_origin(session, item)
    if selection and selection.get("coverage_kind") == "prior":
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM amex_direct_prior_sources "
                        "WHERE origin_line_id=:id"
                    ),
                    {"id": UUID(selection["origin_line_id"])},
                )
            )
            .mappings()
            .one_or_none()
        )
        if not row or any(str(row[k]) != selection[k] for k in IDENTITY_KEYS):
            raise DirectConflict("recognized_source_receipt_incomplete")
        if not _scope_allows(row, config, actor_id):
            raise DirectConflict("expense_account_source_scope_mismatch")
        raw = dict(row)
        raw["coverage_kind"] = "prior"
        if (
            digest(raw) != selection["proof_hash"]
            or row["receiver_rfc"] != context.receiver_rfc
        ):
            raise DirectConflict("recognized_source_identity_changed")
        c = await session.get(AmexRecognitionConsumption, row["consumption_id"])
        r = await session.get(AmexRecognitionRepresentation, row["representation_id"])
        if c.card_account_id != card.id or money(c.amount) != abs(
            money(source.raw_signed_amount)
        ):
            raise DirectConflict("recognized_source_identity_changed")
        cut = await session.get(AmexAccountingCut, row["original_cut_id"])
        return {
            "consumption": c,
            "representation": r,
            "cut": cut,
            "direct_cut": None,
            "selection": selection,
        }
    query = (
        select(AmexRecognitionConsumption, AmexRecognitionRepresentation)
        .join(
            AmexRecognitionRepresentation,
            AmexRecognitionRepresentation.consumption_id
            == AmexRecognitionConsumption.id,
        )
        .where(AmexRecognitionRepresentation.expense_id == source.imported_expense_id)
    )
    pairs = (await session.execute(query)).all()
    targets = [(c, r) for c, r in pairs if c.binding_mode != "report_bridge"]
    if any(owner.binding_mode == "report_bridge" for owner, _ in pairs):
        # The legacy binder rejects DIRECT bank sources. A header without our
        # explicit audited prior selection is not a direct accounting proof.
        raise DirectConflict("prior_bank_requires_explicit_selection")
    if not targets:
        return None
    if len(targets) != 1:
        raise DirectConflict("prior_source_requires_one_exact_receipt")
    c, r = targets[0]
    if r:
        scope_row = (
            (
                await session.execute(
                    text(
                        "SELECT c.torneo_id tournament_id,e.proyecto project_code,"
                        "extract(year FROM e.fecha)::integer edition_year "
                        "FROM expense_reports e LEFT JOIN cuentas_de_gastos c "
                        "ON c.id=e.cuenta_gastos_id WHERE e.id=:id"
                    ),
                    {"id": r.expense_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if not scope_row or not _scope_allows(scope_row, config, actor_id):
            raise DirectConflict("expense_account_source_scope_mismatch")
    if (
        not r
        or not c.accounting_poliza_id
        or c.binding_mode == "report_bridge"
        or c.card_account_id != card.id
        or c.currency != context.currency
        or money(c.amount) != abs(money(source.raw_signed_amount))
    ):
        raise DirectConflict("recognized_source_identity_changed")
    cut = (
        await session.execute(
            select(AmexAccountingCut).where(
                AmexAccountingCut.accounting_poliza_id == c.accounting_poliza_id
            )
        )
    ).scalar_one_or_none()
    direct_cut = (
        await session.execute(
            select(AmexDirectAccountingCut).where(
                AmexDirectAccountingCut.accounting_poliza_id == c.accounting_poliza_id
            )
        )
    ).scalar_one_or_none()
    if cut and str(c.id) not in (cut.snapshot_json.get("consumption_ids") or []):
        raise DirectConflict("recognized_source_receipt_incomplete")
    if (
        direct_cut
        and not (
            await session.execute(
                select(AmexDirectCutItem.id).where(
                    AmexDirectCutItem.cut_id == direct_cut.id,
                    AmexDirectCutItem.consumption_id == c.id,
                    AmexDirectCutItem.coverage_kind == "new",
                )
            )
        ).first()
    ):
        raise DirectConflict("recognized_source_receipt_incomplete")
    if not cut and not direct_cut:
        raise DirectConflict("recognized_source_receipt_incomplete")
    return {"consumption": c, "representation": r, "cut": cut, "direct_cut": direct_cut}


def historical_cfdi_identity(cfdi: Any) -> dict[str, Any]:
    """Verify real historical XML without manufacturing a new SAT application.

    Called only after an exact posted comprobación credit or report receipt is proven.
    R1's canonical SAT identity validator and all global usage guards stay intact.
    """
    from .cfdi_ingestion_service import validate_cfdi_material_identity
    from .cfdi_parser import parse_cfdi_xml

    if cfdi is None or not cfdi.xml_raw:
        raise DirectConflict("historical_cfdi_xml_required")
    try:
        fiscal_uuid = str(UUID(str(cfdi.cfdi_uuid))).upper()
        parsed = parse_cfdi_xml(cfdi.xml_raw)
        if str(parsed.get("cfdi_uuid") or "").upper() != fiscal_uuid:
            raise ValueError("historical UUID changed")
        validate_cfdi_material_identity(cfdi, parsed)
    except Exception as exc:
        raise DirectConflict("historical_cfdi_identity_conflict") from exc
    if str(cfdi.tipo_de_comprobante or "").upper() != "I":
        raise DirectConflict("invoice_type_i_required")
    if str(cfdi.moneda or "").upper() != "MXN":
        raise DirectConflict("unsupported_direct_currency")
    return {
        "uuid": fiscal_uuid,
        "rfc_emisor": normalize_rfc(cfdi.emisor_rfc),
        "rfc_receptor": normalize_rfc(cfdi.receptor_rfc),
        "total": str(money(cfdi.total)),
        "xml_hash": digest(cfdi.xml_raw.encode()),
    }
