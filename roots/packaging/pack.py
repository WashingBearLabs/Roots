"""High-level pack operation — build a .root package from a process YAML."""

from __future__ import annotations

from pathlib import Path

from roots.core.validator import load_process_yaml
from roots.packaging.archive import create_archive
from roots.packaging.extractor import extract_agent_contracts, extract_config_overrides
from roots.packaging.manifest import RootManifest


def pack_process(
    process_path: str | Path,
    output_path: str | Path | None = None,
    version: str | None = None,
    author: str | None = None,
    description: str | None = None,
    include_defaults: str | Path | None = None,
) -> Path:
    """Build a .root package from a process YAML file.

    Args:
        process_path: Path to the process YAML file.
        output_path: Where to write the .root file. Defaults to
            ``{process_id}-{version}.root`` in the current directory.
        version: Semver override; defaults to the process version field.
        author: Author name for the manifest.
        description: Description override; defaults to process description.
        include_defaults: Path to a directory to bundle as ``defaults/``.

    Returns:
        The path to the created .root archive.
    """
    from roots import __version__ as roots_version

    process_path = Path(process_path)
    process = load_process_yaml(process_path)

    pkg_version = version or process.version
    pkg_description = description or process.description or ""

    agent_contracts = extract_agent_contracts(process)
    config_overrides = extract_config_overrides(process)

    has_defaults = False
    defaults_module: str | None = None
    extra_files: dict[str, Path] | None = None

    if include_defaults is not None:
        defaults_dir = Path(include_defaults)
        if defaults_dir.is_dir():
            has_defaults = True
            defaults_module = "defaults"
            extra_files = {}
            for file_path in sorted(defaults_dir.rglob("*")):
                if file_path.is_file():
                    arcname = f"defaults/{file_path.relative_to(defaults_dir)}"
                    extra_files[arcname] = file_path

    manifest = RootManifest(
        package_id=process.id,
        package_version=pkg_version,
        name=process.name,
        description=pkg_description,
        author=author,
        roots_version=f">={roots_version}",
        agent_contracts=agent_contracts,
        config_overrides=config_overrides,
        has_defaults=has_defaults,
        defaults_module=defaults_module,
    )

    if output_path is None:
        output_path = Path(f"{process.id}-{pkg_version}.root")
    else:
        output_path = Path(output_path)

    return create_archive(manifest, process_path, output_path, extra_files=extra_files)
