"""Root packaging utilities."""

from __future__ import annotations

from roots.packaging.archive import (
    create_archive,
    list_archive_contents,
    read_archive,
)
from roots.packaging.config import (
    ConfigError,
    apply_override,
    apply_overrides_from_file,
    list_overrides,
)
from roots.packaging.extractor import extract_agent_contracts, extract_config_overrides
from roots.packaging.manifest import AgentContract, ConfigOverride, RootManifest
from roots.packaging.inspect import inspect_package
from roots.packaging.installer import (
    ContractMatch,
    ContractReport,
    SchemaMismatch,
    install_package,
    load_package,
    validate_contracts,
    validate_package,
)
from roots.packaging.pack import pack_process
from roots.packaging.tracker import (
    InstalledPackage,
    PackageStatus,
    get_package_status,
    list_installed_packages,
    uninstall_package,
)

__all__ = [
    "AgentContract",
    "ConfigError",
    "ConfigOverride",
    "ContractMatch",
    "ContractReport",
    "InstalledPackage",
    "PackageStatus",
    "RootManifest",
    "SchemaMismatch",
    "apply_override",
    "apply_overrides_from_file",
    "create_archive",
    "extract_agent_contracts",
    "extract_config_overrides",
    "get_package_status",
    "inspect_package",
    "install_package",
    "list_archive_contents",
    "list_installed_packages",
    "list_overrides",
    "load_package",
    "pack_process",
    "uninstall_package",
    "validate_contracts",
    "validate_package",
    "read_archive",
]
