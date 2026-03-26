"""End-to-end test: pack -> inspect -> install -> run -> configure."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from roots import Roots
from roots.packaging.config import apply_override
from roots.packaging.extractor import extract_config_overrides
from roots.packaging.inspect import inspect_package
from roots.packaging.pack import pack_process
from roots.storage.sqlite import SqliteBackend


# ---------------------------------------------------------------------------
# Process YAML — 3 agent nodes + 1 deterministic decision node
# ---------------------------------------------------------------------------

_E2E_PROCESS = """\
id: e2e-test-process
name: E2E Test Process
version: "1.0.0"
description: End-to-end packaging lifecycle test
nodes:
  - id: ingest
    type: agent
    label: Ingest Data
    config:
      agent: ingest_agent
      output_key: ingest_out
  - id: gate
    type: decision
    label: Quality Gate
    config:
      mode: deterministic
      edges:
        - target: enrich
          condition: "ingest_out.quality == 'good'"
          label: Good quality
        - target: review
          condition: "ingest_out.quality == 'bad'"
          label: Bad quality
  - id: enrich
    type: agent
    label: Enrich Data
    config:
      agent: enrich_agent
      output_key: enrich_out
  - id: review
    type: agent
    label: Review Data
    config:
      agent: review_agent
      output_key: review_out
  - id: done
    type: end
    label: Complete
    config:
      status: completed
edges:
  - from: ingest
    to: gate
  - from: enrich
    to: done
  - from: review
    to: done
entry_point: ingest
"""

# ---------------------------------------------------------------------------
# Default agent implementations module
# ---------------------------------------------------------------------------

_DEFAULTS_AGENTS_PY = '''\
"""Default agents for e2e test process."""


async def ingest_agent(input: dict) -> dict:
    return {
        "output": {"quality": "good", "data": input.get("work_item_state", {})},
        "escalate": False,
    }


async def enrich_agent(input: dict) -> dict:
    return {
        "output": {"enriched": True},
        "escalate": False,
    }


async def review_agent(input: dict) -> dict:
    return {
        "output": {"reviewed": True},
        "escalate": False,
    }


def register_agents(roots):
    agents = [
        ("ingest_agent", ingest_agent, None, None),
        ("enrich_agent", enrich_agent, None, None),
        ("review_agent", review_agent, None, None),
    ]
    registered = []
    for name, fn, in_schema, out_schema in agents:
        roots.register_agent(name, fn, input_schema=in_schema, output_schema=out_schema)
        registered.append(name)
    return registered
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_process(tmp_path: Path) -> Path:
    p = tmp_path / "process.yaml"
    p.write_text(_E2E_PROCESS)
    return p


def _write_defaults(tmp_path: Path) -> Path:
    defaults_dir = tmp_path / "defaults"
    defaults_dir.mkdir()
    (defaults_dir / "__init__.py").write_text(_DEFAULTS_AGENTS_PY)
    return defaults_dir


# ---------------------------------------------------------------------------
# End-to-end test
# ---------------------------------------------------------------------------


class TestPackagingE2E:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, tmp_path: Path) -> None:
        """Pack -> inspect -> install -> run -> configure."""

        # --- 1. Create process YAML and defaults ---
        process_file = _write_process(tmp_path)
        defaults_dir = _write_defaults(tmp_path)

        # --- 2. Pack ---
        archive_path = tmp_path / "e2e-test.root"
        result = pack_process(
            process_file,
            output_path=archive_path,
            version="1.0.0",
            author="Test",
            include_defaults=str(defaults_dir),
        )
        assert result == archive_path
        assert archive_path.exists()
        assert zipfile.is_zipfile(archive_path)

        # Verify archive contents
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert "process.yaml" in names
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["package_id"] == "e2e-test-process"
            assert manifest["package_version"] == "1.0.0"
            assert manifest["author"] == "Test"
            assert manifest["has_defaults"] is True

        # --- 3. Inspect ---
        # Just verify inspect_package runs without error
        inspect_package(archive_path)

        # --- 4. Install on fresh storage with --apply-defaults ---
        backend = SqliteBackend(":memory:")
        await backend.initialize()

        async with Roots(storage=backend) as app:
            report = await app.install_package(
                archive_path, apply_defaults=True
            )

            # --- 5. Verify contracts satisfied ---
            assert report.ready is True
            assert len(report.satisfied) == 3
            assert len(report.missing) == 0
            satisfied_names = {m.contract.name for m in report.satisfied}
            assert satisfied_names == {"ingest_agent", "enrich_agent", "review_agent"}

            # --- 6. Run the process ---
            run = await app.start_run(
                "e2e-test-process", {"test": True}
            )
            await app.execute_run(run.id)

            final = await app.get_run(run.id)
            assert final is not None
            assert final.status == "completed"

            # Verify state has ingest output routed through "good" quality path
            assert "ingest_out" in final.work_item_state
            assert final.work_item_state["ingest_out"]["quality"] == "good"
            assert "enrich_out" in final.work_item_state
            assert final.work_item_state["enrich_out"]["enriched"] is True
            # review_out should NOT be present (deterministic route went to enrich)
            assert "review_out" not in final.work_item_state

            # --- 7. Config override ---
            process = await app.storage.get_process("e2e-test-process")
            assert process is not None

            overrides = extract_config_overrides(process)
            updated = apply_override(
                process,
                "nodes.ingest.config.output_key",
                "new_ingest_out",
                config_overrides=overrides,
            )

            # Save the override
            await app.storage.delete_process("e2e-test-process")
            await app.storage.save_process(updated)

            # --- 8. Verify override persisted ---
            reloaded = await app.storage.get_process("e2e-test-process")
            assert reloaded is not None
            ingest_node = reloaded.get_node("ingest")
            assert ingest_node is not None
            assert ingest_node.config.output_key == "new_ingest_out"

    @pytest.mark.asyncio
    async def test_self_contained_cleanup(self, tmp_path: Path) -> None:
        """Test is self-contained: all files in tmp_path, no side effects."""
        process_file = _write_process(tmp_path)
        defaults_dir = _write_defaults(tmp_path)

        archive_path = tmp_path / "cleanup-test.root"
        pack_process(
            process_file,
            output_path=archive_path,
            include_defaults=str(defaults_dir),
        )

        backend = SqliteBackend(":memory:")
        await backend.initialize()

        async with Roots(storage=backend) as app:
            await app.install_package(archive_path, apply_defaults=True)
            run = await app.start_run("e2e-test-process", {"cleanup": True})
            await app.execute_run(run.id)

            final = await app.get_run(run.id)
            assert final is not None
            assert final.status == "completed"

        # tmp_path is automatically cleaned up by pytest
