#!/usr/bin/env python3
"""Incident Response Demo — AI triage with escalation and response execution.

Default: mock mode using keyword-based triage (no API key needed).
Pass --model / --api-key / --base-url for live LLM mode.
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

from roots import Roots, LLMConfig
from roots.core.llm import openai_chat_completion
from roots.storage.sqlite import SqliteBackend

from demo._common.demo_server import run_demo
from agents import (  # type: ignore[import-untyped]
    ingest_incident,
    threat_intel_lookup,
    geo_lookup,
    execute_response,
)
from mock_decision import mock_triage_decision  # type: ignore[import-untyped]

STATIC_DIR = str(DEMO_DIR / "static")
PROCESS_YAML = str(DEMO_DIR / "process.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incident Response Demo")
    parser.add_argument("--model", help="LLM model name for live mode")
    parser.add_argument("--base-url", default="https://api.openai.com/v1",
                        help="OpenAI-compatible base URL")
    parser.add_argument("--api-key", help="API key for the LLM provider")
    parser.add_argument("--port", type=int, default=8203,
                        help="Server port (default: 8203)")
    return parser.parse_args()


async def setup(args: argparse.Namespace) -> Roots:
    backend = SqliteBackend(":memory:")
    await backend.initialize()

    if args.model and args.api_key:
        config = LLMConfig(
            base_url=args.base_url,
            api_key=args.api_key,
        )

        async def live_llm(model, messages, tools=None, tool_choice=None):
            return await openai_chat_completion(
                model=model, messages=messages,
                tools=tools, tool_choice=tool_choice,
                config=config,
            )

        app = Roots(
            storage=backend,
            default_model=args.model,
            llm_callable=live_llm,
        )
    else:
        app = Roots(
            storage=backend,
            llm_callable=mock_triage_decision,
        )

    await app.__aenter__()

    await app.load_process(PROCESS_YAML)
    await app.register_agent("ingest_incident", ingest_incident)
    await app.register_agent("threat_intel_lookup", threat_intel_lookup)
    await app.register_agent("geo_lookup", geo_lookup)
    await app.register_agent("execute_response", execute_response)

    return app


def main() -> None:
    args = parse_args()

    if args.model and args.api_key:
        print(f"Running in live mode (model={args.model}).")
    else:
        print("Running in mock mode (no API key needed). "
              "Use --model gpt-4o-mini --api-key YOUR_KEY for live AI.")

    app = asyncio.run(setup(args))
    run_demo(app, "Incident Response", STATIC_DIR, port=args.port)


if __name__ == "__main__":
    main()
