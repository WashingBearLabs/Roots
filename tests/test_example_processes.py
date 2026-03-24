"""Tests for example process YAML files."""

from __future__ import annotations

from pathlib import Path

import pytest

from roots.core.schema import NodeType, ProcessDefinition
from roots.core.validator import load_process_yaml

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples" / "processes"


class TestSimpleLinear:
    def test_parses_and_validates(self) -> None:
        process = load_process_yaml(EXAMPLES_DIR / "simple-linear.yaml")
        assert isinstance(process, ProcessDefinition)
        assert process.id == "simple-linear"
        assert process.entry_point == "step1"

    def test_node_count_and_types(self) -> None:
        process = load_process_yaml(EXAMPLES_DIR / "simple-linear.yaml")
        assert len(process.nodes) == 3
        types = [n.type for n in process.nodes]
        assert types.count(NodeType.AGENT) == 2
        assert types.count(NodeType.END) == 1

    def test_edge_count(self) -> None:
        process = load_process_yaml(EXAMPLES_DIR / "simple-linear.yaml")
        assert len(process.edges) == 2


class TestParallelValidation:
    def test_parses_and_validates(self) -> None:
        process = load_process_yaml(EXAMPLES_DIR / "parallel-validation.yaml")
        assert isinstance(process, ProcessDefinition)
        assert process.id == "parallel-validation"
        assert process.entry_point == "start"

    def test_fork_join_pairing(self) -> None:
        process = load_process_yaml(EXAMPLES_DIR / "parallel-validation.yaml")
        assert "split" in process.fork_join_map
        assert process.fork_join_map["split"] == "merge"

    def test_node_count_and_types(self) -> None:
        process = load_process_yaml(EXAMPLES_DIR / "parallel-validation.yaml")
        assert len(process.nodes) == 9
        type_counts = {}
        for n in process.nodes:
            type_counts[n.type] = type_counts.get(n.type, 0) + 1
        assert type_counts[NodeType.FORK] == 1
        assert type_counts[NodeType.JOIN] == 1
        assert type_counts[NodeType.DECISION] == 1
        assert type_counts[NodeType.AGENT] == 3
        assert type_counts[NodeType.END] == 2

    def test_edge_count(self) -> None:
        process = load_process_yaml(EXAMPLES_DIR / "parallel-validation.yaml")
        assert len(process.edges) == 8


class TestRunSimpleScript:
    @pytest.mark.asyncio
    async def test_run_simple_end_to_end(self) -> None:
        """Verify run_simple.py logic executes end-to-end."""
        from roots import Roots
        from roots.storage.sqlite import SqliteBackend

        backend = SqliteBackend(":memory:")
        await backend.initialize()

        async with Roots(storage=backend) as app:
            await app.load_process(
                str(EXAMPLES_DIR / "simple-linear.yaml")
            )

            async def echo_agent(input: dict) -> dict:  # noqa: A002
                return {
                    "output": {"echo": input["work_item_state"]},
                    "escalate": False,
                }

            await app.register_agent("echo_agent", echo_agent)
            run = await app.start_run("simple-linear", {"message": "hello"})
            await app.execute_run(run.id)

            final = await app.get_run(run.id)
            assert final is not None
            assert final.status == "completed"
