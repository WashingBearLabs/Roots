"""Load and validate .root package archives."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ValidationError

from roots.agents.registry import AgentRegistry
from roots.core.schema import ProcessDefinition
from roots.core.validator import (
    format_validation_errors,
    parse_process_dict,
    validate_structure,
)
from roots.packaging.manifest import AgentContract, RootManifest
from roots.storage.base import StorageBackend


class SchemaMismatch(BaseModel):
    """Describes a schema incompatibility between contract and registration."""

    agent_name: str
    direction: str  # "input" or "output"
    expected: dict[str, Any] | None
    actual: dict[str, Any] | None
    details: str


class ContractMatch(BaseModel):
    """A contract successfully matched to a registration."""

    contract: AgentContract
    registration: dict[str, Any]
    schema_compatible: bool


class ContractReport(BaseModel):
    """Result of validating agent contracts against a registry."""

    satisfied: list[ContractMatch] = []
    missing: list[AgentContract] = []
    optional_missing: list[AgentContract] = []
    schema_mismatches: list[SchemaMismatch] = []
    ready: bool = True


def _check_schema_compatibility(
    agent_name: str,
    direction: str,
    contract_schema: dict[str, Any] | None,
    registration_schema: dict[str, Any] | None,
) -> SchemaMismatch | None:
    """Check if a registration schema is compatible with a contract schema.

    Returns a SchemaMismatch if incompatible, None if compatible.
    If either side has no schema, it's a soft pass (compatible).
    """
    if contract_schema is None or registration_schema is None:
        return None

    # Check that all required properties in the contract exist in the registration
    contract_props = contract_schema.get("properties", {})
    contract_required = set(contract_schema.get("required", []))
    reg_props = registration_schema.get("properties", {})

    missing_props = []
    type_mismatches = []

    for prop_name in contract_required:
        if prop_name not in reg_props:
            missing_props.append(prop_name)
        elif prop_name in contract_props and prop_name in reg_props:
            expected_type = contract_props[prop_name].get("type")
            actual_type = reg_props[prop_name].get("type")
            if expected_type and actual_type and expected_type != actual_type:
                type_mismatches.append(
                    f"{prop_name}: expected type '{expected_type}', "
                    f"got '{actual_type}'"
                )

    if missing_props or type_mismatches:
        details_parts = []
        if missing_props:
            details_parts.append(
                f"missing required properties: {', '.join(sorted(missing_props))}"
            )
        if type_mismatches:
            details_parts.append(
                f"type mismatches: {'; '.join(type_mismatches)}"
            )
        return SchemaMismatch(
            agent_name=agent_name,
            direction=direction,
            expected=contract_schema,
            actual=registration_schema,
            details="; ".join(details_parts),
        )

    return None


def validate_contracts(
    manifest: RootManifest,
    registry: AgentRegistry,
) -> ContractReport:
    """Validate agent contracts from a manifest against a registry."""
    satisfied: list[ContractMatch] = []
    missing: list[AgentContract] = []
    optional_missing: list[AgentContract] = []
    schema_mismatches: list[SchemaMismatch] = []

    for contract in manifest.agent_contracts:
        reg = registry.get(contract.name)
        if reg is None:
            if contract.required:
                missing.append(contract)
            else:
                optional_missing.append(contract)
            continue

        reg_dict = reg.model_dump(mode="json", exclude={"callable"})

        # Check input and output schema compatibility
        input_mismatch = _check_schema_compatibility(
            contract.name, "input", contract.input_schema, reg.input_schema
        )
        output_mismatch = _check_schema_compatibility(
            contract.name, "output", contract.output_schema, reg.output_schema
        )

        mismatches = [m for m in (input_mismatch, output_mismatch) if m is not None]
        schema_mismatches.extend(mismatches)

        schema_compatible = len(mismatches) == 0
        satisfied.append(
            ContractMatch(
                contract=contract,
                registration=reg_dict,
                schema_compatible=schema_compatible,
            )
        )

    ready = len(missing) == 0 and len(schema_mismatches) == 0

    return ContractReport(
        satisfied=satisfied,
        missing=missing,
        optional_missing=optional_missing,
        schema_mismatches=schema_mismatches,
        ready=ready,
    )


def validate_package(archive_path: Path) -> list[str]:
    """Validate a .root package archive and return a list of error strings.

    An empty list means the package is valid.
    """
    errors: list[str] = []

    # 1. Read the archive (catches corrupt zip and missing files)
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = zf.namelist()
            contents: dict[str, bytes] = {name: zf.read(name) for name in names}
    except zipfile.BadZipFile:
        return [f"{archive_path.name}: file is not a valid zip archive"]

    # 2. Validate manifest.json exists and parses
    if "manifest.json" not in contents:
        errors.append("manifest.json: missing from archive")
        return errors

    try:
        manifest_data = json.loads(contents["manifest.json"])
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append(f"manifest.json: invalid JSON — {exc}")
        return errors

    try:
        manifest = RootManifest.model_validate(manifest_data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = " -> ".join(str(p) for p in err["loc"])
            errors.append(f"manifest.json: {loc} — {err['msg']}")
        return errors

    # 3. Verify process_file exists in archive
    if manifest.process_file not in contents:
        errors.append(
            f"{manifest.process_file}: missing from archive "
            f"(declared in manifest.process_file)"
        )
        return errors

    # 4. Parse process YAML
    process_bytes = contents[manifest.process_file]
    try:
        raw = yaml.safe_load(process_bytes)
    except yaml.YAMLError as exc:
        errors.append(f"{manifest.process_file}: invalid YAML — {exc}")
        return errors

    if not isinstance(raw, dict):
        errors.append(
            f"{manifest.process_file}: expected a YAML mapping at top level, "
            f"got {type(raw).__name__}"
        )
        return errors

    # 5. Validate process schema via Pydantic
    raw_dict = cast(dict[str, Any], raw)
    try:
        process = parse_process_dict(raw_dict)
    except ValidationError as exc:
        for msg in format_validation_errors(exc, raw_data=raw_dict):
            errors.append(f"{manifest.process_file}: {msg}")
        return errors

    # 6. Structural validation
    struct_errors = validate_structure(process)
    for err in struct_errors:
        errors.append(f"{manifest.process_file}: {err}")
    if struct_errors:
        return errors

    # 7. Verify checksum if present
    if manifest.checksum is not None:
        actual = hashlib.sha256(process_bytes).hexdigest()
        if actual != manifest.checksum:
            errors.append(
                f"checksum: expected {manifest.checksum}, got {actual}"
            )

    # 8. Verify defaults_module path if has_defaults is True
    if manifest.has_defaults and manifest.defaults_module is not None:
        # Check that defaults_module path exists as a directory prefix in archive
        defaults_prefix = manifest.defaults_module.replace(".", "/")
        has_defaults_files = any(
            name == defaults_prefix or name.startswith(defaults_prefix + "/")
            for name in contents
        )
        if not has_defaults_files:
            errors.append(
                f"defaults: manifest declares defaults_module "
                f"'{manifest.defaults_module}' but no matching files found "
                f"in archive"
            )

    return errors


def load_package(
    archive_path: Path,
) -> tuple[RootManifest, ProcessDefinition, dict[str, bytes]]:
    """Load a validated .root package, returning manifest, process, and contents.

    Raises ValueError if validation fails.
    """
    validation_errors = validate_package(archive_path)
    if validation_errors:
        raise ValueError(
            f"Package validation failed with {len(validation_errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in validation_errors)
        )

    # Re-read (validation already confirmed these are valid)
    with zipfile.ZipFile(archive_path, "r") as zf:
        contents: dict[str, bytes] = {name: zf.read(name) for name in zf.namelist()}

    manifest = RootManifest.model_validate(json.loads(contents["manifest.json"]))
    process_data = cast(dict[str, Any], yaml.safe_load(contents[manifest.process_file]))
    process = parse_process_dict(process_data)

    return manifest, process, contents


async def install_package(
    archive_path: Path,
    storage: StorageBackend,
    registry: AgentRegistry,
    force: bool = False,
) -> tuple[RootManifest, ProcessDefinition, ContractReport]:
    """Install a .root package: load, save process, and validate contracts.

    Returns the manifest, process definition, and contract report.
    Raises ValueError if validation fails or process already exists (without force).
    """
    manifest, process, _contents = load_package(archive_path)

    # Check for existing process
    existing = await storage.get_process(process.id)
    if existing is not None and not force:
        raise ValueError(
            f"Process '{process.id}' already exists. "
            f"Use --force to overwrite."
        )

    # Store package metadata on the process
    process.metadata["package_id"] = manifest.package_id
    process.metadata["package_version"] = manifest.package_version
    process.metadata["installed_at"] = datetime.now(UTC).isoformat()
    process.metadata["installed_from"] = archive_path.name
    if manifest.config_templates:
        process.metadata["config_templates"] = [
            t.model_dump(mode="json") for t in manifest.config_templates
        ]

    # Save (or overwrite) the process
    if existing is not None and force:
        await storage.delete_process(process.id)
    await storage.save_process(process)

    # Validate contracts against currently registered agents
    report = validate_contracts(manifest, registry)

    return manifest, process, report
