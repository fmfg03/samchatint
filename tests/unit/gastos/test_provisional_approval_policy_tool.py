"""Acceptance checks for the bounded provisional-approval configuration tool."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "provisional_policy_tool", ROOT / "scripts/apply_provisional_approval_policy.py"
)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


@pytest.fixture
def before():
    projects = [
        {"id": str(UUID(int=index)), "name": name, "active": True}
        for index, name in enumerate(tool.PROJECT_MATRIX, 1)
    ]
    rrss = next(
        p["id"] for p in projects if p["name"] == "Gestión RRSS y Transmisiones"
    )
    return {
        "employees": [
            {
                "id": value,
                "nombre": "Verified employee",
                "rol": "superadmin" if value == tool.ACTOR_ID else "admin",
                "activo": True,
            }
            for value in sorted(
                {
                    tool.GN,
                    tool.GV,
                    tool.ODILON,
                    tool.OLOF,
                    tool.LUIS_ANGEL,
                    tool.ACTOR_ID,
                }
            )
        ],
        "positions": [
            {
                "position_key": tool.OPERATIONS_POSITION,
                "label": "Operaciones",
                "active": True,
            },
            {"position_key": "direccion_general", "label": "DG", "active": True},
        ],
        "assignments": [
            {
                "position_key": tool.OPERATIONS_POSITION,
                "empleado_id": tool.ODILON,
                "active": True,
            },
            {
                "position_key": "direccion_general",
                "empleado_id": tool.GV,
                "active": True,
            },
        ],
        "projects": projects,
        "rules": [
            {
                "tournament_id": p["id"],
                "eligible_position_keys": [tool.OPERATIONS_POSITION],
                "requires_operations_reference": True,
                "active": True,
            }
            for p in projects
            if p["name"] not in {"Otros Proyectos", "Otros ingresos"}
        ],
        "document": [
            {
                "id": tool.DOCUMENT_ID,
                "numero_referencia": tool.DOCUMENT_REFERENCE,
                "estado": "enviado",
                "referencia_operaciones": "363",
                "torneo_id": rrss,
                "empleado_id": "10000000-0000-0000-0000-000000000099",
                "beneficiario_empleado_id": None,
                "monto_total": "250.00",
                "aprobado_en": None,
                "pagado_en": None,
            }
        ],
        "routes": [
            {
                "documento_id": tool.DOCUMENT_ID,
                "eligible_position_keys": [tool.OPERATIONS_POSITION],
                "eligible_empleado_ids": [tool.ODILON],
                "requires_operations_reference": True,
                "source": "project",
                "resolved_at": "2026-10-01",
            },
            {
                "documento_id": "10000000-0000-0000-0000-000000000088",
                "eligible_position_keys": ["direccion_general"],
                "eligible_empleado_ids": [tool.GV],
                "requires_operations_reference": False,
                "source": "project",
                "resolved_at": "2026-10-01",
            },
        ],
        "approvals": [
            {
                "id": "10000000-0000-0000-0000-000000000077",
                "entidad_id": tool.DOCUMENT_ID,
                "aprobador_id": tool.ACTOR_ID,
                "accion": "enviar",
                "comentario": None,
            }
        ],
    }


def fake_policy():
    async def context(session, document):
        del session
        return {
            "tournament_id": document.torneo_id,
            "requester_id": document.empleado_id,
            "beneficiary_id": document.beneficiario_empleado_id,
        }

    def route(keys, required, source):
        return SimpleNamespace(
            eligible_position_keys=keys,
            requires_operations_reference=required,
            source=source,
        )

    def fingerprint(**kwargs):
        kwargs["route"] = vars(kwargs["route"])
        return "p20261023:project:" + tool.plan_sha(kwargs)

    return SimpleNamespace(
        resolve_document_route_context=context,
        ProjectAuthorizationRoute=route,
        build_route_context_source=fingerprint,
    )


@pytest.fixture
async def plan(before, monkeypatch):
    monkeypatch.setattr(tool, "canonical_api", fake_policy)
    return await tool.build_plan(
        None, before, {"commit": "reviewed", "code_sha256": "code"}
    )


def after_state(before, plan):
    after = copy.deepcopy(before)
    after["positions"].extend(
        {"position_key": key, "label": p["label"], "active": True}
        for key, p in tool.POSITIONS.items()
    )
    after["assignments"].extend(
        {"position_key": key, "empleado_id": employee, "active": True}
        for key, p in tool.POSITIONS.items()
        for employee in p["members"]
    )
    after["rules"] = [
        {
            k: r[k]
            for k in (
                "tournament_id",
                "eligible_position_keys",
                "requires_operations_reference",
                "active",
            )
        }
        for r in plan["configuration"]["project_rules"]
    ]
    after["routes"][0].update(plan["expected_route"])
    after["approvals"].append(
        {
            "id": str(uuid4()),
            "entidad_id": tool.DOCUMENT_ID,
            "aprobador_id": tool.ACTOR_ID,
            "accion": tool.AUDIT_ACTION,
            "comentario": json.dumps(
                {
                    "audit_context": {
                        "plan_sha256": plan["sha256"],
                        "document_context": plan["document_context"],
                    },
                }
            ),
        }
    )
    return after


def test_configuration_has_exact_members_and_matrix_without_organizational_changes(
    before,
):
    original = copy.deepcopy(before)
    plan = tool.configuration_plan(before)
    assert before == original
    assert len(plan["positions"]) == 4
    assert sum(len(p["members"]) for p in plan["positions"].values()) == 8
    assert len(plan["project_rules"]) == 15
    assert tool.ACTOR_ID not in plan["positions"][tool.EXCEPTION_POSITION]["members"]
    assert plan["positions"][tool.FINANCE_POSITION]["members"] == [
        tool.LUIS_ANGEL,
        tool.ACTOR_ID,
    ]


def test_folio_exclusions_do_not_rewrite_other_project_flags(before):
    plan = tool.configuration_plan(before)
    old = {r["tournament_id"]: r for r in before["rules"]}
    for rule in plan["project_rules"]:
        expected = (
            False
            if rule["name"] in tool.EXCLUDED_FOLIO_PROJECTS
            else bool(
                old.get(rule["tournament_id"], {}).get("requires_operations_reference")
            )
        )
        assert rule["requires_operations_reference"] is expected


def test_inactive_old_rule_does_not_impose_folio_requirement_when_reactivated(before):
    old_rule = before["rules"][0]
    old_rule["active"] = False
    old_rule["requires_operations_reference"] = True
    proposed = tool.configuration_plan(before)["project_rules"]
    reactivated = next(
        rule for rule in proposed if rule["tournament_id"] == old_rule["tournament_id"]
    )
    assert reactivated["active"] is True
    assert reactivated["requires_operations_reference"] is False
    for rule in proposed:
        if (
            rule["tournament_id"] != old_rule["tournament_id"]
            and rule["name"] not in tool.EXCLUDED_FOLIO_PROJECTS
        ):
            previous = next(
                (
                    item
                    for item in before["rules"]
                    if item["tournament_id"] == rule["tournament_id"]
                ),
                None,
            )
            assert rule["requires_operations_reference"] is bool(
                previous
                and previous["active"]
                and previous["requires_operations_reference"]
            )


@pytest.mark.parametrize(
    "state", ["borrador", "aprobado", "pagado", "en_proceso_pago", "rechazado"]
)
def test_final_or_unexpected_document_states_block(before, state):
    before["document"][0]["estado"] = state
    with pytest.raises(ValueError, match="not enviado"):
        tool.configuration_plan(before)


@pytest.mark.parametrize(
    "field,value",
    [
        ("referencia_operaciones", "364"),
        ("numero_referencia", "S-other"),
        ("aprobado_en", "2026-10-23"),
        ("pagado_en", "2026-10-23"),
    ],
)
def test_changed_document_identity_or_history_blocks(before, field, value):
    before["document"][0][field] = value
    with pytest.raises(ValueError):
        tool.configuration_plan(before)


def test_prior_financial_approval_blocks_even_if_current_state_is_sent(before):
    before["approvals"].append({"accion": "aprobar"})
    with pytest.raises(ValueError, match="financial approval"):
        tool.configuration_plan(before)


def test_unexpected_member_cannot_be_silently_disabled_or_reassigned(before):
    before["assignments"].append(
        {"position_key": tool.GN_POSITION, "empleado_id": tool.GV, "active": False}
    )
    with pytest.raises(ValueError, match="Unexpected existing member"):
        tool.configuration_plan(before)


def test_configured_operations_holder_must_be_exact_approved_person(before):
    before["assignments"][0]["empleado_id"] = tool.GV
    with pytest.raises(ValueError, match="Operations holder"):
        tool.configuration_plan(before)


@pytest.mark.parametrize(
    "kind",
    [
        "inactive",
        "missing",
        "unauthorized_actor",
        "duplicate_project",
        "inactive_project",
        "missing_project",
        "wrong_project",
    ],
)
def test_unavailable_or_ambiguous_authority_fails_closed(before, kind):
    if kind == "inactive":
        before["employees"][0]["activo"] = False
    elif kind == "missing":
        before["employees"].pop()
    elif kind == "unauthorized_actor":
        next(r for r in before["employees"] if r["id"] == tool.ACTOR_ID)[
            "rol"
        ] = "admin"
    elif kind == "duplicate_project":
        before["projects"].append(dict(before["projects"][0], id=str(uuid4())))
    elif kind == "inactive_project":
        before["projects"][0]["active"] = False
    elif kind == "missing_project":
        before["projects"].pop()
    else:
        before["document"][0]["torneo_id"] = before["projects"][0]["id"]
    with pytest.raises(ValueError):
        tool.configuration_plan(before)


@pytest.mark.asyncio
async def test_context_and_configuration_are_bound_to_hash(before, plan):
    assert plan["expected_route"]["eligible_empleado_ids"] == [tool.GN]
    assert plan["expected_route"]["source"].startswith("p20261023:project:")
    assert plan["sha256"] == tool.plan_sha(
        {k: v for k, v in plan.items() if k != "sha256"}
    )
    changed = copy.deepcopy(plan)
    changed["document_context"]["beneficiary_id"] = tool.GV
    with pytest.raises(ValueError, match="hash is invalid"):
        await tool.apply_plan(None, before, changed, plan["sha256"])


@pytest.mark.asyncio
async def test_changed_before_state_rejected_before_any_mutation(before, plan):
    before["document"][0]["monto_total"] = "251.00"
    with pytest.raises(ValueError, match="state changed"):
        await tool.apply_plan(None, before, plan, plan["sha256"])


@pytest.mark.asyncio
async def test_postconditions_preserve_financial_state_and_route_history(before, plan):
    result = tool.verify_after(before, after_state(before, plan), plan)
    assert result["folio_preserved"] == "363"
    assert result["state_preserved"] == "enviado"
    assert result["configuration_assignments"] == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "money",
        "folio",
        "organizational_member",
        "other_route",
        "wrong_gn",
        "wrong_rule",
        "old_approval",
        "second_audit",
        "audit_hash",
        "position_label",
        "missing_assignment",
    ],
)
async def test_unexpected_after_effects_are_not_accepted(before, plan, change):
    after = after_state(before, plan)
    if change == "money":
        after["document"][0]["monto_total"] = "999.00"
    elif change == "folio":
        after["document"][0]["referencia_operaciones"] = ""
    elif change == "organizational_member":
        after["assignments"][1]["empleado_id"] = tool.GN
    elif change == "other_route":
        after["routes"][1]["eligible_empleado_ids"] = [tool.GN]
    elif change == "wrong_gn":
        after["routes"][0]["eligible_empleado_ids"] = [tool.GV]
    elif change == "wrong_rule":
        after["rules"][0]["eligible_position_keys"] = [tool.GV_POSITION]
    elif change == "old_approval":
        after["approvals"][0]["comentario"] = "rewritten"
    elif change == "second_audit":
        after["approvals"].append(dict(after["approvals"][-1], id=str(uuid4())))
    elif change == "audit_hash":
        after["approvals"][-1]["comentario"] = json.dumps({"plan_sha256": "wrong"})
    elif change == "position_label":
        after["positions"][-1]["label"] = "changed"
    else:
        after["assignments"].pop()
    with pytest.raises(ValueError):
        tool.verify_after(before, after, plan)


def test_private_artifacts_and_explicit_postgresql_connection(tmp_path, monkeypatch):
    directory = tmp_path / "evidence"
    tool.save_json(directory, "plan.json", {"ok": True})
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "plan.json").stat().st_mode & 0o777 == 0o600
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="explicitly selected"):
        tool.database_url(argparse.Namespace(env_file=None))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///wrong")
    with pytest.raises(ValueError, match="PostgreSQL"):
        tool.database_url(argparse.Namespace(env_file=None))
    monkeypatch.setenv("DATABASE_URL", "postgresql://dummy:password@localhost/test")
    assert tool.database_url(argparse.Namespace(env_file=None)).startswith(
        "postgresql+asyncpg://"
    )


@pytest.mark.asyncio
async def test_nested_owner_commit_is_forbidden():
    async with tool.AtomicPolicySession() as session:
        with pytest.raises(ValueError, match="Nested commits"):
            await session.commit()
