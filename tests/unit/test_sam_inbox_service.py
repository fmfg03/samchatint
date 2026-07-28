from __future__ import annotations

from types import SimpleNamespace

import pytest

from samchat.sam_inbox import service


async def _empty_finance_source(*args, **kwargs):
    return {}


def _empty_finance_platform(*args, **kwargs):
    return {}


async def _empty_pending_payments(*args, **kwargs):
    return {"documentos": []}


async def _empty_cfdi_overview(*args, **kwargs):
    return {"pending_expenses": [], "unlinked_cfdis": []}


def _patch_base_sources(monkeypatch):
    monkeypatch.setattr(service, "build_finance_source_snapshot", _empty_finance_source)
    monkeypatch.setattr(
        service, "build_finance_platform_snapshot", _empty_finance_platform
    )
    monkeypatch.setattr(
        service, "get_pending_document_payment_overview", _empty_pending_payments
    )
    monkeypatch.setattr(service, "get_cfdi_matching_overview", _empty_cfdi_overview)


@pytest.mark.asyncio
async def test_sam_inbox_skips_unlinked_local_tournament(monkeypatch):
    _patch_base_sources(monkeypatch)
    tournament = SimpleNamespace(
        id="b708ddc1-2463-4858-9cfd-09b2afa52e52",
        name="Becarios Telmex (Mexico Siglo XXI)",
    )

    async def load_tournaments(*args, **kwargs):
        return [tournament]

    async def load_links(*args, **kwargs):
        return {}

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("local tournament id must not be used as Supabase slug")

    monkeypatch.setattr(service, "_load_active_tournaments", load_tournaments)
    monkeypatch.setattr(service, "_load_tournament_operations_links", load_links)
    monkeypatch.setattr(service, "build_tournament_soul_snapshot", fail_if_called)

    payload = await service.build_sam_inbox_payload(
        object(), current_empleado=SimpleNamespace(id="emp-1", rol="operaciones")
    )

    health = payload["source_health"]["tournament_soul"]
    assert health["ok"] is True
    assert health["linked_tournaments"] == 0
    assert health["skipped_unlinked"] == 1
    assert "No Supabase tournament matched" not in str(health)


@pytest.mark.asyncio
async def test_sam_inbox_uses_operations_slug_for_linked_tournament(monkeypatch):
    _patch_base_sources(monkeypatch)
    called_slugs: list[str] = []
    tournament = SimpleNamespace(id="local-tournament-id", name="Liga local")
    link = SimpleNamespace(
        tournament_id="local-tournament-id",
        operations_tournament_slug="liga-telmex-2026",
    )

    async def load_tournaments(*args, **kwargs):
        return [tournament]

    async def load_links(*args, **kwargs):
        return {"local-tournament-id": link}

    async def snapshot(*args, **kwargs):
        called_slugs.append(kwargs["tournament_slug"])
        return {
            "soul": {
                "tournament": {"id": "remote-tournament-id", "name": "Liga Telmex"},
                "pending_actions": ["Revisar documentos pendientes."],
                "risks": [],
            }
        }

    monkeypatch.setattr(service, "_load_active_tournaments", load_tournaments)
    monkeypatch.setattr(service, "_load_tournament_operations_links", load_links)
    monkeypatch.setattr(service, "build_tournament_soul_snapshot", snapshot)

    payload = await service.build_sam_inbox_payload(
        object(), current_empleado=SimpleNamespace(id="emp-1", rol="operaciones")
    )

    health = payload["source_health"]["tournament_soul"]
    assert called_slugs == ["liga-telmex-2026"]
    assert health["ok"] is True
    assert health["linked_tournaments"] == 1
    assert health["skipped_unlinked"] == 0
    assert payload["all_items"][0]["source_type"] == "tournament_pending"


@pytest.mark.asyncio
async def test_sam_inbox_reports_invalid_operations_slug_without_crashing(monkeypatch):
    _patch_base_sources(monkeypatch)
    tournament = SimpleNamespace(id="local-tournament-id", name="Liga local")
    link = SimpleNamespace(
        tournament_id="local-tournament-id",
        operations_tournament_slug="liga-invalida",
    )

    async def load_tournaments(*args, **kwargs):
        return [tournament]

    async def load_links(*args, **kwargs):
        return {"local-tournament-id": link}

    async def snapshot(*args, **kwargs):
        raise RuntimeError(
            "No Supabase tournament matched tournament_slug=liga-invalida"
        )

    monkeypatch.setattr(service, "_load_active_tournaments", load_tournaments)
    monkeypatch.setattr(service, "_load_tournament_operations_links", load_links)
    monkeypatch.setattr(service, "build_tournament_soul_snapshot", snapshot)

    payload = await service.build_sam_inbox_payload(
        object(), current_empleado=SimpleNamespace(id="emp-1", rol="operaciones")
    )

    health = payload["source_health"]["tournament_soul"]
    assert health["ok"] is False
    assert health["linked_tournaments"] == 1
    assert health["skipped_unlinked"] == 0
    assert "liga-invalida" in health["message"]
    assert payload["all_items"] == []
