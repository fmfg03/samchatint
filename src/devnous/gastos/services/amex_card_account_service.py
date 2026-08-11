"""Corporate AMEX card to liability account catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import AmexCardAccount, CuentaContable


class AmexCardAccountError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AmexCardAccountInput:
    card_label: str
    cardholder_key: str
    last4: str
    liability_cuenta_contable_id: UUID
    cardholder_name: Optional[str] = None
    active: bool = True
    notes: Optional[str] = None


def normalize_amex_last4(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) != 4:
        raise AmexCardAccountError(
            "invalid_last4", "Captura exactamente los últimos 4 dígitos de la tarjeta."
        )
    return digits


def normalize_cardholder_key(value: str) -> str:
    key = "".join(ch for ch in str(value or "").strip().upper() if ch.isalnum())
    if not key:
        raise AmexCardAccountError("missing_cardholder", "El responsable de la tarjeta es requerido.")
    return key[:20]


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


async def _load_active_cuenta(
    session: AsyncSession, cuenta_id: UUID
) -> Optional[CuentaContable]:
    result = await session.execute(
        select(CuentaContable).where(
            CuentaContable.id == cuenta_id, CuentaContable.activo.is_(True)
        )
    )
    return result.scalar_one_or_none()


async def list_amex_card_accounts(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[AmexCardAccount]:
    stmt = (
        select(AmexCardAccount)
        .options(selectinload(AmexCardAccount.liability_cuenta_contable))
        .order_by(AmexCardAccount.cardholder_key.asc(), AmexCardAccount.last4.asc())
    )
    if not include_inactive:
        stmt = stmt.where(AmexCardAccount.active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def find_amex_card_account_by_last4(
    session: AsyncSession, last4: Optional[str]
) -> Optional[AmexCardAccount]:
    if not last4:
        return None
    normalized_last4 = normalize_amex_last4(last4)
    result = await session.execute(
        select(AmexCardAccount)
        .options(selectinload(AmexCardAccount.liability_cuenta_contable))
        .where(
            AmexCardAccount.last4 == normalized_last4,
            AmexCardAccount.active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def upsert_amex_card_account(
    session: AsyncSession, payload: AmexCardAccountInput
) -> AmexCardAccount:
    last4 = normalize_amex_last4(payload.last4)
    cardholder_key = normalize_cardholder_key(payload.cardholder_key)
    card_label = normalize_optional_text(payload.card_label)
    if not card_label:
        raise AmexCardAccountError("missing_label", "La etiqueta de tarjeta es requerida.")

    cuenta = await _load_active_cuenta(session, payload.liability_cuenta_contable_id)
    if cuenta is None:
        raise AmexCardAccountError(
            "invalid_liability_account",
            "La cuenta pasivo AMEX no existe o no está activa.",
        )

    result = await session.execute(
        select(AmexCardAccount).where(AmexCardAccount.last4 == last4)
    )
    item = result.scalar_one_or_none()
    if item is None:
        item = AmexCardAccount(last4=last4)

    item.card_label = card_label
    item.cardholder_key = cardholder_key
    item.cardholder_name = normalize_optional_text(payload.cardholder_name)
    item.liability_cuenta_contable_id = payload.liability_cuenta_contable_id
    item.active = bool(payload.active)
    item.notes = normalize_optional_text(payload.notes)
    item.updated_at = datetime.utcnow()

    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def list_amex_liability_account_options(session: AsyncSession) -> list[CuentaContable]:
    result = await session.execute(
        select(CuentaContable)
        .where(
            CuentaContable.activo.is_(True),
            func.lower(CuentaContable.codigo).like("2120-002%"),
        )
        .order_by(CuentaContable.codigo.asc())
    )
    accounts = list(result.scalars().all())
    if accounts:
        return accounts
    result = await session.execute(
        select(CuentaContable)
        .where(CuentaContable.activo.is_(True))
        .order_by(CuentaContable.codigo.asc())
    )
    return list(result.scalars().all())
