"""Explicit destination grants, immutable policy provenance and legacy boundaries."""

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from devnous.gastos.services import amex_direct_account_treatment_service as owner
from devnous.gastos.services.amex_direct_common import (
    DirectAccessError,
    DirectConflict,
    base_context_config,
    context_config_matches,
    digest,
    item_hash,
)


class Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


def config_world():
    actor, line, version, concept, debtor = [uuid4() for _ in range(5)]
    scope = {
        "actor_id": str(actor),
        "tournament_ids": [str(uuid4())],
        "edition_years": [2026],
        "project_codes": ["ADMIN"],
        "benefit_budget_line_ids": [str(line)],
        "benefit_budget_pairs": [
            {"budget_version_id": str(version), "budget_concept_id": str(concept)}
        ],
        "partner_debtor_account_ids": [str(debtor)],
    }
    config = {
        "entity_code": "PS",
        "company_code": "PS",
        "receiver_rfc": "ABC010101AB1",
        "card_account_ids": [str(uuid4())],
        "actor_scopes": [scope],
        "budget_actor_ids": [str(actor)],
        "edition_years": [2026],
        "tournament_ids": list(scope["tournament_ids"]),
        "project_codes": ["ADMIN"],
        "benefit_budget_line_ids": [str(line)],
        "benefit_budget_pairs": list(scope["benefit_budget_pairs"]),
        "partner_debtor_account_ids": [str(debtor)],
    }
    return NS(
        actor=actor,
        line=line,
        version=version,
        concept=concept,
        debtor=debtor,
        config=config,
        scope=scope,
    )


def test_base_projection_strips_only_the_three_additive_keys_without_mutation():
    w = config_world()
    original = json.loads(json.dumps(w.config))
    base = base_context_config(w.config)
    assert w.config == original
    assert set(base) == set(original) - {
        "benefit_budget_line_ids",
        "benefit_budget_pairs",
        "partner_debtor_account_ids",
    }
    assert base["budget_actor_ids"] == original["budget_actor_ids"]
    assert (
        base["actor_scopes"][0]["tournament_ids"]
        == original["actor_scopes"][0]["tournament_ids"]
    )
    assert "partner_debtor_account_ids" not in base["actor_scopes"][0]


@pytest.mark.parametrize(
    "key",
    [
        "company_code",
        "receiver_rfc",
        "entity_code",
        "card_account_ids",
        "budget_actor_ids",
        "edition_years",
        "project_codes",
        "tournament_ids",
    ],
)
def test_any_old_context_field_change_is_not_grant_compatibility(key):
    w = config_world()
    old_hash = digest(base_context_config(w.config))
    w.config[key] = [str(uuid4())] if isinstance(w.config[key], list) else "CHANGED"
    assert not context_config_matches(old_hash, w.config)


def test_old_actor_scope_changes_are_not_grant_compatibility():
    w = config_world()
    old_hash = digest(base_context_config(w.config))
    w.scope["tournament_ids"] = [str(uuid4())]
    assert not context_config_matches(old_hash, w.config)


def test_benefit_grants_intersect_actual_actor_and_context():
    w = config_world()
    assert owner.benefit_line_allowed(
        w.config,
        w.actor,
        {"id": w.line, "budget_version_id": uuid4(), "budget_concept_id": uuid4()},
    )
    w.scope["benefit_budget_line_ids"] = []
    assert not owner.benefit_line_allowed(
        w.config,
        w.actor,
        {"id": w.line, "budget_version_id": uuid4(), "budget_concept_id": uuid4()},
    )
    assert owner.benefit_line_allowed(
        w.config,
        w.actor,
        {"id": uuid4(), "budget_version_id": w.version, "budget_concept_id": w.concept},
    )


@pytest.mark.parametrize("side", ["actor", "context"])
def test_missing_grant_on_either_side_is_not_global_authority(side):
    w = config_world()
    target = w.scope if side == "actor" else w.config
    target.pop("benefit_budget_line_ids")
    target.pop("benefit_budget_pairs")
    with pytest.raises(DirectAccessError, match="benefit_budget_catalog_unconfigured"):
        owner.benefit_grants(w.config, w.actor)
    assert owner.debtor_account_ids(w.config, w.actor) == {str(w.debtor)}


def test_debtor_catalog_does_not_infer_accounts_from_unconfigured_context():
    w = config_world()
    w.scope["partner_debtor_account_ids"] = [str(uuid4())]
    assert owner.debtor_account_ids(w.config, w.actor) == set()
    with pytest.raises(DirectAccessError, match="actor_scope_denied"):
        owner.debtor_account_ids(w.config, uuid4())


