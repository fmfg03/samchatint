from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from PIL import Image
from scripts import run_ctt_canary as cli


def _args(tmp_path, *, pages=2):
    image_paths = []
    for number in range(pages):
        path = tmp_path / f"page-{number + 1}.jpg"
        Image.new("RGB", (24, 32), "white").save(path)
        image_paths.append(path)
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({"pages": {}}), encoding="utf-8")
    return SimpleNamespace(
        pages=image_paths,
        layout=layout,
        cache_dir=tmp_path / "cache",
        mode="shadow",
        model="test-model",
        attempts=1,
        minimum_players=16,
        expected_team_name="Deportivo Estrellas",
        canonical_input=True,
    )


def test_parser_defaults_to_shadow() -> None:
    parser = cli._parser()

    args = parser.parse_args(
        ["--layout", "layout.json", "--cache-dir", "cache", "front.jpg", "back.jpg"]
    )

    assert args.mode == "shadow"
    assert args.minimum_players == 16
    assert args.canonical_input is False


@pytest.mark.asyncio
async def test_run_requires_page_count_and_api_key(tmp_path, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="exactly two or three"):
        await cli._run(_args(tmp_path, pages=1))

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        await cli._run(_args(tmp_path))


@pytest.mark.asyncio
async def test_run_prints_sanitized_report_and_returns_acceptance_code(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    args = _args(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        cli.CttResponsesExtractor,
        "from_api_key",
        lambda *_args, **kwargs: (
            "responses-extractor",
            kwargs["input_images_are_canonical"],
        ),
    )
    monkeypatch.setattr(cli, "CttDraftCache", lambda path: ("cache", path))
    monkeypatch.setattr(
        cli,
        "CttCachedResponsesExtractor",
        lambda extractor, cache, attempts: (extractor, cache, attempts),
    )

    class FakeReport:
        accepted = True

        def model_dump_json(self, *, indent):
            return json.dumps(
                {"accepted": True, "no_database_write": True},
                indent=indent,
            )

    class FakeRunner:
        def __init__(self, extractor, *, mode, policy):
            assert extractor[0][1] is True
            assert extractor[2] == 1
            assert mode.value == "shadow"
            assert policy.expected_team_name == "Deportivo Estrellas"

        async def run(self, images, layout, *, document_sha256):
            assert len(images) == 2
            assert layout == {"pages": {}}
            assert len(document_sha256) == 64
            return SimpleNamespace(report=FakeReport())

    monkeypatch.setattr(cli, "CttCanaryRunner", FakeRunner)

    status = await cli._run(args)

    assert status == 0
    assert json.loads(capsys.readouterr().out)["no_database_write"] is True
