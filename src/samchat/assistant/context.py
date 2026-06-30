from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(slots=True)
class AssistantContext:
    """Canonical shared context passed across assistant-driven workflows."""

    sport: Optional[str] = None
    tournament_id: Optional[str] = None
    tournament_name: Optional[str] = None
    edition: Optional[str] = None
    fase_torneo: Optional[str] = None
    concepto: Optional[str] = None
    departamento: Optional[str] = None
    responsible_user_id: Optional[str] = None
    branch: Optional[str] = None
    category: Optional[str] = None
    team_id: Optional[str] = None
    expense_account_id: Optional[str] = None
    document_id: Optional[str] = None
    need_id: Optional[str] = None
    expense_id: Optional[str] = None
    receipt_id: Optional[str] = None
    accounting_entry_id: Optional[str] = None
    referencia_base: Optional[str] = None
    referencia_operaciones: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> "AssistantContext":
        payload = payload or {}
        normalized = {
            "sport": payload.get("sport") or payload.get("deporte"),
            "tournament_id": payload.get("tournament_id"),
            "tournament_name": payload.get("tournament_name")
            or payload.get("torneo")
            or payload.get("proyecto"),
            "edition": payload.get("edition") or payload.get("edicion"),
            "fase_torneo": payload.get("fase_torneo") or payload.get("phase"),
            "concepto": payload.get("concepto") or payload.get("concept"),
            "departamento": payload.get("departamento") or payload.get("department"),
            "responsible_user_id": payload.get("responsible_user_id")
            or payload.get("empleado_id"),
            "branch": payload.get("branch") or payload.get("rama"),
            "category": payload.get("category") or payload.get("categoria"),
            "team_id": payload.get("team_id"),
            "expense_account_id": payload.get("expense_account_id")
            or payload.get("cuenta_id"),
            "document_id": payload.get("document_id") or payload.get("documento_id"),
            "need_id": payload.get("need_id"),
            "expense_id": payload.get("expense_id"),
            "receipt_id": payload.get("receipt_id"),
            "accounting_entry_id": payload.get("accounting_entry_id"),
            "referencia_base": payload.get("referencia_base"),
            "referencia_operaciones": payload.get("referencia_operaciones")
            or payload.get("ops_reference"),
        }
        return cls(**normalized)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def merge(self, **updates: Any) -> "AssistantContext":
        current = self.to_dict()
        for key, value in updates.items():
            if key in current and value is not None:
                current[key] = value
        return AssistantContext(**current)
