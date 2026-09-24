"""Isolated FastAPI app for browser-level UX tests.

This module is test-only. It deliberately does not import or modify the production
application object. The session login route exists only inside this isolated app.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from devnous.gastos.routes import (
    admin_routes,
    client_executive_routes,
    dependencies,
    user_routes,
)
from devnous.gastos.routes import admin_budget_ui
from devnous.gastos.services import cuenta_contable_suggester as cuenta_suggester_module
from devnous.gastos.services import (
    documento_payment_service,
    documento_workflow_service,
)
from devnous.gastos.services import (
    expense_accounting_cleanup_service,
    payment_run_service,
)
from samchat.ar import admin_ui as ar_admin_ui
from samchat.ar import collection_matches

EMPLOYEE_ID = UUID("10000000-0000-0000-0000-000000000354")


class _EmptySession:
    async def execute(self, *_args, **_kwargs):
        return []


class _FakeScalarRows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def unique(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _FakeScalarRows(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _RowsSession:
    def __init__(self, rows):
        self._rows = list(rows)

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._rows)


class _ArMappingRows:
    """Small mapping-result adapter for the canonical AR writers."""

    def __init__(self, rows=()):
        self._rows = list(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _ArResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return _ArMappingRows(self._rows)


class _ArMutationSession:
    """In-memory transaction boundary used only by the browser AR fixture."""

    def __init__(self, fixture):
        self.fixture = fixture

    async def execute(self, statement, *_args, **_kwargs):
        sql = str(statement)
        if "SUM(accepted_amount)" in sql:
            total = (
                self.fixture.amount
                if self.fixture.match
                and self.fixture.match["status"] == collection_matches.ACCEPTED_STATUS
                else 0.0
            )
            return _ArResult([{"total": total}])
        if "FROM ar_bank_account_mappings map" in sql:
            return _ArResult(
                [
                    {
                        "id": self.fixture.bank_account_id,
                        "codigo": "1020-001",
                    }
                ]
            )
        return _ArResult()

    async def commit(self):
        self.fixture.commits += 1

    async def rollback(self):
        self.fixture.rollbacks += 1


class _ArFixture:
    budget_version_id = "91000000-0000-0000-0000-000000000001"
    budget_line_id = "91000000-0000-0000-0000-000000000002"
    cfdi_report_id = "91000000-0000-0000-0000-000000000003"
    bank_movement_id = "91000000-0000-0000-0000-000000000004"
    bank_account_id = "91000000-0000-0000-0000-000000000005"
    match_id = "91000000-0000-0000-0000-000000000006"
    ar_item_id = "cfdi:UX-CXC-001"
    amount = 12500.0

    def reset(self):
        self.match = None
        self.audit = []
        self.commits = 0
        self.rollbacks = 0
        self.session = _ArMutationSession(self)

    def ar_item(self):
        collected = (
            self.amount
            if self.match and self.match["status"] == collection_matches.ACCEPTED_STATUS
            else 0.0
        )
        return {
            "ar_item_id": self.ar_item_id,
            "budget_version_id": self.budget_version_id,
            "budget_line_id": self.budget_line_id,
            "cfdi_report_id": self.cfdi_report_id,
            "tournament_id": "torneo-browser-cxc",
            "tournament_name": "Copa Browser UX",
            "tournament_code": "CBUX",
            "phase": "Regional",
            "concept_name": "Patrocinio regional",
            "payer_name": "Patrocinador Browser UX",
            "payer_rfc": "PBU010101AR1",
            "cfdi_uuid": "UX-CXC-001",
            "issued_date": "2026-09-15",
            "due_date": "2026-10-15",
            "expected_income_amount": self.amount,
            "issued_amount": self.amount,
            "linked_income_amount": self.amount,
            "collected_amount": collected,
            "balance_amount": self.amount - collected,
            "collection_status": (
                "matched_collected" if collected else "collection_unknown"
            ),
            "operational_status": "Cobrado" if collected else "Cobranza desconocida",
            "source": "issued_linked",
        }

    def read_model(self):
        item = self.ar_item()
        return {
            "summary": {
                "expected_income_total": self.amount,
                "linked_income_total": self.amount,
                "issued_unlinked_total": 0.0,
                "invoiced_total": self.amount,
                "collected_total": item["collected_amount"],
                "balance_total": item["balance_amount"],
                "overdue_total": 0.0,
                "collection_gap_count": 0 if item["collected_amount"] else 1,
                "matching_gap_count": 0 if item["collected_amount"] else 1,
            },
            "expected_income": [item],
            "issued_linked": [item],
            "issued_unlinked": [],
            "collection_gaps": [] if item["collected_amount"] else [item],
            "matching_gaps": [] if item["collected_amount"] else [item],
            "operational_rows": [item],
        }

    def matching_workbench(self):
        candidate = {
            "bank_movement_id": self.bank_movement_id,
            "bank_amount": self.amount,
            "bank_date": "2026-09-21",
            "bank_name": "Patrocinador Browser UX",
            "bank_rfc": "PBU010101AR1",
            "signals": "RFC y monto coinciden",
        }
        return {
            "budget_version_id": self.budget_version_id,
            "summary": {
                "accepted_match_count": int(bool(self.match and self.match["status"] == collection_matches.ACCEPTED_STATUS)),
                "candidate_match_count": int(not self.match or self.match["status"] != collection_matches.ACCEPTED_STATUS),
                "manual_match_required_count": 0,
                "collection_unknown_count": int(not self.match or self.match["status"] != collection_matches.ACCEPTED_STATUS),
                "payer_gap_count": 0,
                "unmatched_bank_inflow_count": 0,
            },
            "items": [] if self.match and self.match["status"] == collection_matches.ACCEPTED_STATUS else [{
                **self.ar_item(),
                "status": "candidate_match",
                "amount": self.amount,
                "candidate_evidence": [candidate],
            }],
            "accepted_matches": [self.match] if self.match and self.match["status"] == collection_matches.ACCEPTED_STATUS else [],
            "unmatched_bank_inflows": [],
        }


AR_FIXTURE = _ArFixture()
AR_FIXTURE.reset()


async def _ar_no_schema(*_args, **_kwargs):
    return None


async def _ar_load_bank(_session, _bank_movement_id):
    return {
        "id": AR_FIXTURE.bank_movement_id,
        "signo": "+",
        "importe": AR_FIXTURE.amount,
        "fecha": "2026-09-21",
        "cuenta_bancaria": "012345678901234567",
        "rfc_ordenante": "PBU010101AR1",
        "nombre_ordenante": "Patrocinador Browser UX",
        "descripcion": "Cobro patrocinio regional",
        "concepto_banco": "UX-CXC-001",
    }


async def _ar_find_active(_session, **_kwargs):
    if AR_FIXTURE.match and AR_FIXTURE.match["status"] == collection_matches.ACCEPTED_STATUS:
        return dict(AR_FIXTURE.match)
    return {}


async def _ar_bank_account(*_args, **_kwargs):
    return {"id": AR_FIXTURE.bank_account_id, "codigo": "1020-001", "nombre": "Banco Browser UX"}


async def _ar_cxc_account(*_args, **_kwargs):
    return {"id": AR_FIXTURE.budget_line_id, "codigo": "1050-001"}


async def _ar_insert(_session, *, ar_item, bank_movement, **_kwargs):
    AR_FIXTURE.match = {
        "id": AR_FIXTURE.match_id,
        "ar_item_id": ar_item["ar_item_id"],
        "bank_movement_id": bank_movement["id"],
        "accepted_amount": AR_FIXTURE.amount,
        "collection_date": bank_movement["fecha"],
        "payer_name": ar_item["payer_name"],
        "cfdi_report_id": ar_item["cfdi_report_id"],
        "status": collection_matches.ACCEPTED_STATUS,
    }
    return AR_FIXTURE.match


async def _ar_create_poliza(*_args, **_kwargs):
    return "91000000-0000-0000-0000-000000000007"


async def _ar_load_match(_session, match_id):
    return dict(AR_FIXTURE.match) if AR_FIXTURE.match and match_id == AR_FIXTURE.match_id else {}


async def _ar_update_reversed(_session, *, match_id, reversal_reason, **_kwargs):
    if not AR_FIXTURE.match or match_id != AR_FIXTURE.match_id:
        return {}
    AR_FIXTURE.match["status"] = collection_matches.REVERSED_STATUS
    AR_FIXTURE.match["reversal_reason"] = reversal_reason
    return dict(AR_FIXTURE.match)


async def _ar_audit(*_args, **kwargs):
    AR_FIXTURE.audit.append(kwargs)


async def _db_session():
    yield _EmptySession()


async def _load_employee(_session, employee_id):
    if employee_id != EMPLOYEE_ID:
        return None
    return SimpleNamespace(
        id=EMPLOYEE_ID,
        nombre="Browser UX Direction",
        correo="browser-ux@example.invalid",
        rol="coordinador",
        activo=True,
        departamento="direccion",
        permissions=set(),
    )


async def _visible_tools(_session, _employee):
    return {"direccion.tableros_ejecutivos"}


async def _can_access_path(_session, _employee, _path, _method="GET"):
    return True


async def _direction_decision(_session, _employee, _tool, _action_key):
    return None


async def _direction_portfolios(_session, _employee_id, *, is_superadmin):
    del is_superadmin
    if str(_employee_id) != str(EMPLOYEE_ID):
        return []
    return ["portfolio-browser-ux"]


async def _dashboard(*_args, **kwargs):
    edition_year = int(kwargs.get("edition_year") or 2026)
    card = {
        "tournament_id": "browser-ux-tournament",
        "tournament_name": "Torneo Browser UX",
        "as_of": "2026-09-21",
        "budget": 1_250_000,
        "actual": 630_000,
        "committed": 780_000,
        "paid": 510_000,
        "projected": 1_120_000,
        "requested": 920_000,
        "pending_to_pay": 270_000,
        "budget_source_status": "available",
        "alerts": [
            {
                "severity": "high",
                "title": "Partida con presión presupuestal",
            }
        ],
        "budget_breakdowns": {
            "by_concept": [
                {
                    "label": "Hospedaje y alimentación",
                    "budget_total": 420_000,
                    "actual_total": 265_000,
                    "committed_total": 310_000,
                },
                {
                    "label": "Transportación terrestre y aérea",
                    "budget_total": 330_000,
                    "actual_total": 190_000,
                    "committed_total": 220_000,
                },
            ],
            "by_account": [
                {
                    "label": "5300-001-001 · Operación del torneo",
                    "budget_total": 750_000,
                },
                {
                    "label": "5300-010-002 · Servicios logísticos",
                    "budget_total": 500_000,
                },
            ],
        },
        "dossier": {
            "source_status": "available",
            "source_bridge": "authorized_uuid",
            "entities": [
                {
                    "entity_name": "Asociación Deportiva Browser UX",
                    "readiness": {"status": "usable"},
                    "operations": {
                        "ps_owner": "Coordinación Plataforma Sports",
                        "summary": {"teams_count": 3, "players_count": 54},
                        "entity_contacts": [
                            {
                                "name": "Responsable de prueba",
                                "phone": "555-0100",
                                "email": "responsable@example.invalid",
                            }
                        ],
                        "real_teams_by_category_gender": [
                            {
                                "category": "Juvenil",
                                "gender_or_branch": "Varonil",
                                "teams_count": 2,
                                "players_count": 36,
                                "team_names": [
                                    "Equipo con nombre representativo A",
                                    "Equipo con nombre representativo B",
                                ],
                            }
                        ],
                        "players_by_category_age_gender": [
                            {
                                "category": "Juvenil",
                                "gender_or_branch": "Varonil",
                                "age": "15-16",
                                "players_count": 36,
                            }
                        ],
                        "pending_fields": [
                            "Confirmar documentación de dos participantes."
                        ],
                    },
                    "finance": {
                        "pending_fields": [
                            "Conciliar anticipo pendiente contra comprobación."
                        ]
                    },
                }
            ],
            "national_phase": {
                "status": "with_data",
                "matches": [
                    {
                        "phase": "Semifinal",
                        "match_date": "2026-10-12",
                        "field_number": "Cancha principal",
                        "status": "programado",
                    }
                ],
            },
            "marketing": {
                "status": "available",
                "media": {
                    "photos_count": 128,
                    "videos_count": 9,
                    "streams_count": 2,
                },
            },
        },
    }
    return {
        "edition_year": edition_year,
        "scope": "portfolio",
        "cards": [card],
        "data_boundary": {
            "operations": "browser_fixture",
            "budget": "browser_fixture",
            "entity_finance": "browser_fixture",
            "writes": False,
        },
        "unavailable_metrics": ["cashflow", "accounts_receivable", "payments"],
    }


async def _panel_visible_tools(_session, employee):
    return set(getattr(employee, "visible_tool_keys", set()) or set())


async def _panel_can_review_pending_approvals(_session, employee):
    return str(getattr(employee, "correo", "") or "").startswith("approver-browser-ux@")


def _panel_is_budget_control_user(employee):
    return str(getattr(employee, "correo", "") or "").startswith(
        "budget-control-browser-ux@"
    )


def _journey_tournament():
    return SimpleNamespace(
        id=UUID("20000000-0000-0000-0000-000000000001"),
        name="Copa Browser UX",
        etapas=["Regional"],
        categorias=["Juvenil"],
        categories=["Juvenil"],
    )


def _journey_provider():
    return SimpleNamespace(
        id=UUID("30000000-0000-0000-0000-000000000001"),
        nombre="Proveedor Browser UX",
        rfc="PBU010101AAA",
        tipo="proveedor",
        banco="Banco Browser",
        cuenta_bancaria="1234567890",
        cuenta_clabe="012345678901234567",
        activo=True,
    )


def _journey_pending_document():
    provider = _journey_provider()
    tournament = _journey_tournament()
    requester = SimpleNamespace(
        id=UUID("10000000-0000-0000-0000-000000000499"),
        nombre="Solicitante Browser UX",
        aprobador=None,
    )
    return SimpleNamespace(
        id=UUID("40000000-0000-0000-0000-000000000001"),
        numero_referencia="S-UX-0001",
        referencia_operaciones="4101",
        referencia_pago="",
        concepto_pago="Hospedaje para torneo regional",
        notas="Fixture de usabilidad",
        tipo="SOLICITUD",
        estado="enviado",
        currency="MXN",
        monto_solicitado=Decimal("29696.00"),
        monto_total=Decimal("29696.00"),
        creado_en=datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc),
        enviado_en=datetime(2026, 9, 21, 14, 5, tzinfo=timezone.utc),
        aprobado_en=None,
        pagado_en=None,
        empleado=requester,
        empleado_id=requester.id,
        beneficiario_empleado=None,
        beneficiario_empleado_id=None,
        beneficiario_proveedor_cliente=None,
        proveedor_cliente=provider,
        proveedor_cliente_id=provider.id,
        cuenta_gastos=None,
        cuenta_gastos_id=None,
        torneo=tournament,
        torneo_id=tournament.id,
        proyecto_otro="",
        fase="Regional",
    )


async def _journey_active_tournaments(_session, _employee, **_kwargs):
    return [_journey_tournament()]


async def _journey_budget_concepts(_session, tournaments):
    return {str(item.id): [] for item in tournaments}


async def _journey_adjuntos(_session, documento_ids):
    return {doc_id: [] for doc_id in documento_ids}


async def _journey_approver_names(_session, documents):
    return {
        getattr(document, "id", None): "Aprobador Browser UX"
        for document in documents
        if getattr(document, "id", None) is not None
    }


def _journey_budget_control_document():
    document = _journey_pending_document()
    document.estado = "control_presupuestal"
    document.budget_concept_id = None
    return document


async def _journey_concepts_for_document(_session, _document):
    return [
        {
            "id": str(UUID("50000000-0000-0000-0000-000000000001")),
            "label": "Hospedaje y alimentación",
            "cuenta_contable_codigo": "5300-001-001",
            "cuenta_contable_nombre": "Operación del torneo",
            "global": False,
            "applicable_keys": ["Regional"],
            "scope_mode": "phase",
        }
    ]


def _journey_payment_row(*, in_process: bool):
    return {
        "id": str(
            UUID(
                "60000000-0000-0000-0000-000000000002"
                if in_process
                else "60000000-0000-0000-0000-000000000001"
            )
        ),
        "entity_type": "documento",
        "numero_referencia": "S-PAY-0002" if in_process else "S-PAY-0001",
        "referencia_operaciones": "4202" if in_process else "4201",
        "solicitante_nombre": "Solicitante Browser UX",
        "beneficiario_nombre": "Proveedor Browser UX",
        "concepto_pago": (
            "Servicios logísticos con comprobante pendiente"
            if in_process
            else "Hospedaje aprobado para corte"
        ),
        "fecha_pago": datetime(2026, 9, 30).date(),
        "monto": Decimal("18450.00" if in_process else "29696.00"),
        "currency": "MXN",
        "status": "en proceso de pago" if in_process else "programada",
        "can_close": not in_process,
        "can_edit_fecha_pago": not in_process,
        "can_upload_payment_proof": in_process,
        "closure_id": (
            str(UUID("70000000-0000-0000-0000-000000000001")) if in_process else None
        ),
        "amount_issue": "",
    }


async def _journey_list_payment_run_items(
    _session,
    *,
    status_filter,
    date_from=None,
    date_to=None,
    query=None,
    **_kwargs,
):
    del date_from, date_to, query
    if status_filter == "pendientes":
        return [_journey_payment_row(in_process=False)]
    if status_filter in {"cerradas", "en_proceso", "en_proceso_pago"}:
        return [_journey_payment_row(in_process=True)]
    return []


async def _journey_list_prestamo_payment_run_items(*_args, **_kwargs):
    return []


async def _journey_list_payment_run_closures(_session, *, limit=20):
    del limit
    return [
        {
            "id": str(UUID("70000000-0000-0000-0000-000000000001")),
            "closed_at": "2026-09-21 18:00:00",
            "run_date": "2026-09-21",
            "item_count": 1,
            "total_amount": Decimal("18450.00"),
            "closed_by_nombre": "Finanzas Browser UX",
        }
    ]


def _journey_allow_payment_run(_employee):
    return None


def _journey_true(_employee):
    return True


def _journey_cleanup_expense():
    employee = SimpleNamespace(
        id=UUID("10000000-0000-0000-0000-000000000406"),
        nombre="Responsable Contable Browser UX",
    )
    return SimpleNamespace(
        id=UUID("80000000-0000-0000-0000-000000000001"),
        numero_referencia="G-UX-COI-001",
        fecha=datetime(2026, 9, 18).date(),
        empleado=employee,
        empleado_id=employee.id,
        concepto="Hospedaje regional sin clasificación contable",
        proyecto="Copa Browser UX",
        gasto_cantidad=Decimal("8450.00"),
        metodo_pago="transferencia",
        cuenta_contable_base="5300",
        cuenta_contable=None,
        cuenta_contable_id=None,
        contra_cuenta_contable=None,
        contra_cuenta_contable_id=None,
        cuenta_iva=None,
        cuenta_iva_id=None,
        retencion_cuentas_json={},
        cfdi_report=None,
        cfdi_report_id=None,
        cfdi_uuid_manual="",
        iva=None,
        hospedaje_entidad_fiscal=None,
        hospedaje_tasa_impuesto=None,
        hospedaje_impuesto_monto=None,
        hospedaje_impuesto_confirmado=False,
        budget_concept_id=None,
        fase_torneo="Regional",
        origen="manual",
    )


async def _journey_cleanup_expenses(_session, *, extra_conditions=None):
    del extra_conditions
    return [_journey_cleanup_expense()]


async def _journey_unassigned_cfdi_options(_session):
    return []


async def _journey_cleanup_preview(
    _session,
    _expense,
    *,
    include_historical_precedent=False,
):
    del include_historical_precedent
    return {
        "preview": {
            "notes": ["Falta clasificación contable antes de exportar a COI."],
            "contra_account": {},
            "taxes": {
                "iva_account": {},
                "retenciones": [],
                "retenciones_total": 0.0,
                "impuestos_locales": [],
                "impuestos_locales_total": 0.0,
                "iva_trasladado": 0.0,
                "gastos_no_deducibles": [],
                "gastos_no_deducibles_total": 0.0,
                "base_gasto": 8450.0,
                "neto_contrapartida": 8450.0,
            },
        },
        "issues": [
            "Falta cuenta de cargo",
            "Falta contrapartida",
            "Falta CFDI vinculado",
        ],
        "historical_precedent_evidence": {},
    }


def _journey_cleanup_origin(_expense):
    return ("Gasto directo", "Sin documento ligado")


def _journey_cleanup_accounting_display(_expense):
    return SimpleNamespace(
        partida_name="Hospedaje y alimentación",
        partida_from_document=True,
        assigned_cuenta=None,
        mapped_cuenta=None,
    )


def _journey_project_name(value, _mapping):
    return str(value or "—")


def _journey_effective_budget_concept(_expense):
    return None


class _JourneyCuentaContableSuggester:
    def __init__(self, _session):
        pass

    async def get_suggestions_batch(self, **_kwargs):
        return {}


class _MutationResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalars(self):
        return _FakeScalarRows(self._rows)

    def fetchall(self):
        return [(row,) for row in self._rows]

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _MutationSession:
    """Small transaction adapter for canonical writers in the browser fixture.

    It is deliberately test-only: it has no database URL, credentials, or router
    dependency. ``commit`` snapshots the in-memory fixture and tests reset it via
    the page query string before each mutation journey.
    """

    def __init__(self, fixture):
        self.fixture = fixture
        self.added = []

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = uuid4()
        self.added.append(value)
        self.fixture.audit.append(value)

    async def execute(self, statement, *_args, **_kwargs):
        entities = [
            item.get("entity") for item in getattr(statement, "column_descriptions", [])
        ]
        entity = entities[0] if entities else None
        name = getattr(entity, "__name__", "")
        if name == "Documento":
            rows = (
                [
                    self.fixture.documentos[item]
                    for item in self.fixture.selected_documents
                ]
                if self.fixture.selected_documents
                else list(self.fixture.documentos.values())
            )
            return _MutationResult(rows)
        if name == "ExpenseReport":
            return _MutationResult([self.fixture.cleanup_expense])
        if name == "CuentaContable":
            return _MutationResult([self.fixture.account])
        if name == "PaymentRunClosureItem":
            return _MutationResult(self.fixture.closed_document_ids)
        return _MutationResult()

    async def get(self, entity, identifier):
        if getattr(entity, "__name__", "") == "Documento":
            return self.fixture.documentos.get(UUID(str(identifier)))
        return None

    async def commit(self):
        self.fixture.commits += 1

    async def rollback(self):
        self.fixture.rollbacks += 1

    async def refresh(self, _value):
        return None


class _MutationFixture:
    approval_id = UUID("90000000-0000-0000-0000-000000000001")
    reject_id = UUID("90000000-0000-0000-0000-000000000002")
    budget_id = UUID("90000000-0000-0000-0000-000000000003")
    payment_id = UUID("90000000-0000-0000-0000-000000000004")
    cleanup_id = UUID("90000000-0000-0000-0000-000000000005")
    concept_id = UUID("90000000-0000-0000-0000-000000000006")
    account_id = UUID("90000000-0000-0000-0000-000000000007")

    def reset(self):
        self.audit = []
        self.commits = 0
        self.rollbacks = 0
        self.closed_document_ids = []
        self.proofs = []
        self.generated_expense = None
        self.selected_documents = []
        self.approver = SimpleNamespace(
            id=UUID("90000000-0000-0000-0000-000000000101"),
            nombre="Aprobador Browser UX",
            rol="finanzas",
            aprobador_id=None,
        )
        self.accounting = SimpleNamespace(
            id=UUID("90000000-0000-0000-0000-000000000102"),
            nombre="Contabilidad Browser UX",
            rol="finanzas",
            departamento="Finanzas",
            permissions={"contabilidad.pagos.marcar_pagado"},
        )
        employee = SimpleNamespace(
            id=UUID("90000000-0000-0000-0000-000000000103"),
            aprobador_id=None,
            nombre="Solicitante Browser UX",
            departamento="Operaciones",
        )
        provider = SimpleNamespace(nombre="Proveedor Browser UX")

        def document(identifier, reference, state):
            return SimpleNamespace(
                id=identifier,
                numero_referencia=reference,
                empleado_id=employee.id,
                empleado=employee,
                tipo="SOLICITUD",
                estado=state,
                monto_solicitado=Decimal("1250.00"),
                monto_total=Decimal("1250.00"),
                currency="MXN",
                proveedor_cliente_id=UUID("90000000-0000-0000-0000-000000000104"),
                proveedor_cliente=provider,
                beneficiario_empleado_id=None,
                beneficiario_empleado=None,
                torneo=None,
                torneo_id=None,
                cuenta_gastos_id=None,
                gasto_generado_id=None,
                fecha_pago=None,
                metodo_pago="TRANSFERENCIA",
                concepto_pago="Hospedaje regional",
                pagado_en=None,
                aprobado_en=None,
                enviado_en=None,
                budget_concept_id=None,
                fase="Regional",
            )

        self.documentos = {
            self.approval_id: document(self.approval_id, "S-MUT-APPROVE", "enviado"),
            self.reject_id: document(self.reject_id, "S-MUT-REJECT", "enviado"),
            self.budget_id: document(
                self.budget_id, "S-MUT-BUDGET", "control_presupuestal"
            ),
            self.payment_id: document(self.payment_id, "S-MUT-PAY", "aprobado"),
        }
        self.cleanup_expense = SimpleNamespace(
            id=self.cleanup_id,
            cuenta_contable_id=None,
            contra_cuenta_contable_id=None,
            cuenta_iva_id=None,
            cfdi_report_id=None,
            cfdi_uuid_manual=None,
            nova_request_id=None,
            iva=None,
            hospedaje_entidad_fiscal=None,
            hospedaje_tasa_impuesto=None,
            hospedaje_impuesto_monto=None,
            hospedaje_impuesto_confirmado=False,
        )
        self.account = SimpleNamespace(id=self.account_id, activo=True)
        self.session = _MutationSession(self)


MUTATIONS = _MutationFixture()
MUTATIONS.reset()


async def _mutation_load_documento(_session, documento_id):
    return MUTATIONS.documentos.get(UUID(str(documento_id)))


async def _mutation_load_actor(_session, actor_id):
    for actor in (MUTATIONS.approver, MUTATIONS.accounting):
        if actor.id == UUID(str(actor_id)):
            return actor
    return None


async def _mutation_none(*_args, **_kwargs):
    return None


async def _mutation_false(*_args, **_kwargs):
    return False


async def _mutation_audit(*_args, **kwargs):
    MUTATIONS.audit.append(SimpleNamespace(**kwargs))


async def _mutation_budget_concept(_session, budget_concept_id, **_kwargs):
    if budget_concept_id != str(MUTATIONS.concept_id):
        return None
    return {"id": str(MUTATIONS.concept_id), "concept_name": "Hospedaje y alimentación"}


async def _mutation_payment_document(_session, documento_id):
    return MUTATIONS.documentos.get(UUID(str(documento_id)))


async def _mutation_payment_ready(*_args, **_kwargs):
    return SimpleNamespace(status="ready")


async def _mutation_create_expense(**_kwargs):
    expense = SimpleNamespace(
        id=uuid4(),
        numero_referencia="G-MUT-PAY",
        cfdi_report_id=None,
        cfdi_uuid_manual=None,
    )
    MUTATIONS.generated_expense = expense
    return expense


async def _mutation_store_proof(_session, *, attachments, **_kwargs):
    for attachment in attachments:
        MUTATIONS.proofs.append(
            {
                "bytes": attachment.raw_bytes,
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "categoria": attachment.categoria,
            }
        )
    return len(attachments)


# Patch only module references used by this isolated test app.
dependencies._load_empleado_proxy_by_id = _load_employee
dependencies.visible_tools_for = _visible_tools
dependencies.can_access_path = _can_access_path
client_executive_routes.explicit_tool_decision = _direction_decision
client_executive_routes.authorized_direction_portfolio_ids = _direction_portfolios
client_executive_routes.build_client_dashboard = _dashboard
user_routes.visible_tools_for = _panel_visible_tools
user_routes._can_review_pending_approvals = _panel_can_review_pending_approvals
user_routes._is_budget_control_user = _panel_is_budget_control_user
user_routes.fetch_active_tournaments_for_empleado = _journey_active_tournaments
user_routes._tournament_budget_concepts_map_for_js = _journey_budget_concepts
user_routes.fetch_documento_adjuntos_meta_batch = _journey_adjuntos
user_routes.fetch_documento_aprobador_display_batch = _journey_approver_names
user_routes._budget_concepts_for_document = _journey_concepts_for_document
admin_routes.require_payment_run_access = _journey_allow_payment_run
admin_routes.list_payment_run_items = _journey_list_payment_run_items
admin_routes.list_prestamo_payment_run_items = _journey_list_prestamo_payment_run_items
admin_routes.list_payment_run_closures = _journey_list_payment_run_closures
admin_routes.can_manage_payment_run = _journey_true
admin_routes.can_confirm_payment_run_payment = _journey_true
admin_routes.load_cleanup_expenses = _journey_cleanup_expenses
admin_routes.list_unassigned_cfdi_options = _journey_unassigned_cfdi_options
admin_routes.safe_build_cleanup_preview = _journey_cleanup_preview
admin_routes._cleanup_document_origin = _journey_cleanup_origin
admin_routes.build_cleanup_accounting_display = _journey_cleanup_accounting_display
admin_routes.resolve_project_name = _journey_project_name
admin_routes.resolve_effective_budget_concept = _journey_effective_budget_concept
cuenta_suggester_module.CuentaContableSuggester = _JourneyCuentaContableSuggester

# These are the persistence seams of the canonical AR writers. The validation,
# acceptance, reversal, and route contracts remain production code; only storage
# is held in memory for this browser-only fixture.
collection_matches.ensure_ar_collection_match_schema = _ar_no_schema
collection_matches._load_bank_movement = _ar_load_bank
collection_matches._find_active_match = _ar_find_active
collection_matches._resolve_bank_account = _ar_bank_account
collection_matches._resolve_cxc_account = _ar_cxc_account
collection_matches._insert_match = _ar_insert
collection_matches._create_collection_poliza = _ar_create_poliza
collection_matches._load_match = _ar_load_match
collection_matches._update_match_reversed = _ar_update_reversed
collection_matches._audit_match_event = _ar_audit

# The mutation pages invoke the existing writers. Their persistence collaborators
# are constrained to the resettable test transaction above, not replaced by a
# production-style route or identity bypass.
documento_workflow_service._load_documento = _mutation_load_documento
documento_workflow_service._load_actor = _mutation_load_actor
documento_workflow_service.documento_financial_terminal_reason = _mutation_none
documento_workflow_service.documento_has_approval = _mutation_false
documento_workflow_service._document_has_recorded_approval = _mutation_false
documento_workflow_service.record_customer_success_audit_event = _mutation_audit
documento_workflow_service._reopen_informe_de_gastos_on_reject = _mutation_none
documento_workflow_service.assign_fecha_pago_on_solicitud_approval = lambda _doc: None
documento_workflow_service.ensure_provider_approval_posting = _mutation_payment_ready
documento_workflow_service.actor_is_route_approver = _mutation_false
user_routes.resolve_budget_concept = _mutation_budget_concept
documento_payment_service._load_documento_for_payment = _mutation_payment_document
documento_payment_service.parse_amex_payment_card_id = lambda _doc: None
documento_payment_service.ensure_provider_payment_posting = _mutation_payment_ready
documento_payment_service.create_expense_from_data = _mutation_create_expense
documento_payment_service._apply_no_deducible_account_for_unlinked_solicitud = (
    _mutation_none
)
documento_payment_service._schedule_solicitud_paid_telegram_notifications = (
    lambda **_kwargs: None
)
admin_routes.add_solicitud_documento_adjuntos = _mutation_store_proof
payment_run_service.ensure_payment_run_schema = _mutation_none
payment_run_service.record_customer_success_audit_event = _mutation_audit


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="browser-ux-test-session")
app.dependency_overrides[dependencies.get_db_session] = _db_session
app.include_router(client_executive_routes.router)


@app.get("/_test/health")
async def health():
    return {"ok": True}


def _mutation_page(receipt: str = "") -> HTMLResponse:
    payment = MUTATIONS.documentos[MUTATIONS.payment_id]
    return HTMLResponse(f"""<!doctype html><html><body><main>
        <h1>Simulación aislada de mutaciones UX</h1>
        <p>Fixture transaccional de prueba;
        no usa credenciales ni datos productivos.</p>
        {receipt}
        <section><h2>Aprobación y rechazo</h2>
        <form method="post" action="/_test/mutations/approve">
          <button>Aprobar solicitud</button>
        </form>
        <form method="post" action="/_test/mutations/reject">
          <label>Motivo de rechazo <textarea name="reason"></textarea></label>
          <button>Rechazar solicitud</button>
        </form></section>
        <section><h2>Control Presupuestal</h2>
        <form method="post" action="/_test/mutations/budget">
          <label>Concepto
            <input name="concept_id" value="{MUTATIONS.concept_id}">
          </label>
          <button>Asignar concepto y enviar</button>
        </form></section>
        <section><h2>Payment Run</h2>
        <p id="payment-state">Estado actual: {escape(payment.estado)}</p>
        <form method="post" action="/_test/mutations/cutoff">
          <button>Cerrar corte</button>
        </form>
        <form method="post" action="/_test/mutations/proof"
              enctype="multipart/form-data">
          <label>Comprobante <input type="file" name="proof"></label>
          <label>Fecha efectiva <input type="date" name="fecha_pago_efectiva" required></label>
          <button>Registrar comprobante y pago</button>
        </form></section>
        <section><h2>Limpieza contable</h2>
        <form method="post" action="/_test/mutations/cleanup">
          <button>Corregir cuentas de la fila</button>
        </form>
        </section></main></body></html>""")


@app.get("/_test/mutations", response_class=HTMLResponse)
async def mutation_home(reset: bool = True):
    if reset:
        MUTATIONS.reset()
    return _mutation_page()


def _mutation_receipt(
    outcome: str, state: str, next_queue: str, detail: str
) -> HTMLResponse:
    return _mutation_page(
        f'<section aria-label="Recibo de operación"><h2>Recibo de operación</h2>'
        f"<p>Resultado: {escape(outcome)}</p><p>Estado: {escape(state)}</p>"
        f"<p>Siguiente cola: {escape(next_queue)}</p><p>{escape(detail)}</p></section>"
    )


@app.post("/_test/mutations/approve", response_class=HTMLResponse)
async def mutation_approve():
    document = MUTATIONS.documentos[MUTATIONS.approval_id]
    result = await documento_workflow_service.transition_documento_workflow(
        MUTATIONS.session,
        documento_id=document.id,
        actor_id=MUTATIONS.approver.id,
        action="approve",
        comentario="Aprobación UX aislada",
        surface="browser_fixture",
    )
    return _mutation_receipt(
        "Aprobado",
        result.documento.estado,
        "Payment Run",
        "Actor: Aprobador Browser UX",
    )


@app.post("/_test/mutations/reject", response_class=HTMLResponse)
async def mutation_reject(reason: str = Form("")):
    if not reason.strip():
        return _mutation_receipt(
            "Rechazo no enviado",
            "enviado",
            "Aprobación",
            (
                "Motivo requerido por el escenario UX; la regla productiva se "
                "entrega en #369."
            ),
        )
    document = MUTATIONS.documentos[MUTATIONS.reject_id]
    result = await documento_workflow_service.transition_documento_workflow(
        MUTATIONS.session,
        documento_id=document.id,
        actor_id=MUTATIONS.approver.id,
        action="reject",
        comentario=reason,
        surface="browser_fixture",
    )
    return _mutation_receipt(
        "Rechazado",
        result.documento.estado,
        "Solicitante",
        f"Motivo conservado: {result.aprobacion.comentario or 'sin motivo'}",
    )


@app.post("/_test/mutations/budget", response_class=HTMLResponse)
async def mutation_budget(concept_id: str = Form("")):
    document = MUTATIONS.documentos[MUTATIONS.budget_id]
    MUTATIONS.selected_documents = [document.id]
    try:
        result, concept = await user_routes._apply_control_presupuestal_assignment(
            MUTATIONS.session,
            documento_id=document.id,
            budget_concept_id=concept_id,
            actor=MUTATIONS.approver,
        )
    except documento_workflow_service.DocumentoWorkflowValidationError as exc:
        return _mutation_receipt(
            "Asignación rechazada", document.estado, "Control Presupuestal", exc.message
        )
    await MUTATIONS.session.commit()
    return _mutation_receipt(
        "Concepto asignado",
        result.estado,
        "Aprobación",
        f"Concepto: {concept['concept_name']}",
    )


@app.post("/_test/mutations/cutoff", response_class=HTMLResponse)
async def mutation_cutoff():
    document = MUTATIONS.documentos[MUTATIONS.payment_id]
    MUTATIONS.selected_documents = [document.id]
    try:
        result = await payment_run_service.close_payment_run(
            MUTATIONS.session,
            document_ids=[document.id],
            actor_id=MUTATIONS.approver.id,
            notes="Corte UX aislado",
        )
    except payment_run_service.PaymentRunValidationError as exc:
        return _mutation_receipt(
            "Corte rechazado", document.estado, "Payment Run", str(exc)
        )
    MUTATIONS.closed_document_ids.append(document.id)
    return _mutation_receipt(
        "Corte cerrado",
        document.estado,
        "Comprobante de pago",
        f"Corte: {result.closure_id}; no se marcó pagada.",
    )


@app.post("/_test/mutations/proof", response_class=HTMLResponse)
async def mutation_proof(
    proof: UploadFile | None = File(None),
    fecha_pago_efectiva: str | None = Form(None),
):
    document = MUTATIONS.documentos[MUTATIONS.payment_id]
    if document.estado != "en_proceso_pago":
        return _mutation_receipt(
            "Pago rechazado",
            document.estado,
            "Payment Run",
            "Se requiere corte previo antes del comprobante.",
        )
    if proof is None or not proof.filename:
        return _mutation_receipt(
            "Pago rechazado",
            document.estado,
            "Comprobante de pago",
            "Adjunta un comprobante de pago.",
        )
    response = await admin_routes.admin_finance_payment_run_upload_payment_proof(
        document.id,
        Request({"type": "http", "method": "POST", "path": "/_test/mutations/proof"}),
        MUTATIONS.session,
        MUTATIONS.accounting,
        proof,
        fecha_pago_efectiva=fecha_pago_efectiva,
    )
    stored = MUTATIONS.proofs[-1] if MUTATIONS.proofs else None
    if response.status_code != 303 or stored is None:
        return _mutation_receipt(
            "Pago rechazado",
            document.estado,
            "Comprobante de pago",
            "El comprobante no se pudo persistir.",
        )
    return _mutation_receipt(
        "Pago registrado",
        document.estado,
        "Contabilidad",
        (
            f"Actor: {MUTATIONS.accounting.nombre}; evidencia persistida: "
            f"{stored['filename']} ({len(stored['bytes'])} bytes); "
            f"gasto generado: {MUTATIONS.generated_expense.numero_referencia}"
        ),
    )


@app.post("/_test/mutations/cleanup", response_class=HTMLResponse)
async def mutation_cleanup():
    expense = await expense_accounting_cleanup_service.save_expense_cleanup(
        MUTATIONS.session,
        MUTATIONS.cleanup_id,
        cuenta_contable_id=str(MUTATIONS.account_id),
        contra_cuenta_contable_id=str(MUTATIONS.account_id),
    )
    ready = bool(expense.cuenta_contable_id and expense.contra_cuenta_contable_id)
    return _mutation_receipt(
        "Fila corregida",
        "cuentas asignadas",
        "CFDI",
        (
            "Se corrigieron solo las cuentas; CFDI sigue pendiente."
            if ready
            else "La preparación no quedó lista."
        ),
    )


@app.get("/_test/login")
async def login(request: Request):
    request.session["empleado_id"] = str(EMPLOYEE_ID)
    return {"ok": True}


PROFILE_FIXTURES = {
    "employee": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000401"),
            nombre="Empleado Browser UX",
            correo="employee-browser-ux@example.invalid",
            rol="empleado",
            activo=True,
            departamento="Operaciones",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "panel.telegram",
                "panel.entrenamiento",
                "gastos.informes",
                "gastos.solicitudes",
                "gastos.mis_gastos",
                "soporte.self",
            },
        ),
        "active": "gastos",
        "workspace": "solicitudes",
        "task": "Solicitar una transferencia a un proveedor.",
    },
    "approver": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000402"),
            nombre="Aprobador Browser UX",
            correo="approver-browser-ux@example.invalid",
            rol="coordinador",
            activo=True,
            departamento="Operaciones",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "gastos.informes",
                "gastos.solicitudes",
                "soporte.self",
            },
        ),
        "active": "gastos",
        "workspace": "documentos",
        "task": "Atender una solicitud que requiere aprobación.",
    },
    "budget_control": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000403"),
            nombre="Control Presupuestal Browser UX",
            correo="budget-control-browser-ux@example.invalid",
            rol="finanzas",
            activo=True,
            departamento="Finanzas",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "gastos.solicitudes",
                "admin.finanzas",
                "admin.contabilidad",
                "admin.gastos.dashboard",
                "admin.gastos.limpieza",
                "presupuestos.ingresos",
                "soporte.self",
            },
        ),
        "active": "finanzas",
        "workspace": "documentos",
        "task": "Resolver un documento detenido por asignación presupuestal.",
    },
    "finance": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000404"),
            nombre="Finanzas Browser UX",
            correo="finance-browser-ux@example.invalid",
            rol="finanzas",
            activo=True,
            departamento="Finanzas",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "admin.gastos.dashboard",
                "admin.gastos.expenses",
                "admin.gastos.invoices",
                "admin.gastos.cfdi_carga",
                "admin.gastos.cfdi_matching",
                "admin.gastos.limpieza",
                "admin.finanzas",
                "admin.contabilidad",
                "presupuestos.ingresos",
                "soporte.self",
            },
        ),
        "active": "finanzas",
        "workspace": None,
        "task": "Preparar el siguiente corte de pagos.",
    },
    "accounting": {
        "employee": SimpleNamespace(
            id=UUID("10000000-0000-0000-0000-000000000405"),
            nombre="Contabilidad Browser UX",
            correo="accounting-browser-ux@example.invalid",
            rol="finanzas",
            activo=True,
            departamento="Contabilidad",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "admin.gastos.dashboard",
                "admin.gastos.limpieza",
                "admin.finanzas",
                "admin.contabilidad",
                "presupuestos.ingresos",
                "soporte.self",
            },
        ),
        "active": "contabilidad",
        "workspace": None,
        "task": "Resolver un gasto que no está listo para COI.",
    },
    "direction": {
        "employee": SimpleNamespace(
            id=EMPLOYEE_ID,
            nombre="Dirección Browser UX",
            correo="direction-browser-ux@example.invalid",
            rol="coordinador",
            activo=True,
            departamento="Dirección",
            permissions=set(),
            visible_tool_keys={
                "panel.home",
                "direccion.tableros_ejecutivos",
                "soporte.self",
            },
        ),
        "active": "direccion",
        "workspace": None,
        "task": "Identificar qué requiere atención en Dirección.",
    },
}


@app.get("/_test/profile/{profile_name}", response_class=HTMLResponse)
async def profile_navigation(profile_name: str):
    fixture = PROFILE_FIXTURES.get(profile_name)
    if fixture is None:
        return HTMLResponse("<h1>Perfil no encontrado</h1>", status_code=404)

    employee = fixture["employee"]
    top = user_routes.render_top_navigation(employee, fixture["active"])
    workspace = ""
    if fixture["workspace"] is not None:
        workspace = user_routes._gastos_workspace_nav_html(
            employee, fixture["workspace"]
        )

    admin = ""
    if profile_name in {"budget_control", "finance", "accounting"}:
        admin = admin_routes.render_admin_navigation(employee)

    accounting = ""
    if profile_name == "accounting":
        accounting = user_routes._contabilidad_subnav("estado")

    html = f"""
    <html>
      <head><title>Perfil UX · {profile_name}</title><style>
      html,body{{margin:0;max-width:100%;}}
      main{{padding:24px;box-sizing:border-box;max-width:100%;}}
      </style></head>
      <body>
        <main>
          <h1>Perfil simulado: {profile_name}</h1>
          <p data-testid="task-prompt">{fixture["task"]}</p>
          <section aria-label="Navegación global">{top}</section>
          <section aria-label="Navegación de Gastos">{workspace}</section>
          <section aria-label="Navegación administrativa">{admin}</section>
          <section aria-label="Navegación contable">{accounting}</section>
        </main>
      </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/_test/panel/{profile_name}", response_class=HTMLResponse)
