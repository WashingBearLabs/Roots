#!/usr/bin/env python3
"""End-to-end example: load a process, register an echo agent, and execute a run."""

from __future__ import annotations

import asyncio
from pathlib import Path

from roots import Roots
from roots.storage.sqlite import SqliteBackend


async def echo_agent(input: dict) -> dict:  # noqa: A002
    """Simple agent that echoes the work-item state back as output."""
    return {"output": {"echo": input["work_item_state"]}, "escalate": False}


async def main() -> None:
    backend = SqliteBackend(":memory:")
    await backend.initialize()

    async with Roots(storage=backend) as app:
        # Load the simple-linear process YAML
        yaml_path = Path(__file__).parent / "processes" / "simple-linear.yaml"
        await app.load_process(str(yaml_path))

        # Register the echo agent referenced by the process
        await app.register_agent("echo_agent", echo_agent)

        # Start and execute the run
        run = await app.start_run("simple-linear", {"message": "hello"})
        print(f"Run started: {run.id} (status={run.status})")

        await app.execute_run(run.id)

        # Fetch final state
        final = await app.get_run(run.id)
        assert final is not None
        print(f"Run finished: {final.id} (status={final.status})")
        print(f"Final state: {final.work_item_state}")


if __name__ == "__main__":
    asyncio.run(main())
