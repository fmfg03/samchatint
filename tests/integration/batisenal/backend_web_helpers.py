import base64
import io
from uuid import uuid4

from PIL import Image, PngImagePlugin


async def login(client, who="alice"):
    response = await client.get("/fixture/login/" + who)
    client._backend_fixture_actor = who
    client.headers.update(
        {
            "Origin": str(client.base_url).rstrip("/"),
            "X-Batisenal-CSRF": "synthetic-csrf-000000000000000000000000000000000000",
        }
    )
    return response


def payload(**extra):
    return dict(
        organization="acme",
        project="demo",
        request_key=str(uuid4()),
        confirmed=True,
        title="Ayuda sintética",
        description="Error al guardar",
        context={"module": "demo"},
        **extra,
    )


async def create(client, data=None):
    from backend_harness import IDS

    from devnous.gastos.services import batisenal_service as owner

    app = client._transport.app
    principal = owner.Principal(
        IDS[getattr(client, "_backend_fixture_actor", "alice")],
        frozenset({("acme", "demo", "request")}),
    )
    async with app.state.maker() as session:
        identifier = await owner.create_case(
            session, principal, data or payload(), owner.Policy()
        )
        await session.commit()
        return identifier


def png():
    image = Image.new("RGB", (80, 60), "blue")
    stream = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("secret", "PRIVATE METADATA")
    image.save(stream, format="PNG", pnginfo=info)
    return base64.b64encode(stream.getvalue() + b"PRIVATE TRAILING BYTES").decode()
