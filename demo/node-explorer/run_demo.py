#!/usr/bin/env python3
"""Node Explorer Demo — step-through tour of every Roots node type.

Provides step, reset, and tutorial endpoints for the guided tour.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running from repo root or demo directory
DEMO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DEMO_DIR.parents[1]))
sys.path.insert(0, str(DEMO_DIR))

from roots import Roots
from roots.storage.sqlite import SqliteBackend

from demo._common.demo_server import create_demo_app, open_browser
from agents import (  # type: ignore[import-untyped]
    classify_item,
    check_format,
    validate_schema,
    analyze_content_quality,
    analyze_content_deep,
    analyze_metadata_deep,
)
from server_extensions import add_node_explorer_routes  # type: ignore[import-untyped]

import uvicorn

STATIC_DIR = str(DEMO_DIR / "static")
PROCESS_YAML = str(DEMO_DIR / "process.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Node Explorer Demo")
    parser.add_argument("--port", type=int, default=8205,
                        help="Server port (default: 8205)")
    return parser.parse_args()


async def setup() -> Roots:
    backend = SqliteBackend(":memory:")
    await backend.initialize()

    app = Roots(storage=backend)
    await app.__aenter__()

    await app.load_process(PROCESS_YAML)
    await app.register_agent("classify_item", classify_item)
    await app.register_agent("check_format", check_format)
    await app.register_agent("validate_schema", validate_schema)
    await app.register_agent("analyze_content_quality", analyze_content_quality)
    await app.register_agent("analyze_content_deep", analyze_content_deep)
    await app.register_agent("analyze_metadata_deep", analyze_metadata_deep)

    return app


def main() -> None:
    args = parse_args()
    port = args.port

    roots = asyncio.run(setup())
    app = create_demo_app(roots, "Node Explorer", STATIC_DIR)

    # Add step/reset/tutorial endpoints
    add_node_explorer_routes(app)

    open_browser(port)
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