async def profile_panel(profile_name: str, request: Request):
    fixture = PROFILE_FIXTURES.get(profile_name)
    if fixture is None:
        return HTMLResponse("<h1>Perfil no encontrado</h1>", status_code=404)

    html = await user_routes.panel(
        request,
        _EmptySession(),
        fixture["employee"],
    )
    return HTMLResponse(html)


@app.get("/gastos-terceros", response_class=HTMLResponse)
async def journey_employee_transfer_requests(request: Request):
    html = await user_routes.gastos_terceros(
        request,
        _RowsSession([]),
        PROFILE_FIXTURES["employee"]["employee"],
    )
    return HTMLResponse(html)


@app.get("/documentos/nueva-solicitud-terceros", response_class=HTMLResponse)
async def journey_employee_new_third_party_request(request: Request):
    html = await user_routes.nueva_solicitud_terceros_form(
        request,
        _RowsSession([_journey_provider()]),
        PROFILE_FIXTURES["employee"]["employee"],
    )
    return HTMLResponse(html)


@app.get("/documentos/pendientes", response_class=HTMLResponse)
async def journey_approver_pending(request: Request):
    html = await user_routes.documentos_pendientes(
        request,
        _RowsSession([_journey_pending_document()]),
        PROFILE_FIXTURES["approver"]["employee"],
    )
    return HTMLResponse(html)


