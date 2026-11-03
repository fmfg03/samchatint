"""Pinned National policy permits missing CURP without a fictitious decision."""

import copy
from dataclasses import replace
import json
from uuid import uuid4

import pytest

from devnous.copa_telmex.staff_capture_executor import CaptureDenied
from devnous.copa_telmex.staff_capture_effect import freeze_capture_effect
from devnous.copa_telmex.staff_capture_finality_authorization import (
    verify_capture_finality_authorization,
)
from staff_capture_gate_v2 import StaffPreauthorizationDenied
from test_staff_capture_gate_v2 import ctx as _ctx
from test_staff_capture_finality_v2 import final as _final

ctx = _ctx


def national_policy(ctx, kinds=None):
    """Use fixture-specific source IDs; production pins belong to its artifact."""
    ctx.policy.update(
        policy_version="2.1.0",
        phase_binding=dict(
            phase="NACIONAL",
            tenant_id=ctx.scope.tenant_id,
            edition_id=str(ctx.scope.edition_id),
            manifest_sha256=ctx.scope.manifest_sha256,
        ),
        optional_curp_kinds=["PLAYER", "STAFF"] if kinds is None else kinds,
    )
    apply_policy(ctx)


def apply_policy(ctx):
    ctx.policy["policy_hash"] = ctx.policy_hash()
    ctx.scope = replace(ctx.scope, policy_hash=ctx.policy["policy_hash"])
    ctx.rebind()
    ctx.gate = ctx.make_gate()


def omit_curp(ctx, kinds):
    for index, kind in enumerate(("PLAYER", "STAFF")):
        if kind in kinds:
            row = (
                ctx.args["players"][0]
                if kind == "PLAYER"
                else ctx.args["staff"][0]["row"]
            )
            row["curp"] = None
            ctx.evidence["members"][index]["fields"].pop("curp", None)
    ctx.rebind()


@pytest.mark.parametrize("kinds", [("PLAYER",), ("STAFF",), ("PLAYER", "STAFF")])
def test_missing_curp_stays_null_without_a_field_decision(ctx, kinds):
    national_policy(ctx)
    omit_curp(ctx, kinds)
    outcome = ctx.preauthorize()
    pending = json.loads(ctx.final_effect(outcome))
    for kind in ("PLAYER", "STAFF"):
        decisions = outcome[
            "player_field_decisions" if kind == "PLAYER" else "staff_field_decisions"
        ]
        assert [decision["field"] for decision in decisions] == (
            ["full_name", "birth_date"]
            if kind in kinds
            else ["full_name", "birth_date", "curp"]
        )
        row = pending["players"][0] if kind == "PLAYER" else pending["staff"][0]["row"]
        assert (row["curp"] is None) is (kind in kinds)
    assert json.loads(ctx.authorize(outcome))["body"]["authority_metadata"]


def test_null_curp_reaches_separate_signed_finality_capability(ctx):
    national_policy(ctx)
    omit_curp(ctx, ("PLAYER", "STAFF"))
    final = _final.__wrapped__(ctx)
    assert verify_capture_finality_authorization(**final["verify"]).startswith(
        "sha256:"
    )
    assert final["active"]["players"][0]["curp"] is None
    assert final["active"]["staff"][0]["row"]["curp"] is None


@pytest.mark.parametrize("kind", ["PLAYER", "STAFF"])
def test_kind_not_explicitly_optional_remains_required(ctx, kind):
    national_policy(ctx, kinds=["STAFF"] if kind == "PLAYER" else ["PLAYER"])
    omit_curp(ctx, [kind])
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0", "2.1.0", "3.0.0"])
def test_versions_without_extensions_keep_strict_three_field_contract(ctx, version):
    ctx.policy["policy_version"] = version
    apply_policy(ctx)
    omit_curp(ctx, ("PLAYER", "STAFF"))
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


