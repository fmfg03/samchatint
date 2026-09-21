"""Receipt store contracts; no runtime schema creation is permitted here."""

from __future__ import annotations

from typing import Dict, Optional, Protocol

from .contracts import ActionReceipt


class ActionReceiptStore(Protocol):
    def append(self, receipt: ActionReceipt) -> None:
        """Persist one receipt through an owner-run schema."""

    def find_idempotent(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action_id: str,
        action_version: str,
        idempotency_key: str,
    ) -> Optional[ActionReceipt]:
        """Return the prior receipt for an exact mutation identity."""


class InMemoryActionReceiptStore:
    """Test-only store mirroring the persistence uniqueness contract."""

    def __init__(self) -> None:
        self.receipts: list[ActionReceipt] = []
        self._idempotent: Dict[tuple[str, str, str, str, str], ActionReceipt] = {}

    def append(self, receipt: ActionReceipt) -> None:
        self.receipts.append(receipt)
        if receipt.tenant_id and receipt.actor_id and receipt.idempotency_key:
            self._idempotent[
                (
                    receipt.tenant_id,
                    receipt.actor_id,
                    receipt.action_id,
                    receipt.action_version,
                    receipt.idempotency_key,
                )
            ] = receipt

    def find_idempotent(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action_id: str,
        action_version: str,
        idempotency_key: str,
    ) -> Optional[ActionReceipt]:
        return self._idempotent.get(
            (tenant_id, actor_id, action_id, action_version, idempotency_key)
        )