@app.get("/documentos/control-presupuestal", response_class=HTMLResponse)
async def journey_budget_control_queue(request: Request):
    html = await user_routes.documentos_control_presupuestal(
        request,
        _RowsSession([_journey_budget_control_document()]),
        PROFILE_FIXTURES["budget_control"]["employee"],
    )
    return HTMLResponse(html)


_BUDGET_JOURNEY_VERSION_ID = "92000000-0000-0000-0000-000000000001"
_BUDGET_JOURNEY_TOURNAMENT_KEY = "copa-browser-ux"
_BUDGET_JOURNEY_LINE_ID = "92000000-0000-0000-0000-000000000002"


def _budget_journey_line() -> dict[str, object]:
    return {
        "id": _BUDGET_JOURNEY_LINE_ID,
        "budget_concept_id": "92000000-0000-0000-0000-000000000003",
        "concept_name": "Hospedaje y alimentación",
        "budget_amount": 120_000,
        "phase": "Fase regional",
        "cuenta_codigo": "6100-001",
        "cuenta_nombre": "Gastos de torneo",
    }


def _budget_journey_plan() -> dict[str, dict[int, dict[str, float]]]:
    return {
        _BUDGET_JOURNEY_LINE_ID: {
            week: {
                "budget_expense_amount": 120_000.0 if week == 1 else 0.0,
                "expected_income_amount": 0.0,
            }
            for week in range(1, 54)
        }
    }


