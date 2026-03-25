#!/usr/bin/env python3
"""API Explorer Demo — simple echo process with webhook receiver.

Pre-loaded process for experimenting with Roots API endpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

# Allow running from repo root or demo directory
DEMO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DEMO_DIR.parents[1]))
sys.path.insert(0, str(DEMO_DIR))

from roots import Roots
from roots.storage.sqlite import SqliteBackend

from demo._common.demo_server import create_demo_app, open_browser
from agents import echo_agent  # type: ignore[import-untyped]

import uvicorn
from fastapi import Request

STATIC_DIR = str(DEMO_DIR / "static")
PROCESS_YAML = str(DEMO_DIR / "process.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="API Explorer Demo")
    parser.add_argument("--port", type=int, default=8204,
                        help="Server port (default: 8204)")
    return parser.parse_args()


async def setup() -> Roots:
    backend = SqliteBackend(":memory:")
    await backend.initialize()

    app = Roots(storage=backend)
    await app.__aenter__()

    await app.load_process(PROCESS_YAML)
    await app.register_agent("echo_agent", echo_agent)

    return app


def main() -> None:
    args = parse_args()
    port = args.port

    roots = asyncio.run(setup())
    app = create_demo_app(roots, "API Explorer", STATIC_DIR)

    # Webhook receiver: stores received events for the event log panel
    app.state.received_events = []

    @app.post("/api/webhook-receiver")
    async def webhook_receiver(request: Request) -> dict[str, str]:
        body = await request.json()
        app.state.received_events.append(body)
        return {"status": "received"}

    @app.get("/api/received-events")
    async def received_events() -> list[dict[str, Any]]:
        return app.state.received_events

    # Register a webhook pointing at our own receiver
    async def _register_webhook() -> None:
        await roots.storage.create_webhook(
            url=f"http://localhost:{port}/api/webhook-receiver",
            events=["*"],
        )

    asyncio.run(_register_webhook())

    open_browser(port)
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
