"""Private source reads require the linked employee and upstream authenticated role."""

import base64
import json
from uuid import uuid4

import httpx
import pytest

import devnous.gastos  # noqa: F401 — initialize the owning runtime first
from samchat.client_executive.dossier_directory_private import (
    RepresentativeSourceIdentity,
    read_directory_private,
)


def _token(subject, role="authenticated"):
    claims = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": str(subject), "role": role}).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"test.{claims}.signature"


@pytest.mark.asyncio
async def test_source_personal_details_are_bound_to_source_user_and_only_day_month(
    monkeypatch,
):
    employee, subject, representative = (uuid4() for _ in range(3))
    monkeypatch.setenv(
        "DIRECTION_CTT_DIRECTORY_ANON_KEY", "sb_publishable_dossier_test_"
    )
    identity = RepresentativeSourceIdentity(employee, subject, _token(subject))
    directory = {
        "rows": [
            {
                "representative_id": str(representative),
                "entity": "Oaxaca",
                "representative_name": "Contacto público",
                "zone": "Centro",
            }
        ]
    }
    seen = []

    def api(request):
        seen.append(str(request.url.path))
        assert request.url.host == "api.copatelmextelcel.com.mx"
        assert request.headers["Authorization"] == f"Bearer {identity.access_token}"
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": str(subject)})
        assert request.url.path == "/rest/v1/representative_personal_details"
        assert request.url.params["representative_id"] == f"in.({representative})"
        return httpx.Response(
            200,
            json=[
                {
                    "representative_id": str(representative),
                    "representative_birthday": "02-29",
                    "spouse_name": "Contacto personal",
                    "spouse_birthday": "2005-01-02",
                    "phone": "incorrecto",
                }
            ],
        )

    result = await read_directory_private(
        directory, identity, employee_id=employee, transport=httpx.MockTransport(api)
    )
    assert seen == ["/auth/v1/user", "/rest/v1/representative_personal_details"]
    assert result["status"] == "available"
    assert result["rows"][0]["representative_birthday"] == "29/02"
    assert result["rows"][0]["spouse_birthday"] is None
    assert "phone" not in result["rows"][0]


@pytest.mark.asyncio
async def test_private_directory_denies_service_role_mismatched_employee_and_origin_user(
    monkeypatch,
):
    employee, subject, foreign = (uuid4() for _ in range(3))
    monkeypatch.setenv(
        "DIRECTION_CTT_DIRECTORY_ANON_KEY", "sb_publishable_dossier_test_"
    )
    directory = {"rows": [{"representative_id": str(uuid4()), "entity": "Oaxaca"}]}
    seen = []

    def api(request):
        seen.append(str(request.url.path))
        return httpx.Response(200, json={"id": str(foreign)})

    transport = httpx.MockTransport(api)
    for identity in (
        RepresentativeSourceIdentity(foreign, subject, _token(subject)),
        RepresentativeSourceIdentity(
            employee, subject, _token(subject, "service_role")
        ),
        RepresentativeSourceIdentity(employee, subject, _token(foreign)),
    ):
        actual = await read_directory_private(
            directory, identity, employee_id=employee, transport=transport
        )
        assert actual["status"] == "denied"
        assert actual["rows"] == []
    assert not seen
    entered = await read_directory_private(
        directory,
        RepresentativeSourceIdentity(employee, subject, _token(subject)),
        employee_id=employee,
        transport=transport,
    )
    assert entered["status"] == "denied" and not entered["rows"]
    assert seen == ["/auth/v1/user"]
