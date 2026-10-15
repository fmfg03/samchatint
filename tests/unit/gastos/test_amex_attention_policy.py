"""Explicit beneficiary identity and fail-visible attention acceptance."""

from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from devnous.gastos.services.amex_attention_policy import (
    EXCLUDED_EMPLOYEE_IDS,
    AttentionPolicy,
    classify_amex_attention,
    validate_attention_policy,
)

ODILON = UUID(EXCLUDED_EMPLOYEE_IDS[0])
POLICY = AttentionPolicy(True, frozenset({ODILON}))


def row(employee=None, provider=None, **kwargs):
    return NS(
        beneficiario_empleado_id=employee,
        beneficiario_proveedor_cliente_id=provider,
        **kwargs,
    )


@pytest.mark.parametrize(
    "document,account,count,visible,reason",
    [
        (row(ODILON, empleado_id=uuid4()), row(ODILON), 1, False, "by_account"),
        (row(uuid4(), empleado_id=ODILON), None, 1, True, "normal"),
        (row(), row(ODILON), 1, False, "by_account"),
        (row(ODILON), row(uuid4()), 1, True, "conflict"),
        (row(), row(), 1, True, "missing_beneficiary"),
        (row(ODILON, uuid4()), row(ODILON), 1, True, "ambiguous_beneficiary"),
        (row(ODILON), row(provider=uuid4()), 1, True, "conflict"),
        (row(provider=uuid4()), row(ODILON), 1, True, "conflict"),
        (row("invalid"), None, 1, True, "ambiguous_beneficiary"),
        (row(ODILON), row(ODILON), 2, True, "multiple_or_missing_informes"),
        (None, row(ODILON), 0, True, "multiple_or_missing_informes"),
        (
            row(beneficiario_alterno_tipo="proveedor"),
            row(ODILON),
            1,
            True,
            "ambiguous_beneficiary",
        ),
    ],
)
def test_only_explicit_unambiguous_beneficiary_can_be_excluded(
    document, account, count, visible, reason
):
    result = classify_amex_attention(
        document, account, candidate_count=count, policy=POLICY
    )
    assert result.visible is visible
    assert result.reason.endswith(reason)


@pytest.mark.parametrize(
    "configured,existing",
    [
        (["invalid"], [ODILON]),
        ([ODILON], []),
        ([], [ODILON]),
        ([None], [ODILON]),
    ],
)
def test_invalid_configuration_never_hides(configured, existing):
    policy = validate_attention_policy(configured, existing)
    assert not policy.valid and policy.warnings
    assert classify_amex_attention(row(ODILON), policy=policy).visible


def test_valid_configuration_is_uuid_normalized():
    assert validate_attention_policy([str(ODILON)], [ODILON]) == POLICY


@pytest.mark.parametrize("alternate", ["caja_chica", "proveedor", "unknown"])
def test_employee_and_alternate_discriminator_conflict_stays_visible(alternate):
    result = classify_amex_attention(
        row(ODILON, beneficiario_alterno_tipo=alternate), row(ODILON), policy=POLICY
    )
    assert result.visible and result.reason == "amex_attention_ambiguous_beneficiary"


def test_unambiguous_provider_is_visible_without_ambiguous_label():
    provider = uuid4()
    assert (
        classify_amex_attention(
            row(provider=provider), row(provider=provider), policy=POLICY
        ).reason
        == "amex_attention_provider"
    )
    assert (
        classify_amex_attention(row(provider=provider), row(), policy=POLICY).reason
        == "amex_attention_provider"
    )
    assert (
        classify_amex_attention(
            row(provider=provider), row(provider=uuid4()), policy=POLICY
        ).reason
        == "amex_attention_beneficiary_conflict"
    )


LINKED_PROVIDER = UUID("c79ed5bd-4dec-4343-908d-d714937eaa50")


def test_linked_employee_and_provider_are_one_explicit_beneficiary():
    result = classify_amex_attention(
        row(ODILON, LINKED_PROVIDER),
        row(ODILON, LINKED_PROVIDER),
        policy=POLICY,
        provider_employee_ids={LINKED_PROVIDER: ODILON},
    )
    assert result.visible is False
    assert result.beneficiary_id == ODILON
    assert result.reason == "amex_attention_by_account"


@pytest.mark.parametrize(
    "document,account,links,count,reason",
    [
        (row(ODILON, LINKED_PROVIDER), row(ODILON), {}, 1, "ambiguous_beneficiary"),
        (
            row(ODILON, LINKED_PROVIDER),
            row(ODILON),
            {LINKED_PROVIDER: uuid4()},
            1,
            "ambiguous_beneficiary",
        ),
        (
            row(ODILON, LINKED_PROVIDER),
            row(ODILON),
            {LINKED_PROVIDER: None},
            1,
            "ambiguous_beneficiary",
        ),
        (
            row(ODILON, LINKED_PROVIDER),
            row(ODILON),
            {LINKED_PROVIDER: "invalid"},
            1,
            "ambiguous_beneficiary",
        ),
        (
            row(ODILON, LINKED_PROVIDER),
            row(ODILON),
            {uuid4(): ODILON},
            1,
            "ambiguous_beneficiary",
        ),
        (
            row(ODILON, LINKED_PROVIDER, beneficiario_alterno_tipo="proveedor"),
            row(ODILON),
            {LINKED_PROVIDER: ODILON},
            1,
            "ambiguous_beneficiary",
        ),
        (
            row(ODILON, LINKED_PROVIDER),
            row(uuid4()),
            {LINKED_PROVIDER: ODILON},
            1,
            "beneficiary_conflict",
        ),
        (
            row(ODILON, LINKED_PROVIDER),
            row(ODILON),
            {LINKED_PROVIDER: ODILON},
            2,
            "multiple_or_missing_informes",
        ),
        (
            None,
            row(ODILON, LINKED_PROVIDER),
            {LINKED_PROVIDER: ODILON},
            0,
            "multiple_or_missing_informes",
        ),
        (
            row(provider=LINKED_PROVIDER),
            row(provider=LINKED_PROVIDER),
            {LINKED_PROVIDER: ODILON},
            1,
            "provider",
        ),
        (
            row(ODILON, "invalid"),
            row(ODILON),
            {LINKED_PROVIDER: ODILON},
            1,
            "ambiguous_beneficiary",
        ),
    ],
)
def test_provider_link_cannot_hide_ambiguous_or_conflicting_beneficiaries(
    document, account, links, count, reason
):
    result = classify_amex_attention(
        document,
        account,
        candidate_count=count,
        policy=POLICY,
        provider_employee_ids=links,
    )
    assert result.visible is True
    assert result.reason == f"amex_attention_{reason}"


@pytest.mark.parametrize(
    "document,account",
    [
        (row(ODILON, LINKED_PROVIDER), row(ODILON)),
        (row(ODILON), row(ODILON, LINKED_PROVIDER)),
        (row(), row(ODILON, LINKED_PROVIDER)),
    ],
)
def test_linked_dual_identity_remains_compatible_with_same_employee(document, account):
    assert not classify_amex_attention(
        document,
        account,
        policy=POLICY,
        provider_employee_ids={LINKED_PROVIDER: ODILON},
    ).visible


def test_linked_different_employee_keeps_normal_attention():
    other = uuid4()
    result = classify_amex_attention(
        row(other, LINKED_PROVIDER),
        row(other),
        policy=POLICY,
        provider_employee_ids={LINKED_PROVIDER: other},
    )
    assert result.visible is True
    assert result.beneficiary_id == other
    assert result.reason == "amex_attention_normal"
