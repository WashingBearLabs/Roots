"""Tests for roots install flow — install_package and Roots.install_package."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from roots.agents.registry import AgentRegistry
from roots.agents.types import AgentRegistration, AgentType
from roots.packaging.installer import (
    ContractReport,
    install_package,
    load_package,
    validate_contracts,
)
from roots.packaging.manifest import RootManifest
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


def _minimal_manifest_data(
    extra_contracts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    contracts: list[dict[str, Any]] = [
        {"name": "worker_agent", "description": "Does work"},
    ]
    if extra_contracts:
        contracts.extend(extra_contracts)
    return {
        "package_id": "test-org/my-process",
        "package_version": "1.0.0",
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
    agent_type: AgentType = AgentType.LOCAL,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    callback_url: str | None = None,
) -> None:
    reg = AgentRegistration(
        name=name,
        agent_type=agent_type,
        callable=_noop if agent_type == AgentType.LOCAL else None,
        callback_url=callback_url,
        input_schema=input_schema,
        output_schema=output_schema,
    )
    registry.register(reg)


@pytest.fixture
async def storage(tmp_path: Path) -> SqliteBackend:
    db_path = tmp_path / "test.db"
    backend = SqliteBackend(str(db_path))
    await backend.initialize()
    return backend


# ---------------------------------------------------------------------------
# install_package — loads process into storage
# ---------------------------------------------------------------------------


class TestInstallPackage:
    @pytest.mark.asyncio
    async def test_install_loads_process_into_storage(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()

        manifest, process, report = await install_package(
            archive, storage, registry
        )

        # Verify process is in storage
        saved = await storage.get_process("test-process")
        assert saved is not None
        assert saved.id == "test-process"
        assert saved.name == "Test Process"

    @pytest.mark.asyncio
    async def test_install_returns_contract_report(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()

        manifest, process, report = await install_package(
            archive, storage, registry
        )

        assert isinstance(report, ContractReport)
        # worker_agent is not registered, so it should be missing
        assert len(report.missing) == 1
        assert report.missing[0].name == "worker_agent"

    @pytest.mark.asyncio
    async def test_install_with_registered_agents(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()
        _register(registry, "worker_agent")

        manifest, process, report = await install_package(
            archive, storage, registry
        )

        assert len(report.satisfied) == 1
        assert len(report.missing) == 0
        assert report.ready is True


# ---------------------------------------------------------------------------
# Existing process blocked without --force
# ---------------------------------------------------------------------------


class TestExistingProcessBlocked:
    @pytest.mark.asyncio
    async def test_existing_process_blocked_without_force(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()

        # First install succeeds
        await install_package(archive, storage, registry)

        # Second install without force fails
        with pytest.raises(ValueError, match="already exists"):
            await install_package(archive, storage, registry)

    @pytest.mark.asyncio
    async def test_force_overwrites_existing_process(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()

        # First install
        await install_package(archive, storage, registry)

        # Second install with force succeeds
        manifest, process, report = await install_package(
            archive, storage, registry, force=True
        )
        assert process.id == "test-process"

        # Process still exists in storage
        saved = await storage.get_process("test-process")
        assert saved is not None


# ---------------------------------------------------------------------------
# Package metadata stored on process
# ---------------------------------------------------------------------------


class TestPackageMetadata:
    @pytest.mark.asyncio
    async def test_metadata_stored_on_process(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()

        manifest, process, report = await install_package(
            archive, storage, registry
        )

        assert process.metadata["package_id"] == "test-org/my-process"
        assert process.metadata["package_version"] == "1.0.0"
        assert "installed_at" in process.metadata
        assert process.metadata["installed_from"] == "test.root"

    @pytest.mark.asyncio
    async def test_metadata_persisted_in_storage(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()

        await install_package(archive, storage, registry)

        saved = await storage.get_process("test-process")
        assert saved is not None
        assert saved.metadata["package_id"] == "test-org/my-process"


# ---------------------------------------------------------------------------
# Contract report shows satisfied/missing/optional status
# ---------------------------------------------------------------------------


class TestContractReportStatus:
    @pytest.mark.asyncio
    async def test_report_shows_missing_agents(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        manifest_data = _minimal_manifest_data(
            extra_contracts=[
                {"name": "optional_agent", "description": "Optional", "required": False},
            ]
        )
        archive = _build_archive(tmp_path, manifest_data=manifest_data)
        registry = AgentRegistry()

        manifest, process, report = await install_package(
            archive, storage, registry
        )

        assert len(report.missing) == 1
        assert report.missing[0].name == "worker_agent"
        assert len(report.optional_missing) == 1
        assert report.optional_missing[0].name == "optional_agent"
        assert report.ready is False

    @pytest.mark.asyncio
    async def test_report_all_satisfied(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()
        _register(registry, "worker_agent")

        manifest, process, report = await install_package(
            archive, storage, registry
        )

        assert report.ready is True
        assert len(report.missing) == 0
        assert len(report.satisfied) == 1


# ---------------------------------------------------------------------------
# Programmatic API on Roots class
# ---------------------------------------------------------------------------


class TestRootsInstallPackage:
    @pytest.mark.asyncio
    async def test_roots_install_package_api(
        self, tmp_path: Path,
    ):
        from roots import Roots

        db_path = tmp_path / "roots.db"
        backend = SqliteBackend(str(db_path))
        await backend.initialize()

        roots = Roots(storage=backend)
        archive = _build_archive(tmp_path)

        report = await roots.install_package(archive)

        assert isinstance(report, ContractReport)
        assert len(report.missing) == 1

        # Verify process saved
        saved = await backend.get_process("test-process")
        assert saved is not None

    @pytest.mark.asyncio
    async def test_roots_install_package_with_force(
        self, tmp_path: Path,
    ):
        from roots import Roots

        db_path = tmp_path / "roots.db"
        backend = SqliteBackend(str(db_path))
        await backend.initialize()

        roots = Roots(storage=backend)
        archive = _build_archive(tmp_path)

        # First install
        await roots.install_package(archive)

        # Second install with force
        report = await roots.install_package(archive, force=True)
        assert isinstance(report, ContractReport)


# ---------------------------------------------------------------------------
# Install with pre-registered agents vs without
# ---------------------------------------------------------------------------


class TestInstallWithAndWithoutAgents:
    @pytest.mark.asyncio
    async def test_install_without_agents_shows_missing(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()

        _, _, report = await install_package(archive, storage, registry)

        assert not report.ready
        assert len(report.missing) == 1
        missing_names = [c.name for c in report.missing]
        assert "worker_agent" in missing_names

    @pytest.mark.asyncio
    async def test_install_with_all_agents_ready(
        self, tmp_path: Path, storage: SqliteBackend
    ):
        archive = _build_archive(tmp_path)
        registry = AgentRegistry()
        _register(registry, "worker_agent")

        _, _, report = await install_package(archive, storage, registry)

        assert report.ready
        assert len(report.missing) == 0
        assert len(report.satisfied) == 1
