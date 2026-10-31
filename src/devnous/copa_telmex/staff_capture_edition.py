"""Edition binding from protected state; never supplied by an HTTP caller."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TrustedEditionBinding:
    tenant_id: str
    edition_id: UUID
    tournament_id: UUID
    edition_year: int
    roster_slug: str
