"""Read-only, receiver-scoped, deterministic DIRECT invoice suggestions.

The PostgreSQL lateral query ranks the *entire* authorized catalog before
limiting each item's page. Scores are ordering evidence, never authority to link.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from ..models import AmexMerchantIdentityAlias, CFDIReport, ExpenseReport
from .amex_direct_common import (
    DirectConflict,
    descriptor_key,
    digest,
    load_items,
    load_statement,
    money,
)
from .amex_direct_effective_source_service import load_effective_sources
from .amex_direct_matching_service import (
    _available,
    cfdi_identity,
    current_status_receipt,
)
from .amex_direct_navigation import save_navigation_cursor
from .cfdi_parser import parse_cfdi_xml

RANKING_VERSION = "suggestions_v2"
CURSOR_KEY = "amex_direct_suggestion_cursors"
_ACCENT_FROM = "áéíóúüñ"
_ACCENT_TO = "aeiouun"

# Lexical noise only: removing these tokens never proves merchant identity.
# SQL and Python use exactly this finite set, with no distance/score threshold.
ISSUER_STOPWORDS = frozenset(
    {
        "a",
        "c",
        "v",
        "s",
        "r",
        "l",
        "sa",
        "sas",
        "sc",
        "sapi",
        "cv",
        "rl",
        "srl",
        "de",
        "del",
        "el",
        "la",
        "los",
        "las",
        "y",
        "sociedad",
        "anonima",
        "capital",
        "variable",
        "responsabilidad",
        "limitada",
        "mexico",
        "mex",
        "cdmx",
        "restaurante",
        "restaurantes",
        "restaurant",
        "restaurants",
    }
)


def merchant_tokens(value: str) -> set[str]:
    """Portable SQL/Python normalization; no extension or locale assumptions."""
    normalized = (
        str(value or "").lower().translate(str.maketrans(_ACCENT_FROM, _ACCENT_TO))
    )
    return set(re.findall(r"[a-z0-9]+", normalized))


def merchant_similarity(left: str, right: str) -> Decimal:
    """Jaccard token overlap, used for explanatory parity tests."""
    a, b = merchant_tokens(left), merchant_tokens(right)
    return Decimal(len(a & b)) / Decimal(len(a | b)) if a | b else Decimal(0)


def issuer_tokens(value: str) -> set[str]:
    """Keep distinctive commerce tokens separate from legal/location noise."""
    return merchant_tokens(value) - ISSUER_STOPWORDS


def issuer_similarity(left: str, right: str) -> Decimal:
    """Jaccard of normalized issuer-name evidence; never fiscal authority."""
    a, b = issuer_tokens(left), issuer_tokens(right)
    return Decimal(len(a & b)) / Decimal(len(a | b)) if a | b else Decimal(0)


# Parameters only; descriptors, identities and IDs are never interpolated.
# Invalid/nonfinite Float projections cannot be cast to numeric before filtering.
_RANK_SQL = text(
    """