def test_legacy_item_without_head_keeps_project_contract_and_hash():
    item = NS(id=uuid4(), import_id=uuid4())
    before = item_hash(item)
    assert owner.treatment_kind(item, {}) == "project_expense"
    item.account_treatment_decision_id = None
    assert item_hash(item) == before


def test_revoked_destination_is_not_a_legacy_project_fallback():
    item = NS(id=uuid4(), import_id=uuid4(), account_treatment_decision_id=uuid4())
    decision = NS(
        id=item.account_treatment_decision_id,
        item_id=item.id,
        import_id=item.import_id,
        action="revoke",
        treatment="partner_receivable",
    )
    assert owner.treatment_kind(item, {decision.id: decision}) == "unclassified"


@pytest.mark.parametrize("bad_field", ["item_id", "import_id", "action", "treatment"])
def test_cross_source_or_unknown_decision_head_fails_closed(bad_field):
    item = NS(id=uuid4(), import_id=uuid4(), account_treatment_decision_id=uuid4())
    decision = NS(
        id=item.account_treatment_decision_id,
        item_id=item.id,
        import_id=item.import_id,
        action="set",
        treatment="benefit_expense",
    )
    setattr(decision, bad_field, uuid4() if bad_field.endswith("_id") else "unknown")
    with pytest.raises(DirectConflict, match="account_treatment_head_conflict"):
        owner.active_account_treatment(item, {decision.id: decision})


def policy(w):
    return NS(
        id=uuid4(),
        config_hash=digest(w.config),
        base_config_hash=digest(base_context_config(w.config)),
        config_text=json.dumps(
            w.config, sort_keys=True, default=str, separators=(",", ":")
        ),
        base_config_text=json.dumps(
            base_context_config(w.config),
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ),
        revision=1,
    )


@pytest.mark.asyncio
async def test_grant_only_json_without_owner_policy_is_never_authority():
    w = config_world()
    old_hash = digest(base_context_config(w.config))
    assert context_config_matches(old_hash, w.config)
    session = NS(execute=AsyncMock(return_value=Result(None)))
    with pytest.raises(DirectConflict, match="account_treatment_policy_unconfigured"):
        await owner.policy_for_config(session, w.config, NS(config_hash=old_hash))


@pytest.mark.asyncio
async def test_installed_current_policy_binds_exact_full_and_base_config():
    w = config_world()
    installed = policy(w)
    session = NS(execute=AsyncMock(return_value=Result(installed)))
    assert (
        await owner.policy_for_config(
            session, w.config, NS(config_hash=installed.base_config_hash)
        )
        is installed
    )
    assert session.execute.await_count == 1
    installed.config_text = installed.config_text.replace('"PS"', '"FORGED"')
    with pytest.raises(DirectConflict, match="account_treatment_policy_unconfigured"):
        await owner.policy_for_config(session, w.config)


@pytest.mark.asyncio
async def test_installed_policy_does_not_admit_an_unanchored_old_context_hash():
    w = config_world()
    installed = policy(w)
    session = NS(execute=AsyncMock(side_effect=[Result(installed), Result(None)]))
    with pytest.raises(DirectConflict, match="authorized_context_changed"):
        await owner.policy_for_config(session, w.config, NS(config_hash="f" * 64))


@pytest.mark.asyncio
async def test_catalog_configuration_changes_invalidate_existing_policy():
    w = config_world()
    installed = policy(w)
    w.config["partner_debtor_account_ids"].append(str(uuid4()))
    session = NS(execute=AsyncMock(return_value=Result(installed)))
    with pytest.raises(DirectConflict, match="account_treatment_policy_unconfigured"):
        await owner.policy_for_config(session, w.config)


@pytest.mark.asyncio
async def test_options_never_read_account_catalog_without_installed_policy(monkeypatch):
    w = config_world()
    session = NS(execute=AsyncMock(return_value=Result(None)))
    monkeypatch.setattr(
        owner,
        "load_statement",
        AsyncMock(
            return_value=(None, None, NS(config_hash=digest(w.config)), w.config)
        ),
    )
    with pytest.raises(DirectConflict, match="account_treatment_policy_unconfigured"):
        await owner.list_direct_account_treatment_options(
            session, actor_id=w.actor, import_id=uuid4()
        )
    assert session.execute.await_count == 1
    assert "amex_direct_account_treatment_policies" in str(
        session.execute.await_args.args[0]
    )