@pytest.mark.parametrize("change", ["tenant", "edition", "manifest"])
def test_national_binding_must_match_signed_scope_before_publication(ctx, change):
    national_policy(ctx)
    phase = ctx.policy["phase_binding"]
    if change == "tenant":
        phase["tenant_id"] = "other-tenant"
    elif change == "edition":
        phase["edition_id"] = str(uuid4())
    else:
        phase["manifest_sha256"] = "0" * 64
    apply_policy(ctx)
    omit_curp(ctx, ("PLAYER", "STAFF"))
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "phase",
        "phase_type",
        "phase_missing",
        "phase_extra",
        "phase_null",
        "tenant_type",
        "tenant_format",
        "edition_type",
        "edition_upper",
        "edition_bad",
        "manifest_type",
        "manifest_prefix",
        "manifest_upper",
        "manifest_short",
        "kinds_null",
        "kinds_empty",
        "kinds_string",
        "kinds_unknown",
        "kinds_type",
        "kinds_order",
        "kinds_duplicate",
        "no_phase",
        "no_kinds",
        "hash",
    ],
)
def test_invalid_or_incomplete_national_profile_is_denied(ctx, change):
    national_policy(ctx)
    phase = ctx.policy["phase_binding"]
    if change == "version":
        ctx.policy["policy_version"] = "2.0.0"
    elif change == "phase":
        phase["phase"] = "ESTATAL"
    elif change == "phase_type":
        phase["phase"] = 1
    elif change == "phase_missing":
        del phase["phase"]
    elif change == "phase_extra":
        phase["scope"] = "any"
    elif change == "phase_null":
        ctx.policy["phase_binding"] = None
    elif change == "tenant_type":
        phase["tenant_id"] = 1
    elif change == "tenant_format":
        phase["tenant_id"] = "../prod"
    elif change == "edition_type":
        phase["edition_id"] = uuid4()
    elif change == "edition_upper":
        phase["edition_id"] = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
    elif change == "edition_bad":
        phase["edition_id"] = "not-an-edition"
    elif change == "manifest_type":
        phase["manifest_sha256"] = False
    elif change == "manifest_prefix":
        phase["manifest_sha256"] = "sha256:" + "0" * 64
    elif change == "manifest_upper":
        phase["manifest_sha256"] = "A" * 64
    elif change == "manifest_short":
        phase["manifest_sha256"] = "0" * 63
    elif change == "kinds_null":
        ctx.policy["optional_curp_kinds"] = None
    elif change == "kinds_empty":
        ctx.policy["optional_curp_kinds"] = []
    elif change == "kinds_string":
        ctx.policy["optional_curp_kinds"] = "PLAYER"
    elif change == "kinds_unknown":
        ctx.policy["optional_curp_kinds"] = ["COACH"]
    elif change == "kinds_type":
        ctx.policy["optional_curp_kinds"] = [True]
    elif change == "kinds_order":
        ctx.policy["optional_curp_kinds"] = ["STAFF", "PLAYER"]
    elif change == "kinds_duplicate":
        ctx.policy["optional_curp_kinds"] = ["PLAYER", "PLAYER"]
    elif change == "no_phase":
        del ctx.policy["phase_binding"]
    elif change == "no_kinds":
        del ctx.policy["optional_curp_kinds"]
    if change == "edition_type":
        # Even a UUID object carrying the right identity violates the JSON contract.
        ctx.policy["policy_hash"] = "sha256:" + "0" * 64
    else:
        ctx.policy["policy_hash"] = (
            ctx.policy_hash() if change != "hash" else "sha256:" + "0" * 64
        )
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.make_gate()


@pytest.mark.parametrize("kind", ["PLAYER", "STAFF"])
@pytest.mark.parametrize("value", ["", " ", "short", "0" * 19, "0" * 17 + "!"])
def test_supplied_curp_cannot_use_null_exception_or_skip_validation(ctx, kind, value):
    national_policy(ctx)
    index = 0 if kind == "PLAYER" else 1
    row = ctx.args["players"][0] if index == 0 else ctx.args["staff"][0]["row"]
    row["curp"] = value
    # Oversize values already fail the canonical column wire before signing;
    # representable malformed values must also fail the independent gate.
    with pytest.raises((StaffPreauthorizationDenied, CaptureDenied)):
        ctx.rebind()
        ctx.preauthorize()
    assert ctx.published == 0


