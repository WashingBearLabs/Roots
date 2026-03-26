#!/usr/bin/env python3
"""Install and run the sample-review Root package.

Usage:
    python examples/packaging/install_and_run.py

This script demonstrates the full packaging lifecycle:
  1. Install the pre-built .root package with default agents
  2. Verify all agent contracts are satisfied
  3. Run the process with a sample work item
  4. Print the final run state
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from roots import Roots
from roots.storage.sqlite import SqliteBackend

PACKAGE_PATH = Path(__file__).parent / "sample-review-1.0.0.root"


async def main() -> None:
    backend = SqliteBackend(":memory:")
    await backend.initialize()

    async with Roots(storage=backend) as app:
        # 1. Install with defaults
        print(f"Installing {PACKAGE_PATH.name}...")
        report = await app.install_package(PACKAGE_PATH, apply_defaults=True)

        # 2. Check contracts
        print(f"Contracts satisfied: {len(report.satisfied)}/{len(report.satisfied) + len(report.missing)}")
        assert report.ready, "Not all agent contracts are satisfied!"
        print("All agents wired — ready to run.\n")

        # 3. Run
        print("Starting run...")
        run = await app.start_run("sample-review", {"source": "example"})
        await app.execute_run(run.id)

        final = await app.get_run(run.id)
        assert final is not None
        print(f"Run completed with status: {final.status}")
        print(f"Final state keys: {list(final.work_item_state.keys())}")


if __name__ == "__main__":
    asyncio.run(main())
