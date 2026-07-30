"""Persistent authorization profile board.

Profiles are the UI-friendly layer over the authorization strategy matrix: a
person-like profile (Perfil Odilon, Perfil Luis Angel, etc.) contains editable
rule switches copied from the canonical resolver. This stage is advisory and
keeps enforcement out of the live workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .authorization_strategy_service import (
    APPROVER_ROLES,
    AUTHORIZATION_STRATEGY_RULES,
    AuthorizationStrategyRule,
)


@dataclass(frozen=True)
class AuthorizationProfile:
    id: UUID
    profile_key: str
    name: str
    role_key: str
    employee_matcher: str
    active: bool
    rules: tuple[dict[str, Any], ...]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


PROFILE_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    ("perfil_odilon", "Perfil Odilon", "director_operaciones", "odilon trujillo"),
    ("perfil_luis_angel", "Perfil Luis Angel", "dayf", "luis angel orozco"),
    ("perfil_olof", "Perfil Olof", "dgoat", "olof"),
    ("perfil_benjamin", "Perfil Benjamin", "gerente_ayf", "benjamin jimenez"),
    ("perfil_dg", "Perfil DG", "dg", "federico gonzalez"),
)


def _profile_rule_from_strategy(
    rule: AuthorizationStrategyRule,
    *,
    role_key: str,
) -> Optional[dict[str, Any]]:
    is_first = rule.first_approver_role == role_key
    is_second = role_key in rule.second_approver_roles
    if not is_first and not is_second:
        return None
    return {
        "rule_key": rule.key,
        "enabled": True,
        "area_key": rule.area_key,
        "erogation_key": rule.erogation_key,
        "erogation_label": rule.erogation_label,
        "amount_mode": rule.amount_mode,
        "amount_value": str(rule.amount_value) if rule.amount_value is not None else "",
        "can_first_approve": is_first,
        "can_second_approve": is_second,
        "requires_pending_advance_review": rule.requires_pending_advance_review,
        "requires_no_invoice": rule.requires_no_invoice,
        "requires_unbudgeted": rule.requires_unbudgeted,
        "requires_budget_excess": rule.requires_budget_excess,
        "requires_urgent": rule.requires_urgent,
        "conditions": list(rule.conditions),
    }


def default_profile_rules(role_key: str) -> tuple[dict[str, Any], ...]:
    rows = [
        row
        for rule in AUTHORIZATION_STRATEGY_RULES
        if (row := _profile_rule_from_strategy(rule, role_key=role_key)) is not None
    ]
    return tuple(rows)


async def ensure_authorization_profiles_schema(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS authorization_profiles (
                id UUID PRIMARY KEY,
                profile_key VARCHAR(120) NOT NULL UNIQUE,
                name VARCHAR(160) NOT NULL,
                role_key VARCHAR(80) NOT NULL,
                employee_matcher VARCHAR(200) NULL,
                rules JSONB NOT NULL DEFAULT '[]'::jsonb,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by_empleado_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_authorization_profiles_profile_key ON authorization_profiles(profile_key)"
        )
    )
    await session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_authorization_profiles_active ON authorization_profiles(active)"
        )
    )


async def seed_default_authorization_profiles(
    session: AsyncSession,
    *,
    actor_id: Any = None,
) -> None:
    await ensure_authorization_profiles_schema(session)
    for profile_key, name, role_key, matcher in PROFILE_DEFINITIONS:
        rules = list(default_profile_rules(role_key))
        await session.execute(
            text(
                """
                INSERT INTO authorization_profiles (
                    id, profile_key, name, role_key, employee_matcher, rules,
                    active, created_by_empleado_id, created_at, updated_at
                )
                VALUES (
                    :id, :profile_key, :name, :role_key, :matcher,
                    CAST(:rules_json AS jsonb), TRUE, :actor_id, NOW(), NOW()
                )
                ON CONFLICT (profile_key) DO NOTHING
                """
            ),
            {
                "id": uuid4(),
                "profile_key": profile_key,
                "name": name,
                "role_key": role_key,
                "matcher": matcher,
                "rules_json": __import__("json").dumps(rules),
                "actor_id": actor_id,
            },
        )


def _row_to_profile(row: Any) -> AuthorizationProfile:
    raw_rules = row.rules if hasattr(row, "rules") else row[5]
    if raw_rules is None:
        rules = ()
    elif isinstance(raw_rules, str):
        rules = tuple(__import__("json").loads(raw_rules))
    else:
        rules = tuple(raw_rules)
    return AuthorizationProfile(
        id=row.id if hasattr(row, "id") else row[0],
        profile_key=row.profile_key if hasattr(row, "profile_key") else row[1],
        name=row.name if hasattr(row, "name") else row[2],
        role_key=row.role_key if hasattr(row, "role_key") else row[3],
        employee_matcher=(
            row.employee_matcher if hasattr(row, "employee_matcher") else row[4]
        ),
        active=bool(row.active if hasattr(row, "active") else row[6]),
        rules=rules,
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
    )


async def list_authorization_profiles(
    session: AsyncSession,
) -> list[AuthorizationProfile]:
    await seed_default_authorization_profiles(session)
    result = await session.execute(
        text(
            """
            SELECT id, profile_key, name, role_key, employee_matcher, rules, active,
                   created_at, updated_at
            FROM authorization_profiles
            ORDER BY name
            """
        )
    )
    return [_row_to_profile(row) for row in result.fetchall()]


async def get_authorization_profile(
    session: AsyncSession,
    profile_id: UUID | str,
) -> Optional[AuthorizationProfile]:
    await seed_default_authorization_profiles(session)
    result = await session.execute(
        text(
            """
            SELECT id, profile_key, name, role_key, employee_matcher, rules, active,
                   created_at, updated_at
            FROM authorization_profiles
            WHERE id = :profile_id
            """
        ),
        {"profile_id": str(profile_id)},
    )
    row = result.fetchone()
    return _row_to_profile(row) if row else None


def summarize_profile_rules(rules: Iterable[dict[str, Any]]) -> dict[str, int]:
    enabled = 0
    first = 0
    second = 0
    exceptions = 0
    total = 0
    for rule in rules:
        total += 1
        if rule.get("enabled"):
            enabled += 1
        if rule.get("can_first_approve"):
            first += 1
        if rule.get("can_second_approve"):
            second += 1
        if any(
            rule.get(flag)
            for flag in (
                "requires_no_invoice",
                "requires_unbudgeted",
                "requires_budget_excess",
                "requires_urgent",
                "requires_pending_advance_review",
            )
        ):
            exceptions += 1
    return {
        "total": total,
        "enabled": enabled,
        "first": first,
        "second": second,
        "exceptions": exceptions,
    }


async def copy_authorization_profile(
    session: AsyncSession,
    *,
    source_profile_id: UUID | str,
    new_name: str,
    actor_id: Any = None,
) -> AuthorizationProfile:
    source = await get_authorization_profile(session, source_profile_id)
    if source is None:
        raise ValueError("Perfil origen no encontrado")
    base_key = "perfil_" + "_".join(new_name.lower().strip().split())
    key = base_key[:100] or f"perfil_{uuid4().hex[:8]}"
    suffix = 2
    while True:
        existing = await session.execute(
            text("SELECT 1 FROM authorization_profiles WHERE profile_key = :key"),
            {"key": key},
        )
        if existing.fetchone() is None:
            break
        key = f"{base_key[:92]}_{suffix}"
        suffix += 1
    new_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO authorization_profiles (
                id, profile_key, name, role_key, employee_matcher, rules,
                active, created_by_empleado_id, created_at, updated_at
            ) VALUES (
                :id, :profile_key, :name, :role_key, :matcher,
                CAST(:rules_json AS jsonb), TRUE, :actor_id, NOW(), NOW()
            )
            """
        ),
        {
            "id": new_id,
            "profile_key": key,
            "name": new_name.strip() or f"Copia de {source.name}",
            "role_key": source.role_key,
            "matcher": source.employee_matcher,
            "rules_json": __import__("json").dumps(list(source.rules)),
            "actor_id": actor_id,
        },
    )
    await session.commit()
    profile = await get_authorization_profile(session, new_id)
    if profile is None:
        raise ValueError("No se pudo crear la copia")
    return profile


async def update_authorization_profile_rules(
    session: AsyncSession,
    *,
    profile_id: UUID | str,
    enabled_rule_keys: set[str],
    first_rule_keys: set[str],
    second_rule_keys: set[str],
) -> None:
    profile = await get_authorization_profile(session, profile_id)
    if profile is None:
        raise ValueError("Perfil no encontrado")
    updated: list[dict[str, Any]] = []
    for rule in profile.rules:
        next_rule = dict(rule)
        rule_key = str(next_rule.get("rule_key") or "")
        next_rule["enabled"] = rule_key in enabled_rule_keys
        next_rule["can_first_approve"] = rule_key in first_rule_keys
        next_rule["can_second_approve"] = rule_key in second_rule_keys
        updated.append(next_rule)
    await session.execute(
        text(
            """
            UPDATE authorization_profiles
            SET rules = CAST(:rules_json AS jsonb), updated_at = NOW()
            WHERE id = :profile_id
            """
        ),
        {
            "profile_id": str(profile_id),
            "rules_json": __import__("json").dumps(updated),
        },
    )
    await session.commit()
