"""Tests for installed package tracking — list, status, and uninstall."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from roots import Roots
from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentRegistration, AgentType
from roots.packaging.tracker import (
    InstalledPackage,
    PackageStatus,
    get_package_status,
    list_installed_packages,
    uninstall_package,
)
from roots.packaging.installer import install_package
from roots.storage.sqlite import SqliteBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_PROCESS = """\
id: test-process
name: Test Process
version: "1.0.0"
description: A test process
nodes:
  - id: step1
    type: agent
    label: First Step
    config:
      agent: worker_agent
      output_key: step1_out
  - id: done
    type: end
    label: Complete
    config:
      status: completed
edges:
  - from: step1
    to: done
entry_point: step1
"""

_SECOND_PROCESS = """\
id: second-process
name: Second Process
version: "2.0.0"
description: Another process
nodes:
  - id: step1
    type: agent
    label: First Step
    config:
      agent: reviewer_agent
      output_key: step1_out
  - id: done
    type: end
    label: Complete
    config:
      status: completed
edges:
  - from: step1
    to: done
entry_point: step1
"""


def _minimal_manifest_data(
    package_id: str = "test-org/my-process",
    package_version: str = "1.0.0",
    contracts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if contracts is None:
        contracts = [{"name": "worker_agent", "description": "Does work"}]
    return {
        "package_id": package_id,
        "package_version": package_version,
        "name": "My Process",
        "description": "A test process",
        "roots_version": ">=0.1.0",
        "agent_contracts": contracts,
    }


def _build_archive(
    tmp_path: Path,
    manifest_data: dict[str, Any] | None = None,
    process_yaml: str = _SIMPLE_PROCESS,
    name: str = "test.root",
) -> Path:
    process_bytes = process_yaml.encode()
    if manifest_data is None:
        manifest_data = _minimal_manifest_data()
    manifest_data.setdefault(
        "checksum", hashlib.sha256(process_bytes).hexdigest()
    )
    archive_path = tmp_path / name
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest_data, indent=2))
        zf.writestr("process.yaml", process_bytes)
    return archive_path


def _noop(**kwargs: Any) -> dict[str, Any]:
    return {}


def _register(
    registry: AgentRegistry,
    name: str,
) -> None:
    reg = AgentRegistration(
        name=name,
        agent_type=AgentType.LOCAL,
        callable=_noop,
    )
    registry.register(reg)


@pytest.fixture
async def storage(tmp_path: Path) -> SqliteBackend:
    db_path = tmp_path / "test.db"
    backend = SqliteBackend(str(db_path))
    await backend.initialize()
    return backend


async def _install(
    tmp_path: Path,
    storage: SqliteBackend,
    registry: AgentRegistry,
    manifest_data: dict[str, Any] | None = None,
    process_yaml: str = _SIMPLE_PROCESS,
    name: str = "test.root",
) -> None:
    archive = _build_archive(
        tmp_path, manifest_data=manifest_data, process_yaml=process_yaml, name=name
    )
    await install_package(archive, storage, registry)


# ---------------------------------------------------------------------------
# list_installed_packages
# ---------------------------------------------------------------------------


class TestListInstalledPackages:
    @pytest.mark.asyncio
    async def test_empty_when_no_packages(self, storage: SqliteBackend):
        registry = AgentRegistry()
        result = await list_installed_packages(storage, registry)
        assert result == []

    @pytest.mark.asyncio
    async def test_lists_installed_packages(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        await _install(tmp_path, storage, registry)

        result = await list_installed_packages(storage, registry)

        assert len(result) == 1
        assert isinstance(result[0], InstalledPackage)
        assert result[0].package_id == "test-org/my-process"
        assert result[0].package_version == "1.0.0"
        assert result[0].process_id == "test-process"

    @pytest.mark.asyncio
    async def test_shows_wiring_status_missing(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        await _install(tmp_path, storage, registry)

        result = await list_installed_packages(storage, registry)

        assert result[0].agents_wired == 0
        assert result[0].agents_total == 1
        assert result[0].ready is False

    @pytest.mark.asyncio
    async def test_shows_wiring_status_satisfied(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        _register(registry, "worker_agent")
        await _install(tmp_path, storage, registry)

        result = await list_installed_packages(storage, registry)

        assert result[0].agents_wired == 1
        assert result[0].agents_total == 1
        assert result[0].ready is True

    @pytest.mark.asyncio
    async def test_lists_multiple_packages(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()

        # Install first package
        await _install(tmp_path, storage, registry, name="first.root")

        # Install second package
        manifest2 = _minimal_manifest_data(
            package_id="test-org/second",
            package_version="2.0.0",
            contracts=[{"name": "reviewer_agent", "description": "Reviews"}],
        )
        await _install(
            tmp_path, storage, registry,
            manifest_data=manifest2,
            process_yaml=_SECOND_PROCESS,
            name="second.root",
        )

        result = await list_installed_packages(storage, registry)
        assert len(result) == 2
        ids = {p.package_id for p in result}
        assert ids == {"test-org/my-process", "test-org/second"}


# ---------------------------------------------------------------------------
# get_package_status
# ---------------------------------------------------------------------------


class TestGetPackageStatus:
    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_package(self, storage: SqliteBackend):
        registry = AgentRegistry()
        result = await get_package_status("nonexistent", storage, registry)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_detailed_status(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        _register(registry, "worker_agent")
        await _install(tmp_path, storage, registry)

        result = await get_package_status("test-org/my-process", storage, registry)

        assert result is not None
        assert isinstance(result, PackageStatus)
        assert result.package_id == "test-org/my-process"
        assert result.process_id == "test-process"
        assert result.process_name == "Test Process"
        assert result.contract_report.ready is True
        assert len(result.contract_report.satisfied) == 1
        assert result.active_runs == 0

    @pytest.mark.asyncio
    async def test_shows_missing_agents(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        await _install(tmp_path, storage, registry)

        result = await get_package_status("test-org/my-process", storage, registry)

        assert result is not None
        assert result.contract_report.ready is False
        assert len(result.contract_report.missing) == 1

    @pytest.mark.asyncio
    async def test_shows_active_runs(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        await _install(tmp_path, storage, registry)

        # Create a run and set it to running
        run = await storage.create_run("test-process", {"data": "test"})
        await storage.update_run_status(run.id, "running")

        result = await get_package_status("test-org/my-process", storage, registry)
        assert result is not None
        assert result.active_runs == 1


# ---------------------------------------------------------------------------
# uninstall_package
# ---------------------------------------------------------------------------


class TestUninstallPackage:
    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_package(self, storage: SqliteBackend):
        result = await uninstall_package("nonexistent", storage)
        assert result is False

    @pytest.mark.asyncio
    async def test_removes_package(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        await _install(tmp_path, storage, registry)

        # Verify it's there
        process = await storage.get_process("test-process")
        assert process is not None

        result = await uninstall_package("test-org/my-process", storage)
        assert result is True

        # Verify it's gone
        process = await storage.get_process("test-process")
        assert process is None

    @pytest.mark.asyncio
    async def test_blocks_uninstall_with_active_runs(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        await _install(tmp_path, storage, registry)

        # Create a run and set it to running
        run = await storage.create_run("test-process", {"data": "test"})
        await storage.update_run_status(run.id, "running")

        with pytest.raises(ValueError, match="active run"):
            await uninstall_package("test-org/my-process", storage)

        # Process should still exist
        process = await storage.get_process("test-process")
        assert process is not None

    @pytest.mark.asyncio
    async def test_force_uninstall_with_active_runs(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        registry = AgentRegistry()
        await _install(tmp_path, storage, registry)

        # Create a run and set it to running
        run = await storage.create_run("test-process", {"data": "test"})
        await storage.update_run_status(run.id, "running")

        result = await uninstall_package("test-org/my-process", storage, force=True)
        assert result is True

        # Process should be gone
        process = await storage.get_process("test-process")
        assert process is None


# ---------------------------------------------------------------------------
# Programmatic APIs on Roots class
# ---------------------------------------------------------------------------


class TestRootsPackageAPIs:
    @pytest.mark.asyncio
    async def test_list_installed_packages(self, tmp_path: Path):
        db_path = tmp_path / "roots.db"
        backend = SqliteBackend(str(db_path))
        await backend.initialize()

        roots = Roots(storage=backend)
        archive = _build_archive(tmp_path)
        await roots.install_package(archive)

        packages = await roots.list_installed_packages()
        assert len(packages) == 1
        assert packages[0].package_id == "test-org/my-process"

    @pytest.mark.asyncio
    async def test_get_package_status(self, tmp_path: Path):
        db_path = tmp_path / "roots.db"
        backend = SqliteBackend(str(db_path))
        await backend.initialize()

        roots = Roots(storage=backend)
        archive = _build_archive(tmp_path)
        await roots.install_package(archive)

        status_info = await roots.get_package_status("test-org/my-process")
        assert status_info is not None
        assert status_info.package_id == "test-org/my-process"
        assert status_info.process_id == "test-process"

    @pytest.mark.asyncio
    async def test_uninstall_package(self, tmp_path: Path):
        db_path = tmp_path / "roots.db"
        backend = SqliteBackend(str(db_path))
        await backend.initialize()

        roots = Roots(storage=backend)
        archive = _build_archive(tmp_path)
        await roots.install_package(archive)

        result = await roots.uninstall_package("test-org/my-process")
        assert result is True

        # Verify gone
        status_info = await roots.get_package_status("test-org/my-process")
        assert status_info is None

    @pytest.mark.asyncio
    async def test_uninstall_nonexistent_returns_false(self, tmp_path: Path):
        db_path = tmp_path / "roots.db"
        backend = SqliteBackend(str(db_path))
        await backend.initialize()

        roots = Roots(storage=backend)
        result = await roots.uninstall_package("nonexistent")
        assert result is False
