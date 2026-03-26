"""Installed package tracking — list, status, and uninstall."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from roots.agents.registry import AgentRegistry
from roots.core.schema import ProcessDefinition
from roots.packaging.installer import ContractReport, validate_contracts
from roots.packaging.manifest import RootManifest
from roots.storage.base import StorageBackend


@dataclass
class InstalledPackage:
    """Summary of an installed package."""

    package_id: str
    package_version: str
    process_id: str
    installed_at: str
    installed_from: str
    agents_wired: int
    agents_total: int
    ready: bool


@dataclass
class PackageStatus:
    """Detailed status of an installed package."""

    package_id: str
    package_version: str
    process_id: str
    process_name: str
    process_description: str
    installed_at: str
    installed_from: str
    contract_report: ContractReport
    overrides: list[dict[str, Any]]
    active_runs: int


async def list_installed_packages(
    storage: StorageBackend,
    registry: AgentRegistry,
) -> list[InstalledPackage]:
    """List all installed packages by scanning processes with package metadata."""
    processes = await storage.list_processes()
    packages: list[InstalledPackage] = []

    for process in processes:
        if "package_id" not in process.metadata:
            continue

        # Build a minimal manifest to validate contracts
        manifest = _manifest_from_process(process)
        report = validate_contracts(manifest, registry)

        agents_total = len(manifest.agent_contracts)
        agents_wired = len(report.satisfied)

        packages.append(
            InstalledPackage(
                package_id=process.metadata["package_id"],
                package_version=process.metadata.get("package_version", "unknown"),
                process_id=process.id,
                installed_at=process.metadata.get("installed_at", "unknown"),
                installed_from=process.metadata.get("installed_from", "unknown"),
                agents_wired=agents_wired,
                agents_total=agents_total,
                ready=report.ready,
            )
        )

    return packages


async def get_package_status(
    package_id: str,
    storage: StorageBackend,
    registry: AgentRegistry,
) -> PackageStatus | None:
    """Get detailed status for a specific installed package."""
    processes = await storage.list_processes()

    process: ProcessDefinition | None = None
    for p in processes:
        if p.metadata.get("package_id") == package_id:
            process = p
            break

    if process is None:
        return None

    manifest = _manifest_from_process(process)
    report = validate_contracts(manifest, registry)

    # Extract current config overrides
    from roots.packaging.extractor import extract_config_overrides

    config_overrides = extract_config_overrides(process)
    overrides_list = [
        {
            "path": o.path,
            "value": o.default_value,
            "type": o.value_type,
        }
        for o in config_overrides
    ]

    # Count active runs
    runs = await storage.list_runs(process_id=process.id)
    active_runs = sum(1 for r in runs if r.status in ("running", "paused"))

    return PackageStatus(
        package_id=process.metadata["package_id"],
        package_version=process.metadata.get("package_version", "unknown"),
        process_id=process.id,
        process_name=process.name,
        process_description=process.description or "",
        installed_at=process.metadata.get("installed_at", "unknown"),
        installed_from=process.metadata.get("installed_from", "unknown"),
        contract_report=report,
        overrides=overrides_list,
        active_runs=active_runs,
    )


async def uninstall_package(
    package_id: str,
    storage: StorageBackend,
    force: bool = False,
) -> bool:
    """Uninstall a package by removing its process from storage.

    Returns True if the package was found and removed.
    Raises ValueError if there are active runs (unless force=True).
    """
    processes = await storage.list_processes()

    process: ProcessDefinition | None = None
    for p in processes:
        if p.metadata.get("package_id") == package_id:
            process = p
            break

    if process is None:
        return False

    # Check for active runs
    runs = await storage.list_runs(process_id=process.id)
    active_runs = [r for r in runs if r.status in ("running", "paused")]

    if active_runs and not force:
        raise ValueError(
            f"Package '{package_id}' has {len(active_runs)} active run(s). "
            f"Use --force to uninstall anyway."
        )

    await storage.delete_process(process.id)
    return True


def _manifest_from_process(process: ProcessDefinition) -> RootManifest:
    """Build a minimal RootManifest from process metadata for contract validation."""
    from roots.packaging.extractor import extract_agent_contracts

    contracts = extract_agent_contracts(process)

    return RootManifest(
        package_id=process.metadata.get("package_id", "unknown"),
        package_version=process.metadata.get("package_version", "0.0.0"),
        name=process.name,
        description=process.description or "",
        roots_version=">=0.1.0",
        agent_contracts=contracts,
    )
