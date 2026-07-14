#!/usr/bin/env python3
"""Run a persistence-free CTT extraction canary on local page images."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import List

from PIL import Image

from devnous.tournaments.core.ctt_canary import (
    CttCanaryMode,
    CttCanaryPolicy,
    CttCanaryRunner,
    ctt_document_sha256,
)
from devnous.tournaments.core.ctt_extraction_cache import (
    CttCachedResponsesExtractor,
    CttDraftCache,
)
from devnous.tournaments.core.ctt_responses_extractor import (
    DEFAULT_CTT_RESPONSES_MODEL,
    CttResponsesExtractor,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pages", nargs="+", type=Path)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["shadow", "active"], default="shadow")
    parser.add_argument(
        "--model",
        default=os.getenv("CTT_RESPONSES_MODEL") or DEFAULT_CTT_RESPONSES_MODEL,
    )
    parser.add_argument("--attempts", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument("--minimum-players", type=int, default=16)
    parser.add_argument("--expected-team-name")
    parser.add_argument(
        "--canonical-input",
        action="store_true",
        help="Treat every page as an already normalized 2550x3300 CTT image.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    if len(args.pages) not in (2, 3):
        raise SystemExit("Provide exactly two or three ordered page images.")
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required.")

    payloads = [path.read_bytes() for path in args.pages]
    document_hash = ctt_document_sha256(payloads)
    images: List[Image.Image] = []
    for path in args.pages:
        with Image.open(path) as image:
            images.append(image.convert("RGB"))
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    extractor = CttResponsesExtractor.from_api_key(
        api_key,
        model=args.model,
        input_images_are_canonical=args.canonical_input,
    )
    cached = CttCachedResponsesExtractor(
        extractor,
        CttDraftCache(args.cache_dir),
        attempts=args.attempts,
    )
    runner = CttCanaryRunner(
        cached,
        mode=CttCanaryMode(args.mode),
        policy=CttCanaryPolicy(
            minimum_players=args.minimum_players,
            expected_team_name=args.expected_team_name,
        ),
    )
    execution = await runner.run(images, layout, document_sha256=document_hash)
    print(execution.report.model_dump_json(indent=2))
    return 0 if execution.report.accepted else 2


def main() -> None:
    args = _parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
