from __future__ import annotations

from dataclasses import replace

from samchat.assistant.specialist_benchmarks import build_seed_benchmarks
from samchat.assistant.specialist_task_registry import (
    DISABLED,
    SpecialistTaskRegistration,
    build_specialist_task_registry,
    build_specialist_task_registry_report,
    get_specialist_task_registration,
    route_specialist_task_from_text,
    specialist_task_ids,
    validate_specialist_task_registry,
)


def _seed_task_ids() -> set[str]:
    return {benchmark.task.task_id for benchmark in build_seed_benchmarks()}


def test_specialist_task_registry_matches_seed_benchmarks() -> None:
    registry = build_specialist_task_registry()
    registry_ids = {item.task_id for item in registry}

    assert len(registry) == 10
    assert registry_ids == _seed_task_ids()
    assert set(specialist_task_ids()) == _seed_task_ids()
    assert all(item.status == "enabled" for item in registry)
    assert all(item.required_any for item in registry)
    assert all(item.signals for item in registry)


def test_specialist_task_registry_validation_and_report_are_read_only_inventory() -> None:
    report = build_specialist_task_registry_report(seed_task_ids=_seed_task_ids())
    validation = validate_specialist_task_registry(seed_task_ids=_seed_task_ids())

    assert validation["status"] == "valid"
    assert report["registry_id"] == "samchat_specialist_task_registry_v1"
    assert report["authority"] == "read_only_registry"
    assert report["status"] == "valid"
    assert report["total"] == 10
    assert report["enabled"] == 10
    assert report["disabled"] == 0
    assert len(report["tasks"]) == 10
    assert set(report["routable_task_ids"]) == _seed_task_ids()


def test_specialist_task_registry_get_by_task_id() -> None:
    item = get_specialist_task_registration("samchat-cxc-collection-001")

    assert item is not None
    assert item.task_id == "SAMCHAT-CXC-COLLECTION-001"
    assert item.case_type == "money_request"
    assert "cxc" in item.tags
    assert item.routable is True


def test_specialist_task_registry_routes_existing_natural_prompts() -> None:
    assert (
        route_specialist_task_from_text("prepara la cxc de la factura 669dbf39 contra dcc nacional")
        == "SAMCHAT-CXC-COLLECTION-001"
    )
    assert (
        route_specialist_task_from_text("revisa la comprobacion amex referencia 28 de odilon")
        == "SAMCHAT-FIN-AMEX-001"
    )
    assert (
        route_specialist_task_from_text("armame un borrador para el impuesto sobre hospedaje del hotel de leon")
        == "SAMCHAT-SUPPLIER-HOTEL-001"
    )


def test_specialist_task_registry_fails_closed_on_ambiguous_matches() -> None:
    assert (
        route_specialist_task_from_text("prepara preview de amex referencia 28 y tambien cxc de dcc con factura")
        is None
    )


def test_specialist_task_registry_does_not_route_disabled_tasks() -> None:
    enabled = build_specialist_task_registry()[0]
    disabled = replace(enabled, status=DISABLED)

    assert (
        route_specialist_task_from_text(
            "prepara la cxc de la factura 669dbf39 contra dcc nacional",
            registry=(disabled,),
        )
        is None
    )


def test_specialist_task_registry_validation_detects_seed_mismatch() -> None:
    report = validate_specialist_task_registry(seed_task_ids={"SAMCHAT-MISSING-001"})

    assert report["status"] == "invalid"
    assert report["missing_from_registry"] == ["SAMCHAT-MISSING-001"]
    assert "SAMCHAT-CXC-COLLECTION-001" in report["extra_in_registry"]
