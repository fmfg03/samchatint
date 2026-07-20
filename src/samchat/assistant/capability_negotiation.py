from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Sequence

CAPABILITY_INQUIRY = "capability_inquiry"
SUPPORTED = "SUPPORTED"
SUPPORTED_WITH_INPUTS = "SUPPORTED_WITH_INPUTS"
SUPPORTED_WITH_CONFIRMATION = "SUPPORTED_WITH_CONFIRMATION"
PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
UNSUPPORTED = "UNSUPPORTED"


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.lower()).strip()


@dataclass(frozen=True)
class CapabilityGoal:
    interaction_mode: str
    capability_id: str
    input_artifact_type: str
    desired_outcome: str
    destination_system: str
    raw_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    public_name: str
    input_artifact_types: tuple[str, ...]
    desired_outcome: str
    destination_system: str
    input_aliases: tuple[str, ...]
    outcome_aliases: tuple[str, ...]
    destination_aliases: tuple[str, ...]
    required_actions: tuple[str, ...]
    available_steps: tuple[str, ...]
    required_fields: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    requires_confirmation: bool
    implementation_complete: bool
    write_flag: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityEvaluation:
    status: str
    capability_id: str
    public_name: str
    missing_fields: tuple[str, ...]
    unavailable_actions: tuple[str, ...]
    reason_codes: tuple[str, ...]
    registry_hash: str
    requires_confirmation: bool

    def to_trace(self) -> dict[str, Any]:
        return asdict(self)


CAPABILITY_REGISTRY: dict[str, CapabilitySpec] = {
    "expenses.receipt_to_payment_request": CapabilitySpec(
        capability_id="expenses.receipt_to_payment_request",
        public_name="Preparar un gasto y su solicitud de pago desde un comprobante",
        input_artifact_types=("expense_receipt", "cfdi_invoice"),
        desired_outcome="expense_and_payment_request",
        destination_system="samchat_expenses",
        input_aliases=("comprobante", "ticket", "recibo", "factura", "cfdi"),
        outcome_aliases=("cuenta de gastos", "solicitud de pago", "reembolso"),
        destination_aliases=(),
        required_actions=(
            "expenses.create_personal_receipt_workflow",
            "expenses.create_third_party_receipt_workflow",
        ),
        available_steps=(
            "extract_receipt_fields",
            "prepare_expense_preview",
            "prepare_payment_request_preview",
        ),
        required_fields=("uploaded_document", "payment_subject_type"),
        allowed_roles=(
            "empleado",
            "user",
            "coordinador",
            "finanzas",
            "admin",
            "super_admin",
            "superadmin",
        ),
        requires_confirmation=True,
        implementation_complete=True,
        write_flag="ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED",
    ),
    "accounting.policy_to_coi": CapabilitySpec(
        capability_id="accounting.policy_to_coi",
        public_name="Validar y publicar una póliza en COI",
        input_artifact_types=("accounting_policy",),
        desired_outcome="post_accounting_policy",
        destination_system="coi",
        input_aliases=("poliza", "asiento"),
        outcome_aliases=("subir", "subes", "cargar", "publicar", "registrar"),
        destination_aliases=("coi",),
        required_actions=("accounting.post_policy_to_coi",),
        available_steps=("extract_accounting_policy", "validate_accounting_policy"),
        required_fields=("uploaded_document",),
        allowed_roles=("finanzas", "admin", "super_admin", "superadmin"),
        requires_confirmation=True,
        implementation_complete=False,
    ),
}


