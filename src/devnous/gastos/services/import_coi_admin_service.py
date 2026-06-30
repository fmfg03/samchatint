"""
Helpers for COI workbook upload from admin UI.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class COIUploadSummary:
    mode: str
    polizas: int
    created: int
    updated: int
    lines: int
    cfdi_created: int
    cfdi_reused: int

    @classmethod
    def from_result(cls, result: dict) -> "COIUploadSummary":
        return cls(
            mode=str(result.get("mode") or "apply"),
            polizas=int(result.get("polizas") or 0),
            created=int(result.get("created") or 0),
            updated=int(result.get("updated") or 0),
            lines=int(result.get("lines") or 0),
            cfdi_created=int(result.get("cfdi_created") or 0),
            cfdi_reused=int(result.get("cfdi_reused") or 0),
        )
