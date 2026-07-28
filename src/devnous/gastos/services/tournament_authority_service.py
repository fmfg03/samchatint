"""Authority boundary for local gastos projects created by the assistant."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from samchat.assistant.tournament_application_contract import (
    APPLIED,
    TournamentApplicationContract,
    verify_tournament_application_contract,
)

logger = logging.getLogger(__name__)


class TournamentAuthorityUnavailableError(RuntimeError):
    """The immutable authority record could not be inspected or verified."""


@dataclass(frozen=True)
class GovernedGastosProjectError(RuntimeError):
    case_id: str
    case_version: int
    application_hash: str

    def __str__(self) -> str:
        return "Governed gastos project must be changed through its assistant case"


async def get_applied_gastos_project_provenance(
    session: AsyncSession,
    tournament_id: UUID | str,
) -> Optional[dict[str, Any]]:
    """Return verified receipt provenance for an RQF-applied local project."""

    try:
        result = await session.execute(
            text("""
                SELECT case_id, version_number, answer_contract
                FROM analyst_case_versions
                WHERE answer_contract -> 'tournament_application' ->> 'state' = 'applied'
                  AND answer_contract -> 'tournament_application'
                        -> 'application_receipt' -> 'payload'
                        ->> 'target_tournament_id' = :target_tournament_id
                ORDER BY version_number DESC
                LIMIT 1
                """),
            {"target_tournament_id": str(tournament_id)},
        )
        row = result.mappings().first()
    except Exception as exc:
        logger.error("Unable to inspect governed gastos project target: %s", exc)
        raise TournamentAuthorityUnavailableError(
            "Unable to verify tournament authority boundary"
        ) from exc
    if row is None:
        return None

    try:
        answer_contract = row.get("answer_contract") or {}
        if isinstance(answer_contract, str):
            answer_contract = json.loads(answer_contract)
        stored_contract = answer_contract.get("tournament_application")
        contract = TournamentApplicationContract.from_mapping(stored_contract)
        verify_tournament_application_contract(contract)
        receipt = contract.application_receipt
        target_id = str(
            (receipt.payload if receipt else {}).get("target_tournament_id") or ""
        )
        if (
            contract.state != APPLIED
            or target_id != str(tournament_id)
            or receipt is None
        ):
            raise ValueError(
                "Applied tournament receipt does not bind requested target"
            )
    except Exception as exc:
        logger.error("Invalid governed gastos project receipt: %s", exc)
        raise TournamentAuthorityUnavailableError(
            "Unable to verify tournament authority receipt"
        ) from exc

    return {
        "case_id": str(row.get("case_id") or ""),
        "case_version": int(row.get("version_number") or 0),
        "application_hash": receipt.receipt_hash,
    }


async def require_ungoverned_gastos_project(
    session: AsyncSession,
    tournament_id: UUID | str,
) -> None:
    provenance = await get_applied_gastos_project_provenance(session, tournament_id)
    if provenance is not None:
        raise GovernedGastosProjectError(**provenance)
