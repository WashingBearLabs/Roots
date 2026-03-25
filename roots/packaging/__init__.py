"""Root packaging utilities."""

from __future__ import annotations

from roots.packaging.archive import (
    create_archive,
    list_archive_contents,
    read_archive,
)
from roots.packaging.extractor import extract_agent_contracts, extract_config_overrides
from roots.packaging.manifest import AgentContract, ConfigOverride, RootManifest
from roots.packaging.inspect import inspect_package
from roots.packaging.installer import load_package, validate_package
from roots.packaging.pack import pack_process

__all__ = [
    "AgentContract",
    "ConfigOverride",
    "RootManifest",
    "create_archive",
    "extract_agent_contracts",
    "extract_config_overrides",
    "inspect_package",
    "list_archive_contents",
    "load_package",
    "pack_process",
    "validate_package",
    "read_archive",
]
