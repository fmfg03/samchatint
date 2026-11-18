"""Edition and geographic authorization for CTT reports and roster corrections."""

import re
from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .registration_read_model import _rows

SUPERADMIN_ROLES = frozenset({"superadmin", "super_admin"})
CTT_ROSTER_READ_ROLES = SUPERADMIN_ROLES | {"admin", "coordinador", "finanzas"}
CTT_ROSTER_SLUG = re.compile(r"copa-telmex-(20[0-9]{2})")


@dataclass(frozen=True)
class CttEditionAccess:
    tournament_id: str
    edition_year: int
    roster_slug: str
    age_cutoff: date | None
    state_codes: frozenset[str]
    edit_state_codes: frozenset[str]

    @property
    def national(self) -> bool:
        return "" in self.state_codes


async def _ctt_report_schema_exists(session: AsyncSession) -> bool:
    rows = await _rows(
        session,
        "SELECT to_regclass('public.copa_telmex_edition_access') AS relation",
    )
    return bool(rows[0]["relation"])


async def list_managed_ctt_roster_slugs(session: AsyncSession) -> frozenset[str]:
    """Return enabled CTT editions independent of the viewer's assignments."""
    if not await _ctt_report_schema_exists(session):
        return frozenset()
    rows = await _rows(
        session,
        """SELECT e.roster_slug FROM public.copa_telmex_tournament_editions e
           JOIN public.tournaments t ON t.id = e.tournament_id AND t.active
           JOIN public.copa_telmex_report_settings settings
             ON settings.edition_id = e.id AND settings.enabled
           WHERE e.active
             AND e.roster_slug = 'copa-telmex-' || CAST(e.edition_year AS text)""",
    )
    return frozenset(row["roster_slug"] for row in rows)


async def list_ctt_report_editions(
    session: AsyncSession,
    *,
    employee_id: str,
    role: str,
    roster_slug: str | None = None,
) -> list[CttEditionAccess] | None:
    """Return only assigned editions, or None while the owner migration is absent."""
    if not await _ctt_report_schema_exists(session):
        return None
    national_admin = str(role or "").strip().lower() in SUPERADMIN_ROLES
    rows = await _rows(
        session,
        """SELECT CAST(e.tournament_id AS text) AS tournament_id,
                  e.edition_year, e.roster_slug, settings.age_cutoff,
                  a.state_code, a.can_edit
           FROM public.copa_telmex_tournament_editions e
           JOIN public.tournaments t ON t.id = e.tournament_id AND t.active
           JOIN public.copa_telmex_report_settings settings
             ON settings.edition_id = e.id AND settings.enabled
           LEFT JOIN public.copa_telmex_edition_access a
             ON a.edition_id = e.id AND a.empleado_id = CAST(:employee_id AS uuid)
           WHERE e.active
             AND e.roster_slug = 'copa-telmex-' || CAST(e.edition_year AS text)
             AND (CAST(:roster_slug AS text) IS NULL OR e.roster_slug = :roster_slug)
             AND (:national_admin OR a.empleado_id IS NOT NULL)
           ORDER BY e.edition_year DESC, a.state_code""",
        {
            "employee_id": employee_id,
            "roster_slug": roster_slug,
            "national_admin": national_admin,
        },
    )
    by_edition: dict[int, list[dict]] = {}
    for row in rows:
        by_edition.setdefault(row["edition_year"], []).append(row)
    editions = []
    for year, scope_rows in by_edition.items():
        first = scope_rows[0]
        editions.append(
            CttEditionAccess(
                tournament_id=first["tournament_id"],
                edition_year=year,
                roster_slug=first["roster_slug"],
                age_cutoff=first["age_cutoff"],
                state_codes=(
                    frozenset({""})
                    if national_admin
                    else frozenset(row["state_code"] for row in scope_rows)
                ),
                edit_state_codes=(
                    frozenset({""})
                    if national_admin
                    else frozenset(
                        row["state_code"] for row in scope_rows if row["can_edit"]
                    )
                ),
            )
        )
    return editions


def ctt_scope_allows_state(
    access: CttEditionAccess, state: str | None, *, write: bool = False
) -> bool:
    from .ctt_registration_report import resolve_ctt_geography

    permitted = access.edit_state_codes if write else access.state_codes
    if "" in permitted:
        return True
    state_code = resolve_ctt_geography(state, None).state_code
    return bool(state_code and state_code in permitted)


async def enforce_ctt_roster_access(
    session: AsyncSession,
    *,
    employee_id: str,
    role: str,
    roster_slug: str | None,
    state: str | None,
    write: bool = False,
    target_state: str | None = None,
) -> bool:
    if not CTT_ROSTER_SLUG.fullmatch(roster_slug or ""):
        return False
    editions = await list_ctt_report_editions(
        session, employee_id=employee_id, role=role, roster_slug=roster_slug
    )
    if editions is None or (
        not editions and roster_slug not in await list_managed_ctt_roster_slugs(session)
    ):
        return False
    if not editions or not ctt_scope_allows_state(editions[0], state, write=write):
        raise HTTPException(403, "El expediente está fuera de tu alcance de CTT.")
    if (
        write
        and target_state is not None
        and not ctt_scope_allows_state(editions[0], target_state, write=True)
    ):
        raise HTTPException(
            403, "La entidad de destino está fuera de tu alcance de CTT."
        )
    return True
