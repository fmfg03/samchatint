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
from devnous.gastos.services import cuenta_contable_suggester as cuenta_suggester_module
from devnous.gastos.services import (
    documento_payment_service,
    documento_workflow_service,
)
from devnous.gastos.services import (
    expense_accounting_cleanup_service,
    payment_run_service,
)

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
async def mutation_proof(proof: UploadFile | None = File(None)):
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


@app.get("/admin/finanzas/payment-run", response_class=HTMLResponse)
async def journey_payment_run(request: Request):
    return await admin_routes.admin_finance_payment_run(
        request,
        _EmptySession(),
        PROFILE_FIXTURES["finance"]["employee"],
        status="pendientes",
        date_from=None,
        date_to=None,
        q=None,
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