def _budget_journey_actuals() -> dict[str, dict[int, dict[str, float]]]:
    return {
        _BUDGET_JOURNEY_LINE_ID: {
            week: {
                "real_expense_cash": 45_000.0 if week == 1 else 0.0,
                "committed_unpaid": 15_000.0 if week == 1 else 0.0,
                "real_income": 0.0,
            }
            for week in range(1, 54)
        }
    }


def _budget_journey_shell(*, title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""
        <!doctype html><html><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{escape(title)}</title>
        <style>
          {admin_routes._admin_workspace_styles("1400px", layout="data")}
          html,body{{margin:0;max-width:100%;}}
          .workspace-shell{{box-sizing:border-box;max-width:100%;padding:24px;}}
          .workspace-card{{box-sizing:border-box;max-width:100%;}}
        </style></head><body><main class="workspace-shell">{body}</main></body></html>
        """
    )


@app.get("/admin/presupuestos", response_class=HTMLResponse)
async def journey_budget_dashboard() -> HTMLResponse:
    """Test-only, read-only budget dashboard using canonical UI renderers."""
    cards = admin_budget_ui.render_tournament_dashboard_cards(
        [
            {
                "tournament_id": _BUDGET_JOURNEY_TOURNAMENT_KEY,
                "tournament_code": "CBUX-2026",
                "tournament_name": "Copa Browser UX",
                "line_count": 1,
            }
        ],
        edition_year=2026,
        version_id=_BUDGET_JOURNEY_VERSION_ID,
        tournament_rollups={
            _BUDGET_JOURNEY_TOURNAMENT_KEY: {
                "budget_expense_total": 120_000.0,
                "real_expense_total": 45_000.0,
                "committed_pending_total": 15_000.0,
            }
        },
    )
    return _budget_journey_shell(
        title="Presupuestos - Administración",
        body=f"""
        <h1>Presupuestos 2026</h1>
        <p>Selecciona un torneo para revisar presupuesto y ejercido real.</p>
        <section class="workspace-card" aria-label="Torneos presupuestales">
          {cards}
        </section>
        """,
    )


@app.get(
    "/admin/presupuestos/torneo/{tournament_key}", response_class=HTMLResponse
)
async def journey_budget_tournament_detail(tournament_key: str) -> HTMLResponse:
    if tournament_key != _BUDGET_JOURNEY_TOURNAMENT_KEY:
        return HTMLResponse("<h1>Torneo no encontrado</h1>", status_code=404)

    line = _budget_journey_line()
    plan_map = _budget_journey_plan()
    actuals_map = _budget_journey_actuals()
    executive = admin_budget_ui.render_budget_executive_dashboard(
        [line],
        plan_map=plan_map,
        actuals_map=actuals_map,
        tournament_key=tournament_key,
        edition_year=2026,
        version_id=_BUDGET_JOURNEY_VERSION_ID,
        budget_view="expenses",
        show_committed=True,
    )
    matrix = admin_budget_ui.render_budget_partida_matrix(
        [line],
        plan_map=plan_map,
        actuals_map=actuals_map,
        version_id=_BUDGET_JOURNEY_VERSION_ID,
        tournament_key=tournament_key,
        can_edit=False,
        edition_year=2026,
        matrix_mode="expenses",
        budget_view="expenses",
        show_committed=True,
    )
    return _budget_journey_shell(
        title="Copa Browser UX - Presupuestos",
        body=f"""
        <h1>Copa Browser UX</h1>
        <p>Presupuesto operativo 2026</p>
        <a href="/admin/presupuestos">← Dashboard</a>
        {executive}
        <section class="workspace-card" aria-label="Partidas presupuestales">
          {matrix}
        </section>
        """,
    )


@app.get("/admin/finanzas/payment-run", response_class=HTMLResponse)
async def journey_payment_run(request: Request):
    return await admin_routes.admin_finance_payment_run(
        request,
        _EmptySession(),
        PROFILE_FIXTURES["finance"]["employee"],
        status=request.query_params.get("status", "pendientes"),
        date_from=request.query_params.get("date_from"),
        date_to=request.query_params.get("date_to"),
        q=request.query_params.get("q"),
        vista=request.query_params.get("vista"),
    )


@app.get("/admin/gastos/sin-cuenta-contable", response_class=HTMLResponse)
async def journey_accounting_cleanup(request: Request):
    html = await admin_routes.gastos_sin_cuenta_contable(
        request,
        _RowsSession([]),
        period=None,
        bi_year=None,
        bi_scope=None,
        current_empleado=PROFILE_FIXTURES["accounting"]["employee"],
    )
    return HTMLResponse(html)


def _ar_shell(*, title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{escape(title)} - Samchat</title>
        <style>{admin_routes._admin_workspace_styles("1380px", layout="data")}{ar_admin_ui.ar_admin_styles()}</style>
        </head><body><div class="workspace-shell">{body}</div></body></html>"""
    )