def capability_registry_hash() -> str:
    payload = {
        key: CAPABILITY_REGISTRY[key].to_dict() for key in sorted(CAPABILITY_REGISTRY)
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def detect_capability_goal(text: str) -> Optional[CapabilityGoal]:
    normalized = _normalize(text)
    if not normalized or "document_intake_result json:" in normalized:
        return None

    conditional_upload = bool(
        re.search(
            r"\b(?:si|cuando)\s+te\s+(?:subo|mando|envio|comparto|cargo|paso)\b",
            normalized,
        )
    )
    explicit_capability = bool(
        re.search(
            r"\b(?:puedes|podrias|serias capaz|me puedes|me podrias)\b",
            normalized,
        )
    )
    if not conditional_upload and not explicit_capability:
        return None

    for capability_id in sorted(CAPABILITY_REGISTRY):
        spec = CAPABILITY_REGISTRY[capability_id]
        has_input = any(alias in normalized for alias in spec.input_aliases)
        has_outcome = any(alias in normalized for alias in spec.outcome_aliases)
        has_destination = not spec.destination_aliases or any(
            alias in normalized for alias in spec.destination_aliases
        )
        if has_input and has_outcome and has_destination:
            return CapabilityGoal(
                interaction_mode=CAPABILITY_INQUIRY,
                capability_id=spec.capability_id,
                input_artifact_type=spec.input_artifact_types[0],
                desired_outcome=spec.desired_outcome,
                destination_system=spec.destination_system,
                raw_text=text or "",
            )

    return CapabilityGoal(
        interaction_mode=CAPABILITY_INQUIRY,
        capability_id="unknown",
        input_artifact_type="unknown",
        desired_outcome="unknown",
        destination_system="unknown",
        raw_text=text or "",
    )


def capability_negotiation_enabled(employee_id: Any = None) -> bool:
    enabled = (os.getenv("ASSISTANT_CAPABILITY_NEGOTIATION_ENABLED") or "").strip()
    if enabled.lower() not in {"1", "true", "yes", "on"}:
        return False
    allowlist = {
        item.strip()
        for item in (
            os.getenv("ASSISTANT_CAPABILITY_NEGOTIATION_EMPLOYEE_IDS") or ""
        ).split(",")
        if item.strip()
    }
    return not allowlist or str(employee_id or "").strip() in allowlist


def receipt_workflow_writes_enabled(employee_id: Any = None) -> bool:
    enabled = (os.getenv("ASSISTANT_RECEIPT_WORKFLOW_WRITES_ENABLED") or "").strip()
    if enabled.lower() not in {"1", "true", "yes", "on"}:
        return False
    allowlist = {
        item.strip()
        for item in (os.getenv("ASSISTANT_RECEIPT_WORKFLOW_EMPLOYEE_IDS") or "").split(
            ","
        )
        if item.strip()
    }
    return not allowlist or str(employee_id or "").strip() in allowlist


def _flag_enabled(name: Optional[str], flags: Optional[Mapping[str, bool]]) -> bool:
    if not name:
        return True
    if flags is not None:
        return bool(flags.get(name, False))
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def evaluate_capability(
    goal: CapabilityGoal,
    *,
    supported_actions: Sequence[str],
    role: Optional[str],
    provided_fields: Optional[Mapping[str, Any]] = None,
    flags: Optional[Mapping[str, bool]] = None,
) -> CapabilityEvaluation:
    registry_hash = capability_registry_hash()
    spec = CAPABILITY_REGISTRY.get(goal.capability_id)
    if spec is None:
        return CapabilityEvaluation(
            status=UNSUPPORTED,
            capability_id=goal.capability_id,
            public_name="Capacidad no registrada",
            missing_fields=(),
            unavailable_actions=(),
            reason_codes=("capability_not_registered",),
            registry_hash=registry_hash,
            requires_confirmation=False,
        )

    normalized_role = (role or "user").strip().lower()
    if normalized_role not in spec.allowed_roles:
        return CapabilityEvaluation(
            status=UNSUPPORTED,
            capability_id=spec.capability_id,
            public_name=spec.public_name,
            missing_fields=(),
            unavailable_actions=(),
            reason_codes=("role_not_allowed",),
            registry_hash=registry_hash,
            requires_confirmation=spec.requires_confirmation,
        )

    installed = set(supported_actions)
    unavailable = tuple(
        action for action in spec.required_actions if action not in installed
    )
    reasons: list[str] = []
    if unavailable:
        reasons.append("terminal_actions_unavailable")
    if not spec.implementation_complete:
        reasons.append("workflow_incomplete")
    if not _flag_enabled(spec.write_flag, flags):
        reasons.append("workflow_writes_disabled")
    if reasons:
        return CapabilityEvaluation(
            status=PARTIALLY_SUPPORTED if spec.available_steps else UNSUPPORTED,
            capability_id=spec.capability_id,
            public_name=spec.public_name,
            missing_fields=(),
            unavailable_actions=unavailable,
            reason_codes=tuple(reasons),
            registry_hash=registry_hash,
            requires_confirmation=spec.requires_confirmation,
        )

    values = dict(provided_fields or {})
    missing = tuple(field for field in spec.required_fields if not values.get(field))
    status = (
        SUPPORTED_WITH_INPUTS
        if missing
        else (SUPPORTED_WITH_CONFIRMATION if spec.requires_confirmation else SUPPORTED)
    )
    return CapabilityEvaluation(
        status=status,
        capability_id=spec.capability_id,
        public_name=spec.public_name,
        missing_fields=missing,
        unavailable_actions=(),
        reason_codes=("capability_available",),
        registry_hash=registry_hash,
        requires_confirmation=spec.requires_confirmation,
    )


def render_capability_response(
    goal: CapabilityGoal,
    evaluation: CapabilityEvaluation,
) -> str:
    if goal.capability_id == "expenses.receipt_to_payment_request":
        if evaluation.status == PARTIALLY_SUPPORTED:
            return (
                "Puedo leer el comprobante, extraer sus datos y preparar el borrador. "
                "El registro automático de la cuenta de gastos y la solicitud de pago "
                "no está habilitado en este momento. Si lo subes, te mostraré lo que "
                "puedo preparar sin registrar cambios."
            )
        if evaluation.status == UNSUPPORTED:
            return (
                "No puedo completar ese flujo con tus permisos actuales. "
                "No consulté ni modifiqué información financiera."
            )
        return (
            "Sí. Sube el comprobante y extraeré importe, fecha, proveedor y concepto. "
            "Después confirmaré si corresponde a un gasto personal o a un pago a "
            "tercero, te mostraré el borrador y no registraré nada sin tu confirmación."
        )

    if goal.capability_id == "accounting.policy_to_coi":
        return (
            "Puedo ayudarte a leer y validar la póliza, pero no tengo una integración "
            "habilitada para publicarla directamente en COI. No ejecuté ninguna acción."
        )

    return (
        "No tengo una capacidad registrada para completar ese resultado. "
        "Puedo ayudarte a precisar el archivo de entrada y el sistema destino."
    )
