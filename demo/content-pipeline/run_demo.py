#!/usr/bin/env python3
"""Content Pipeline Demo — agent pools and deterministic decisions."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running from repo root or demo directory
DEMO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DEMO_DIR.parents[1]))
sys.path.insert(0, str(DEMO_DIR))

from roots import Roots
from roots.storage.sqlite import SqliteBackend

from demo._common.demo_server import run_demo
from agents import (  # type: ignore[import-untyped]  # local module
    analyze_sentiment,
    classify_content,
    detect_spam,
    score_toxicity,
)

STATIC_DIR = str(DEMO_DIR / "static")
PROCESS_YAML = str(DEMO_DIR / "process.yaml")


async def setup() -> Roots:
    backend = SqliteBackend(":memory:")
    await backend.initialize()

    app = Roots(storage=backend)
    await app.__aenter__()

    await app.load_process(PROCESS_YAML)
    await app.register_agent("classify_content", classify_content)
    await app.register_agent("sentiment_analyzer", analyze_sentiment)
    await app.register_agent("toxicity_scorer", score_toxicity)
    await app.register_agent("spam_detector", detect_spam)

    return app


def main() -> None:
    app = asyncio.run(setup())
    run_demo(app, "Content Pipeline", STATIC_DIR, port=8201)


if __name__ == "__main__":
    main()