@app.get("/admin/finanzas/cuentas-por-cobrar", response_class=HTMLResponse)
async def journey_finance_accounts_receivable(reset: bool = False):
    """Test-only shell over the canonical AR read and matching renderers."""
    if reset:
        AR_FIXTURE.reset()
    read_model = AR_FIXTURE.read_model()
    body = ar_admin_ui.render_ar_read_model_html(
        read_model,
        base_url="/admin/finanzas/cuentas-por-cobrar",
        export_url="/admin/finanzas/cuentas-por-cobrar/export.xlsx",
        prepoliza_export_url=(
            "/admin/finanzas/cuentas-por-cobrar/prepolizas-coi.xlsx"
        ),
        return_to="/admin/finanzas/cuentas-por-cobrar",
    )
    body += ar_admin_ui.render_ar_matching_workbench_html(
        AR_FIXTURE.matching_workbench(),
        return_to="/admin/finanzas/cuentas-por-cobrar",
        bank_accounts=[
            {
                "id": AR_FIXTURE.bank_account_id,
                "codigo": "1020-001",
                "nombre": "Banco Browser UX",
            }
        ],
    )
    return _ar_shell(title="Cuentas por cobrar", body=body)


@app.post("/admin/finanzas/cuentas-por-cobrar/matches/accept")
async def journey_finance_ar_match_accept(
    budget_version_id: str = Form(...),
    ar_item_id: str = Form(...),
    bank_movement_id: str = Form(...),
    ar_amount: float = Form(...),
    acceptance_reason: str = Form(...),
    bank_account_id: str = Form(...),
    budget_line_id: str = Form(""),
    cfdi_report_id: str = Form(""),
    payer_rfc: str = Form(""),
    payer_name: str = Form(""),
    return_to: str = Form("/admin/finanzas/cuentas-por-cobrar"),
):
    return await admin_routes.admin_finance_ar_match_accept(
        budget_version_id=budget_version_id,
        ar_item_id=ar_item_id,
        bank_movement_id=bank_movement_id,
        ar_amount=ar_amount,
        acceptance_reason=acceptance_reason,
        bank_account_id=bank_account_id,
        budget_line_id=budget_line_id or None,
        cfdi_report_id=cfdi_report_id or None,
        payer_rfc=payer_rfc or None,
        payer_name=payer_name or None,
        return_to=return_to,
        current_empleado=PROFILE_FIXTURES["accounting"]["employee"],
        session=AR_FIXTURE.session,
    )


