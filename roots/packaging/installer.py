"""Load and validate .root package archives."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from roots.core.schema import ProcessDefinition
from roots.core.validator import (
    format_validation_errors,
    parse_process_dict,
    validate_structure,
)
from roots.packaging.manifest import RootManifest


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