@pytest.mark.parametrize("kind", ["PLAYER", "STAFF"])
@pytest.mark.parametrize(
    "change",
    [
        "extra_curp_geometry",
        "present_curp_no_geometry",
        "missing_name",
        "missing_birth_date",
    ],
)
def test_evidence_exactly_matches_provided_and_required_fields(ctx, kind, change):
    national_policy(ctx)
    index = 0 if kind == "PLAYER" else 1
    row = ctx.args["players"][0] if index == 0 else ctx.args["staff"][0]["row"]
    fields = ctx.evidence["members"][index]["fields"]
    geometry = copy.deepcopy(fields["curp"])
    if change == "present_curp_no_geometry":
        del fields["curp"]
    else:
        omit_curp(ctx, [kind])
        if change == "extra_curp_geometry":
            fields["curp"] = geometry
        elif change == "missing_name":
            row.update(first_name="", last_name="")
        elif change == "missing_birth_date":
            row["birth_date"] = None
    ctx.rebind()
    with pytest.raises(StaffPreauthorizationDenied):
        ctx.preauthorize()
    assert ctx.published == 0


@pytest.mark.parametrize(
    "stage", ["pending_signature", "finalize_rows", "finality_signature"]
)
def test_each_authoritative_verification_rechecks_national_scope_pin(ctx, stage):
    national_policy(ctx)
    omit_curp(ctx, ("PLAYER", "STAFF"))
    final = _final.__wrapped__(ctx)
    count = ctx.published
    # Simulate a stale in-process policy binding, keeping all signatures and
    # row bytes unchanged, to isolate the check in every authoritative path.
    ctx.gate._policy["phase_binding"]["manifest_sha256"] = "0" * 64
    if stage == "pending_signature":
        action = ctx.authorize
        args = {"result": final["args"]["preauthorization"]}
    elif stage == "finalize_rows":
        action = ctx.gate.finalize_rows
        args = dict(final["args"], observed_pending_rows=final["observed"])
    else:
        action = ctx.gate.authorize_finality_effect
        args = final["signing"]
    with pytest.raises((StaffPreauthorizationDenied, CaptureDenied)):
        action(**args)
    assert ctx.published == count


def test_director_and_auxiliary_without_curp_receive_only_real_field_decisions(ctx):
    auxiliary = copy.deepcopy(ctx.args["staff"][0]["row"])
    auxiliary.update(id=str(uuid4()), staff_slot=2, role="auxiliar", curp=None)
    ctx.args["staff"][1].update(presence="PRESENT", row=auxiliary)
    evidence = ctx.evidence["members"][2]
    geometry = evidence["absence_geometry"]
    evidence.update(
        row_id=auxiliary["id"],
        presence="PRESENT",
        absence_geometry=None,
        fields={name: copy.deepcopy(geometry) for name in ("full_name", "birth_date")},
    )

    def complete_effect(outcome):
        args = copy.deepcopy(ctx.args)
        for row in [args["team"]] + args["players"]:
            row.update(ctx.generated[row["id"]])
        for row in args["players"] + [item["row"] for item in args["staff"]]:
            row.update(outcome["authority_metadata"][row["id"]])
        return freeze_capture_effect(**args)

    ctx.final_effect = complete_effect
    ctx.policy["required_staff_slots"] = [1, 2]
    national_policy(ctx)
    omit_curp(ctx, ("PLAYER", "STAFF"))
    final = _final.__wrapped__(ctx)
    assert verify_capture_finality_authorization(**final["verify"]).startswith(
        "sha256:"
    )
    assert all(item["row"]["curp"] is None for item in final["active"]["staff"])
    decisions = final["args"]["preauthorization"]["staff_field_decisions"]
    assert len(decisions) == 4
    assert {item["slot"] for item in decisions} == {1, 2}
    assert {item["field"] for item in decisions} == {"full_name", "birth_date"}