WITH requested AS (
 SELECT * FROM jsonb_to_recordset(CAST(:items AS jsonb)) AS i(
   item_id uuid, amount numeric, bank_date date, descriptor text,
   declared_uuid text, issuer_rfc text, candidate_offset integer)
), catalog AS (
 SELECT c.id, c.cfdi_uuid, c.emisor_rfc, c.fecha,
   CASE WHEN c.total::text NOT IN ('NaN','Infinity','-Infinity')
     THEN round(c.total::text::numeric,2) END AS amount,
   ARRAY(SELECT DISTINCT t FROM unnest(regexp_split_to_array(
     regexp_replace(translate(lower(coalesce(c.emisor_nombre,'')),
       'áéíóúüñ','aeiouun'),'[^a-z0-9]+',' ','g'),' +')) AS t
     WHERE t<>'' AND t<>ALL(CAST(:merchant_stopwords AS text[]))) AS issuer_tokens,
   ARRAY(SELECT DISTINCT t FROM unnest(regexp_split_to_array(
     regexp_replace(translate(lower(concat_ws(' ',c.emisor_rfc,
       c.descripcion_concepto_principal)), 'áéíóúüñ','aeiouun'),
       '[^a-z0-9]+',' ','g'), ' +')) AS t
     WHERE t<>'' AND t<>ALL(CAST(:merchant_stopwords AS text[]))) AS fallback_tokens
 FROM cfdi_reports c
 WHERE c.origen='sat' AND upper(trim(c.receptor_rfc))=:receiver
   AND upper(c.moneda)='MXN' AND upper(c.tipo_de_comprobante)='I'
)
SELECT i.item_id, ranked.* FROM requested i
CROSS JOIN LATERAL (
 SELECT c.id AS cfdi_id, c.cfdi_uuid,
   coalesce(((i.declared_uuid IS NOT NULL AND upper(c.cfdi_uuid)=i.declared_uuid)
     OR (i.issuer_rfc IS NOT NULL
       AND upper(trim(c.emisor_rfc))=i.issuer_rfc)),false) AS identity_match,
   c.amount=i.amount AS exact_amount,
   issuer_overlap.n::numeric / greatest(issuer_union.n,1) AS similarity,
   fallback_overlap.n::numeric / greatest(fallback_union.n,1) AS fallback_similarity,
   c.amount-i.amount AS amount_delta,
   abs(c.amount-i.amount)/i.amount AS relative_delta,
   c.fecha::date-i.bank_date AS days_delta,
   count(*) OVER () AS catalog_count
 FROM catalog c
 CROSS JOIN LATERAL (
   SELECT ARRAY(SELECT DISTINCT t FROM unnest(regexp_split_to_array(
     regexp_replace(translate(lower(coalesce(i.descriptor,'')),
       'áéíóúüñ','aeiouun'),'[^a-z0-9]+',' ','g'),' +')) AS t
     WHERE t<>'' AND t<>ALL(CAST(:merchant_stopwords AS text[]))) AS tokens
 ) bank
 CROSS JOIN LATERAL (
   SELECT count(*) AS n FROM
     (SELECT unnest(c.issuer_tokens) INTERSECT SELECT unnest(bank.tokens)) s
 ) issuer_overlap
 CROSS JOIN LATERAL (
   SELECT count(*) AS n FROM
     (SELECT unnest(c.issuer_tokens) UNION SELECT unnest(bank.tokens)) s
 ) issuer_union
 CROSS JOIN LATERAL (
   SELECT count(*) AS n FROM
     (SELECT unnest(c.fallback_tokens) INTERSECT SELECT unnest(bank.tokens)) s
 ) fallback_overlap
 CROSS JOIN LATERAL (
   SELECT count(*) AS n FROM
     (SELECT unnest(c.fallback_tokens) UNION SELECT unnest(bank.tokens)) s
 ) fallback_union
 WHERE c.amount>0 AND i.amount>0
 ORDER BY identity_match DESC, exact_amount DESC, similarity DESC,
   fallback_similarity DESC, relative_delta ASC,
   abs(c.fecha::date-i.bank_date) ASC NULLS LAST,
   upper(c.cfdi_uuid) ASC NULLS LAST, c.id ASC
 LIMIT :take OFFSET i.candidate_offset
) ranked
ORDER BY i.item_id
"""
).bindparams(merchant_stopwords=sorted(ISSUER_STOPWORDS))


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except (ValueError, TypeError):
        return None


def _cursor(navigation: dict, payload: dict) -> str:
    return save_navigation_cursor(navigation, CURSOR_KEY, payload)


async def suggest_direct_statement_cfdis(
    session: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    navigation: dict,
    item_ids: list[UUID] | None = None,
    cursor: str | None = None,
    page_size: int = 25,
    candidates_per_item: int = 3,
) -> dict[str, Any]:
    """Authorize before source/catalog reads; never flush, lock, reserve or call SAT."""
    with session.no_autoflush:
        return await _suggest(
            session,
            actor_id=actor_id,
            import_id=import_id,
            navigation=navigation,
            item_ids=item_ids,
            cursor=cursor,
            page_size=page_size,
            candidates_per_item=candidates_per_item,
        )


async def _suggest(session: Any, **options: Any) -> dict[str, Any]:
    from .amex_direct_fiscal_evidence_service import (
        read_direct_cfdi_fiscal_evidence_batch,
    )

    actor_id, import_id = options["actor_id"], options["import_id"]
    navigation, cursor = options["navigation"], options["cursor"]
    page_size, per_item = options["page_size"], options["candidates_per_item"]
    statement, state, context, _ = await load_statement(
        session, actor_id, import_id, "conciliar", finance=True
    )
    if context is None:
        raise DirectConflict("contexto_fiscal_pendiente")
    if not 1 <= page_size <= 25 or not 1 <= per_item <= 3:
        raise DirectConflict("invalid_cfdi_suggestions")
    items = await load_items(session, import_id)
    sources = await load_effective_sources(session, import_id)
    wanted = options["item_ids"]
    if wanted is not None and (
        len(wanted) > 25 or not set(wanted) <= {i.id for i in items}
    ):
        raise DirectConflict("item_context_mismatch")
    eligible = [
        i
        for i in items
        if sources[i.id].movement_kind == "debit" and (wanted is None or i.id in wanted)
    ]
    fingerprint = digest(
        [
            RANKING_VERSION,
            actor_id,
            import_id,
            context.id,
            context.version,
            context.receiver_rfc,
            state.source_version,
            [(i.id, i.source_version, i.source_hash) for i in items],
            page_size,
            per_item,
        ]
    )
    offset, candidate_offsets = 0, {}
    if cursor:
        position = (navigation.get(CURSOR_KEY) or {}).get(cursor)
        if not position or position.get("fingerprint") != fingerprint:
            raise DirectConflict("invalid_cfdi_suggestions_cursor")
        if position.get("item_ids") != (
            sorted(str(v) for v in wanted) if wanted is not None else None
        ):
            raise DirectConflict("invalid_cfdi_suggestions_cursor")
        offset, candidate_offsets = position["offset"], position["candidate_offsets"]
    selected = eligible[offset : offset + page_size]
    aliases = []
    if selected:
        aliases = list(
            (
                await session.execute(
                    select(AmexMerchantIdentityAlias).where(
                        AmexMerchantIdentityAlias.entity_code == context.entity_code,
                        AmexMerchantIdentityAlias.card_account_id
                        == statement.card_account_id,
                        AmexMerchantIdentityAlias.active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
    expenses = {}
    expense_ids = [sources[i.id].imported_expense_id for i in selected]
    if expense_ids:
        expenses = {
            e.id: e
            for e in (
                await session.execute(
                    select(ExpenseReport).where(ExpenseReport.id.in_(expense_ids))
                )
            )
            .scalars()
            .all()
        }
    inputs, output = [], []
    for item in selected:
        source = sources[item.id]
        data = source.source_json
        amount = money(data["amount"]) if data.get("amount") is not None else None
        descriptor = str(data.get("description") or "")
        matches = [a for a in aliases if a.descriptor == descriptor_key(descriptor)]
        issuer = item.issuer_rfc or (
            matches[0].issuer_rfc if len(matches) == 1 else None
        )
        row = dict(
            item_id=str(item.id),
            item_version=item.source_version,
            row_number=data.get("row_number"),
            bank_descriptor=descriptor,
            bank_date=str(_date(data.get("date")) or ""),
            bank_amount=str(amount) if amount is not None else None,
            candidates=[],
            has_more=False,
            cursor=None,
            examined=0,
            coverage_complete=True,
            pending_reason=None,
        )
        output.append(row)
        if amount is None or amount <= 0 or source.imported_expense_id not in expenses:
            row.update(coverage_complete=False, pending_reason="fiscal_source_missing")
            continue
        inputs.append(
            dict(
                item_id=str(item.id),
                amount=str(amount),
                bank_date=_date(data.get("date")),
                descriptor=descriptor,
                declared_uuid=item.declared_uuid,
                issuer_rfc=str(issuer).strip().upper() if issuer else None,
                candidate_offset=candidate_offsets.get(str(item.id), 0),
            )
        )
    ranked = []
    if inputs:
        ranked = list(
            (
                await session.execute(
                    _RANK_SQL,
                    dict(
                        items=json.dumps(inputs, default=str),
                        receiver=context.receiver_rfc,
                        take=per_item + 1,
                    ),
                )
            )
            .mappings()
            .all()
        )
    ids = {
        row["cfdi_id"]
        for item in selected
        for row in [r for r in ranked if str(r["item_id"]) == str(item.id)][:per_item]
    }
    cfdis = {}
    if ids:
        cfdis = {
            c.id: c
            for c in (
                await session.execute(select(CFDIReport).where(CFDIReport.id.in_(ids)))
            )
            .scalars()
            .all()
        }
    evidence_cache = (
        await read_direct_cfdi_fiscal_evidence_batch(
            session,
            list(cfdis.values()),
            receiver_rfc=context.receiver_rfc,
            company_code=context.company_code,
        )
        if cfdis
        else {}
    )
    for item, row in zip(selected, output):
        hits = [r for r in ranked if str(r["item_id"]) == str(item.id)]
        examined = hits[:per_item]
        row.update(examined=len(examined), has_more=len(hits) > per_item)
        for hit in examined:
            cfdi = cfdis.get(hit["cfdi_id"])
            if cfdi is None:
                continue
            try:
                identity = cfdi_identity(cfdi)
                if (
                    identity["rfc_receptor"] != context.receiver_rfc
                    or money(identity["total"]) <= 0
                ):
                    continue
                parsed = parse_cfdi_xml(cfdi.xml_raw)
                receipt = await current_status_receipt(session, cfdi)
                available = await _available(
                    session, cfdi, expenses[sources[item.id].imported_expense_id]
                )
            except DirectConflict:
                continue
            delta = money(identity["total"]) - money(row["bank_amount"])
            fiscal_date, bank_date = _date(parsed.get("fecha")), _date(row["bank_date"])
            days = (fiscal_date - bank_date).days if fiscal_date and bank_date else None
            row["candidates"].append(
                dict(
                    id=str(cfdi.id),
                    uuid=identity["uuid"],
                    issuer_rfc=identity["rfc_emisor"],
                    issuer_name=parsed.get("emisor_nombre"),
                    date=str(fiscal_date or ""),
                    total=identity["total"],
                    amount_delta=str(delta),
                    days_delta=days,
                    identity_hash=digest(identity),
                    status=(
                        receipt.observed_status if receipt else "sin vigencia reciente"
                    ),
                    observed_at=str(receipt.observed_at) if receipt else None,
                    available=available,
                    provisional=True,
                    reasons=dict(
                        identity_match=bool(hit["identity_match"]),
                        exact_amount=delta == 0,
                        merchant_similarity=str(hit["similarity"]),
                        fallback_similarity=str(hit.get("fallback_similarity", 0)),
                        amount_delta=str(delta),
                        days_delta=days,
                    ),
                    **evidence_cache[str(cfdi.id)],
                )
            )
        if row["has_more"]:
            row["coverage_complete"] = False
            if wanted is not None and len(wanted) == 1:
                row["cursor"] = _cursor(
                    navigation,
                    dict(
                        fingerprint=fingerprint,
                        item_ids=[str(item.id)],
                        offset=0,
                        candidate_offsets={
                            str(item.id): candidate_offsets.get(str(item.id), 0)
                            + len(examined)
                        },
                    ),
                )
        if not row["candidates"] and not row["coverage_complete"]:
            row["pending_reason"] = (
                row["pending_reason"] or "candidate_search_incomplete"
            )
    has_more = offset + page_size < len(eligible)
    next_cursor = (
        _cursor(
            navigation,
            dict(
                fingerprint=fingerprint,
                item_ids=sorted(str(v) for v in wanted) if wanted is not None else None,
                offset=offset + page_size,
                candidate_offsets={},
            ),
        )
        if has_more
        else None
    )
    return dict(
        items=output,
        has_more=has_more,
        next_cursor=next_cursor,
        ranking_version=RANKING_VERSION,
        source_version=state.source_version,
        statement=statement,
        context=context,
    )
