"""Bounded SAT observations; transactions are closed before every SOAP call."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from devnous.sat.cfdi_status_service import SATCFDIStatusService

from ..models import AmexCFDIStatusReceipt, CFDIReport, ExpenseReport
from .amex_direct_common import (
    DirectConflict,
    DirectResult,
    authorize,
    configuration_for,
    digest,
    load_items,
    load_statement,
)
from .amex_direct_matching_service import (
    _merchant,
    cfdi_identity,
    current_status_receipt,
    reconcile_direct_statement,
)

REUSABLE_STATUSES = frozenset({"vigente", "cancelado"})

MAX_QUERIES = 50
MAX_CONCURRENCY = 2
EXTERNAL_BUDGET_SECONDS = 30


def _lease_active(progress: dict[str, Any], now: datetime) -> bool:
    if not progress.get("running"):
        return False
    stamp = datetime.fromisoformat(progress["started_at"])
    return now - stamp < timedelta(seconds=EXTERNAL_BUDGET_SECONDS + 5)


async def _status_candidates(
    session: Any, statement: Any, context: Any, items: list[Any]
) -> list[dict[str, Any]]:
    from datetime import timedelta

    from .amex_direct_effective_source_service import effective_source_for_item

    candidates = {}
    for item in items:
        source = await effective_source_for_item(session, item)
        if source.movement_kind != "debit" or item.accounting_status == "recognized":
            continue
        expense = await session.get(ExpenseReport, source.imported_expense_id)
        if expense is None or expense.fecha is None:
            continue
        merchant, _ = await _merchant(session, item, context, statement.card_account_id)
        if not item.declared_uuid and not merchant:
            continue
        query = (
            select(CFDIReport)
            .where(
                CFDIReport.origen == "sat",
                func.upper(CFDIReport.receptor_rfc) == context.receiver_rfc,
                func.upper(CFDIReport.moneda) == "MXN",
                func.upper(CFDIReport.tipo_de_comprobante) == "I",
            )
            .order_by(CFDIReport.id)
        )
        if item.declared_uuid:
            query = query.where(func.upper(CFDIReport.cfdi_uuid) == item.declared_uuid)
        else:
            query = query.where(
                func.upper(CFDIReport.emisor_rfc) == merchant,
                CFDIReport.fecha >= expense.fecha - timedelta(days=3),
                CFDIReport.fecha <= expense.fecha + timedelta(days=3),
            )
        for cfdi in (await session.execute(query)).scalars().all():
            try:
                identity = cfdi_identity(cfdi)
            except DirectConflict:
                continue
            from .amex_direct_common import money

            if money(identity["total"]) != money(expense.gasto_cantidad):
                continue
            receipt = await current_status_receipt(session, cfdi)
            if receipt and receipt.observed_status not in REUSABLE_STATUSES:
                receipt = None
            candidates[str(cfdi.id)] = {
                "cfdi_id": str(cfdi.id),
                "identity": identity,
                "request_hash": digest(identity),
                "receipt_id": str(receipt.id) if receipt else None,
            }
    return [candidates[key] for key in sorted(candidates)]


def _observed_request_matches(
    response: dict[str, Any], identity: dict[str, Any]
) -> bool:
    from .amex_direct_common import money, normalize_rfc

    request = response.get("request")
    if not isinstance(request, dict):
        return False
    try:
        return bool(
            str(UUID(str(request["uuid"]))).upper() == identity["uuid"]
            and normalize_rfc(request["rfc_emisor"]) == identity["rfc_emisor"]
            and normalize_rfc(request["rfc_receptor"]) == identity["rfc_receptor"]
            and money(request["total"]) == money(identity["total"])
        )
    except (KeyError, ValueError):
        return False


async def consult_direct_statement_status(
    factory: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    expected_source_version: int,
    request_key: str,
    consented: bool,
    status_service: Any = None,
) -> DirectResult:
    """Process at most one server-selected chunk; never hold locks during SOAP."""
    if not consented or not request_key:
        return DirectResult("pending", "sat_consent_required")
    started = datetime.now(timezone.utc)
    async with factory.begin() as session:
        statement, state, context, config = await load_statement(
            session, actor_id, import_id, "vigencia", locked=True, finance=True
        )
        if not context or state.source_version != expected_source_version:
            return DirectResult("pending", "stale_source_version")
        prior = state.progress_json or {}
        if _lease_active(prior.get("selected_consultation") or {}, started):
            return DirectResult("pending", "sat_consultation_in_progress")
        if prior.get("running") and prior.get("source_version") == state.source_version:
            stamp = datetime.fromisoformat(prior["started_at"])
            if started - stamp < timedelta(seconds=EXTERNAL_BUDGET_SECONDS + 5):
                return DirectResult(
                    "pending",
                    "sat_consultation_in_progress",
                    source_version=state.source_version,
                    progress=prior,
                )
        items = await load_items(session, import_id)
        candidates = await _status_candidates(session, statement, context, items)
        if (
            prior.get("request_key") == request_key
            and prior.get("source_version") == state.source_version
            and prior.get("context_id") == str(context.id)
        ):
            progress = dict(prior)
            # A completed cursor is not an everlasting status receipt. Reuse
            # only the current exact XML/status proof in the same ordered slot.
            old_candidates = prior.get("candidates") or []
            progress["completed"] = {
                str(index): {"receipt_id": candidate["receipt_id"], "outcome": "cached"}
                for index, candidate in enumerate(candidates)
                if candidate.get("receipt_id")
                and str(index) in prior.get("completed", {})
                and index < len(old_candidates)
                and candidate["cfdi_id"] == old_candidates[index]["cfdi_id"]
                and candidate["request_hash"] == old_candidates[index]["request_hash"]
            }
            cursor = 0
            while str(cursor) in progress["completed"]:
                cursor += 1
            progress.update(candidates=candidates, cursor=cursor, total=len(candidates))
        else:
            progress = {
                "request_key": request_key,
                "context_id": str(context.id),
                "context_version": context.version,
                "source_version": state.source_version,
                "candidates": candidates,
                "completed": {},
                "cursor": 0,
                "observed": 0,
                "total": len(candidates),
            }
        progress.update(running=True, started_at=started.isoformat())
        state.progress_json = progress
        source_version = state.source_version
        context_id = context.id
        candidates = progress["candidates"]
        cursor = int(progress["cursor"])
    # There is no open SQL session, transaction or recognition lock here.
    service = status_service or SATCFDIStatusService()
    stop = cursor + MAX_QUERIES
    selected = list(enumerate(candidates[cursor:stop], cursor))
    deadline = asyncio.get_running_loop().time() + EXTERNAL_BUDGET_SECONDS
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def observe(index: int, candidate: dict[str, Any]):
        if candidate.get("receipt_id"):
            return index, candidate, None, "cached"
        async with semaphore:
            if asyncio.get_running_loop().time() >= deadline:
                return index, candidate, None, "pending"
            identity = candidate["identity"]
            try:
                response = await service.consult_status(
                    uuid=identity["uuid"],
                    rfc_emisor=identity["rfc_emisor"],
                    rfc_receptor=identity["rfc_receptor"],
                    total=identity["total"],
                )
                if not isinstance(response, dict) or not _observed_request_matches(
                    response, identity
                ):
                    return index, candidate, None, "invalid_observed_request"
                return index, candidate, response, "observed"
            except (asyncio.TimeoutError, OSError, ValueError):
                return index, candidate, None, "error"

    tasks = [
        asyncio.create_task(observe(index, candidate)) for index, candidate in selected
    ]
    observations = []
    try:
        if tasks:
            done, pending = await asyncio.wait(
                tasks, timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            observations = [task.result() for task in done if not task.cancelled()]
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with factory.begin() as session:
            _, state, _, _ = await load_statement(
                session, actor_id, import_id, "vigencia", locked=True, finance=True
            )
            if (
                state.progress_json
                and state.progress_json.get("request_key") == request_key
            ):
                updated = dict(state.progress_json)
                updated["running"] = False
                state.progress_json = updated
        raise
    observed = {}
    attempts = {}
    for index, candidate, response, outcome in sorted(observations):
        receipt_id = candidate.get("receipt_id")
        if response is not None:
            identity = candidate["identity"]
            async with factory.begin() as session:
                # A real observation may be kept for its original XML identity
                # even if this source changed; it is never applied to a new one.
                current_config = configuration_for(
                    statement.card_account_id,
                    context.company_code,
                    context.receiver_rfc,
                )
                if current_config is None:
                    from .amex_direct_common import DirectAccessError

                    raise DirectAccessError("actor_scope_denied")
                await authorize(
                    session,
                    actor_id,
                    "vigencia",
                    config=current_config,
                    finance=True,
                    locked=True,
                )
                cfdi = await session.get(CFDIReport, UUID(candidate["cfdi_id"]))
                if cfdi is not None:
                    receipt = AmexCFDIStatusReceipt(
                        cfdi_id=cfdi.id,
                        fiscal_uuid=identity["uuid"],
                        issuer_rfc=identity["rfc_emisor"],
                        receiver_rfc=identity["rfc_receptor"],
                        amount=identity["total"],
                        xml_hash=identity["xml_hash"],
                        request_hash=candidate["request_hash"],
                        observed_status=str(
                            response.get("status") or "unknown"
                        ).lower(),
                        response_json=response,
                        response_hash=digest(response),
                        actor_id=actor_id,
                        observed_at=datetime.now(timezone.utc),
                    )
                    session.add(receipt)
                    await session.flush()
                    receipt_id = str(receipt.id)
        terminal = outcome == "cached" or (
            response is not None and response.get("status") in REUSABLE_STATUSES
        )
        attempt = {"receipt_id": receipt_id, "outcome": outcome}
        if response is not None:
            attempt["status"] = str(response.get("status") or "unknown")
        attempts[str(index)] = attempt
        if terminal and receipt_id:
            observed[str(index)] = attempt
    async with factory.begin() as session:
        statement, state, context, config = await load_statement(
            session, actor_id, import_id, "vigencia", locked=True, finance=True
        )
        if (
            state.source_version != source_version
            or context.id != context_id
            or not state.progress_json
            or state.progress_json.get("request_key") != request_key
        ):
            return DirectResult(
                "pending",
                "sat_progress_source_changed",
                statement=statement,
                source_version=state.source_version,
            )
        progress = dict(state.progress_json)
        completed = dict(progress["completed"])
        completed.update(observed)
        next_cursor = cursor
        while str(next_cursor) in completed:
            next_cursor += 1
        history = list(progress.get("attempts") or [])
        history.append({"started_at": started.isoformat(), "outcomes": attempts})
        progress.update(
            attempts=history,
            completed=completed,
            cursor=next_cursor,
            running=False,
            observed=sum(bool(v["receipt_id"]) for v in completed.values()),
            total=len(candidates),
        )
        state.progress_json = progress
        await session.flush()
        matched = await reconcile_direct_statement(
            session,
            actor_id=actor_id,
            import_id=import_id,
            expected_source_version=state.source_version,
        )
        # Own matching changed projection versions, not the consulted XML/source.
        progress["source_version"] = state.source_version
        state.progress_json = progress
        matched.progress = progress
        return matched


async def consult_selected_direct_cfdi_status(
    factory: Any,
    *,
    actor_id: UUID,
    import_id: UUID,
    item_id: UUID,
    cfdi_id: UUID,
    expected_source_version: int,
    expected_item_version: int,
    expected_identity_hash: str,
    request_key: str,
    consented: bool,
    status_service: Any = None,
) -> DirectResult:
    """Observe exactly one explicit choice, without linking or widening matching."""
    from .amex_direct_common import require_unfrozen
    from .amex_direct_effective_source_service import effective_source_for_item

    if not consented or not request_key:
        return DirectResult("pending", "sat_consent_required")

    async def validate(session):
        statement, state, context, _ = await load_statement(
            session, actor_id, import_id, "vigencia", locked=True, finance=True
        )
        await require_unfrozen(session, import_id)
        if context is None or state.source_version != expected_source_version:
            raise DirectConflict("stale_source_version")
        items = await load_items(session, import_id, locked=True)
        item = next((row for row in items if row.id == item_id), None)
        if item is None or item.source_version != expected_item_version:
            raise DirectConflict("stale_item_version")
        source = await effective_source_for_item(session, item)
        if source.movement_kind != "debit" or item.accounting_status == "recognized":
            raise DirectConflict("item_context_mismatch")
        cfdi = await session.get(CFDIReport, cfdi_id, populate_existing=True)
        # Do not expose or consult a document outside the receiver context.
        if (
            cfdi is None
            or str(cfdi.receptor_rfc or "").upper().strip() != context.receiver_rfc
        ):
            raise DirectConflict("cfdi_missing")
        identity = cfdi_identity(cfdi)
        if digest(identity) != expected_identity_hash:
            raise DirectConflict("fiscal_identity_changed")
        source_hash = digest(
            [
                str(context.id),
                context.version,
                source.source_json,
                str(source.imported_expense_id),
                item.source_version,
            ]
        )
        return state, cfdi, identity, source_hash

    lease_token = str(uuid4())
    async with factory.begin() as session:
        state, cfdi, identity, source_hash = await validate(session)
        prior = dict(state.progress_json or {})
        started = datetime.now(timezone.utc)
        if _lease_active(prior, started) or _lease_active(
            prior.get("selected_consultation") or {}, started
        ):
            return DirectResult("pending", "sat_consultation_in_progress")
        receipt = await current_status_receipt(session, cfdi)
        if receipt and receipt.observed_status in REUSABLE_STATUSES:
            return DirectResult(
                "observed",
                source_version=state.source_version,
                progress={
                    "status": receipt.observed_status,
                    "receipt_id": str(receipt.id),
                    "cached": True,
                },
            )
        prior["selected_consultation"] = {
            "running": True,
            "started_at": started.isoformat(),
            "token": lease_token,
            "request_key": request_key,
            "item_id": str(item_id),
            "cfdi_id": str(cfdi_id),
            "source_hash": source_hash,
            "identity_hash": expected_identity_hash,
        }
        state.progress_json = prior

    async def release_lease():
        from ..models import AmexStatementDirectState, AmexStatementImport

        async with factory.begin() as session:
            # Internal lease cleanup follows import -> state locking and does
            # not require business authority that may have just been revoked.
            await session.get(AmexStatementImport, import_id, with_for_update=True)
            state = await session.get(
                AmexStatementDirectState, import_id, with_for_update=True
            )
            if state is None:
                return
            progress = dict(state.progress_json or {})
            lease = dict(progress.get("selected_consultation") or {})
            if lease.get("token") == lease_token:
                lease["running"] = False
                progress["selected_consultation"] = lease
                state.progress_json = progress

    try:
        # No database transaction or lock survives the external request.
        service = status_service or SATCFDIStatusService()
        try:
            response = await asyncio.wait_for(
                service.consult_status(
                    uuid=identity["uuid"],
                    rfc_emisor=identity["rfc_emisor"],
                    rfc_receptor=identity["rfc_receptor"],
                    total=identity["total"],
                ),
                timeout=EXTERNAL_BUDGET_SECONDS,
            )
        except (asyncio.TimeoutError, OSError, ValueError):
            return DirectResult("pending", "sat_status_pending")
        if not isinstance(response, dict) or not _observed_request_matches(
            response, identity
        ):
            return DirectResult("pending", "invalid_observed_request")
        async with factory.begin() as session:
            state, cfdi, current_identity, current_source_hash = await validate(session)
            lease = (state.progress_json or {}).get("selected_consultation") or {}
            if (
                lease.get("token") != lease_token
                or not lease.get("running")
                or current_source_hash != source_hash
                or current_identity != identity
            ):
                return DirectResult("pending", "sat_progress_source_changed")
            receipt = AmexCFDIStatusReceipt(
                cfdi_id=cfdi.id,
                fiscal_uuid=identity["uuid"],
                issuer_rfc=identity["rfc_emisor"],
                receiver_rfc=identity["rfc_receptor"],
                amount=identity["total"],
                xml_hash=identity["xml_hash"],
                request_hash=digest(identity),
                observed_status=str(response.get("status") or "unknown").lower(),
                response_json=response,
                response_hash=digest(response),
                actor_id=actor_id,
                observed_at=datetime.now(timezone.utc),
            )
            session.add(receipt)
            await session.flush()
            return DirectResult(
                "observed",
                source_version=state.source_version,
                progress={
                    "status": receipt.observed_status,
                    "receipt_id": str(receipt.id),
                },
            )
    finally:
        propagating_error = sys.exc_info()[0] is not None
        try:
            await release_lease()
        except Exception:
            # Preserve the original denial/cancellation. An unreleased lease
            # still expires within the bounded external-request window.
            if not propagating_error:
                raise
