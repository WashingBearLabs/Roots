#!/usr/bin/env python3
"""Research Assistant Demo — fork/join parallelism and checkpoint approval."""

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
from agents import (  # type: ignore[import-untyped]
    search_academic,
    search_news,
    search_web,
    summarize_results,
)

STATIC_DIR = str(DEMO_DIR / "static")
PROCESS_YAML = str(DEMO_DIR / "process.yaml")


async def setup() -> Roots:
    backend = SqliteBackend(":memory:")
    await backend.initialize()

    app = Roots(storage=backend)
    await app.__aenter__()

    await app.load_process(PROCESS_YAML)
    await app.register_agent("search_academic", search_academic)
    await app.register_agent("search_news", search_news)
    await app.register_agent("search_web", search_web)
    await app.register_agent("summarize_results", summarize_results)

    return app


def main() -> None:
    app = asyncio.run(setup())
    run_demo(app, "Research Assistant", STATIC_DIR, port=8202)


if __name__ == "__main__":
    main()
