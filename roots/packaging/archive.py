"""Create and read .root package archives."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from roots.packaging.manifest import RootManifest

# Guardrails against decompression ("zip") bombs when reading untrusted .root
# packages: cap the number of entries and the total uncompressed bytes.
MAX_ARCHIVE_FILES = 1000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MB


class ArchiveTooLargeError(ValueError):
    """Raised when a .root archive exceeds the safety limits for reading."""


def create_archive(
    manifest: RootManifest,
    process_path: Path,
    output_path: Path,
    extra_files: dict[str, Path] | None = None,
) -> Path:
    """Create a .root zip archive.

    Writes manifest.json, copies process.yaml, and optionally includes
    README.md, defaults/ directory, and any extra_files.  Computes a
    SHA-256 checksum of process.yaml and stores it in the manifest.
    """
    process_dir = process_path.parent

    # Compute checksum of process.yaml
    process_bytes = process_path.read_bytes()
    manifest = manifest.model_copy(
        update={"checksum": hashlib.sha256(process_bytes).hexdigest()}
    )

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # manifest.json
        zf.writestr("manifest.json", manifest.model_dump_json(indent=2))

        # process.yaml
        zf.writestr("process.yaml", process_bytes)

        # README.md (if exists alongside process)
        readme_path = process_dir / "README.md"
        if readme_path.is_file():
            zf.write(readme_path, "README.md")

        # defaults/ directory
        defaults_dir = process_dir / "defaults"
        if defaults_dir.is_dir():
            for file_path in sorted(defaults_dir.rglob("*")):
                if file_path.is_file():
                    arcname = str(file_path.relative_to(process_dir))
                    zf.write(file_path, arcname)

        # config/ directory
        config_dir = process_dir / "config"
        if config_dir.is_dir():
            for file_path in sorted(config_dir.rglob("*")):
                if file_path.is_file():
                    arcname = str(file_path.relative_to(process_dir))
                    zf.write(file_path, arcname)

        # Extra files
        if extra_files:
            for arcname, local_path in extra_files.items():
                zf.write(local_path, arcname)

    return output_path


def read_archive(
    archive_path: Path,
) -> tuple[RootManifest, dict[str, bytes]]:
    """Read a .root archive, returning the manifest and all file contents.

    Validates the SHA-256 checksum of process.yaml if present.
    """
    with zipfile.ZipFile(archive_path, "r") as zf:
        infos = zf.infolist()

        # Guard 1: entry-count cap.
        if len(infos) > MAX_ARCHIVE_FILES:
            raise ArchiveTooLargeError(
                f"Archive has too many entries: {len(infos)} "
                f"(limit {MAX_ARCHIVE_FILES})"
            )

        # Guard 2: declared total uncompressed size (catches honest bombs early,
        # before any decompression).
        declared_total = sum(info.file_size for info in infos)
        if declared_total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ArchiveTooLargeError(
                f"Archive uncompressed size {declared_total} bytes exceeds "
                f"limit of {MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes"
            )

        # Guard 3: bounded reads (defends against headers that lie about size).
        contents: dict[str, bytes] = {}
        budget = MAX_ARCHIVE_UNCOMPRESSED_BYTES
        for name in zf.namelist():
            with zf.open(name) as member:
                data = member.read(budget + 1)
            if len(data) > budget:
                raise ArchiveTooLargeError(
                    "Archive uncompressed contents exceed limit of "
                    f"{MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes"
                )
            budget -= len(data)
            contents[name] = data

    manifest_bytes = contents.get("manifest.json")
    if manifest_bytes is None:
        raise ValueError("Archive missing manifest.json")
    manifest = RootManifest(**json.loads(manifest_bytes))

    # Validate checksum
    if manifest.checksum is not None:
        process_bytes = contents.get("process.yaml")
        if process_bytes is None:
            raise ValueError("Archive missing process.yaml but checksum is set")
        actual = hashlib.sha256(process_bytes).hexdigest()
        if actual != manifest.checksum:
            raise ValueError(
                f"Checksum mismatch: expected {manifest.checksum}, got {actual}"
            )

    return manifest, contents


def list_archive_contents(archive_path: Path) -> list[str]:
    """Return a list of file paths in the archive."""
    with zipfile.ZipFile(archive_path, "r") as zf:
        return zf.namelist()