@app.post("/admin/finanzas/cuentas-por-cobrar/matches/{match_id}/reverse")
async def journey_finance_ar_match_reverse(
    match_id: str,
    reversal_reason: str = Form(...),
    return_to: str = Form("/admin/finanzas/cuentas-por-cobrar"),
):
    return await admin_routes.admin_finance_ar_match_reverse(
        match_id=match_id,
        reversal_reason=reversal_reason,
        return_to=return_to,
        current_empleado=PROFILE_FIXTURES["accounting"]["employee"],
        session=AR_FIXTURE.session,
    )


@app.get("/admin/finanzas/cuentas-por-cobrar/prepolizas-coi.xlsx")
async def journey_finance_ar_prepoliza_export():
    return HTMLResponse("<h1>Prepólizas CxC</h1><p>Exportación COI preparada.</p>")


@app.get("/admin/contabilidad/cuentas-por-cobrar", response_class=HTMLResponse)
async def journey_accounting_accounts_receivable():
    """Isolated destination proving the accounting CxC purpose remains distinct."""
    return _ar_shell(
        title="Vista contable CxC",
        body="""
        <section class="workspace-card"><h1>Vista contable CxC</h1>
        <p>Consulta de CFDI emitidos y pólizas de ingreso cobrado.</p>
        <p>Esta vista no acepta ni revierte matches de cobranza.</p></section>
        """,
    )
