"""Load default agent implementations from a .root package archive."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from roots import Roots
    from roots.packaging.manifest import RootManifest


class _SyncRegistrationProxy:
    """Wraps a Roots instance to expose a synchronous register_agent method.

    Default agent modules are synchronous, but ``Roots.register_agent`` is
    async. This proxy calls the underlying sync registry method directly.
    """

    def __init__(self, roots: Roots) -> None:
        self._roots = roots

    def register_agent(
        self,
        name: str,
        callable: Any,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        self._roots._agent_registry.register_local(
            name, callable, input_schema=input_schema, output_schema=output_schema
        )


def load_defaults(
    archive_contents: dict[str, bytes],
    manifest: RootManifest,
    roots: Roots,
) -> list[str]:
    """Load and register default agent implementations from a package archive.

    Extracts the defaults directory from archive_contents, imports the module
    specified by ``manifest.defaults_module``, and calls its
    ``register_agents(roots)`` function.

    Returns a list of registered agent names, or an empty list when the
    manifest declares no defaults.
    """
    if not manifest.has_defaults:
        return []

    if manifest.defaults_module is None:
        return []

    print(
        "\u26a0 Loading default agents from package. "
        "Only install packages from trusted sources."
    )

    # Determine the archive prefix that corresponds to the module path.
    # e.g. "defaults.agents" -> files under "defaults/"
    module_parts = manifest.defaults_module.split(".")
    defaults_dir_prefix = module_parts[0] + "/"

    # Extract matching files to a temporary directory
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for name, data in archive_contents.items():
            if name.startswith(defaults_dir_prefix) or name == module_parts[0]:
                dest = tmp_path / name
                # Prevent path traversal (zip-slip)
                if not dest.resolve().is_relative_to(tmp_path.resolve()):
                    raise ValueError(
                        f"Path traversal detected in archive entry: {name!r}"
                    )
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)

        # Ensure __init__.py files exist for package imports
        _ensure_init_files(tmp_path / module_parts[0])

        # Resolve the module file directly — avoids sys.path manipulation
        # which could shadow stdlib/installed packages.
        module_name = manifest.defaults_module
        module_file = tmp_path / Path(*module_parts[:-1]) / (module_parts[-1] + ".py")
        if not module_file.exists():
            # Try as a package (__init__.py)
            module_file = tmp_path / Path(*module_parts) / "__init__.py"
        if not module_file.exists():
            raise ImportError(
                f"Could not find module '{module_name}' in extracted defaults"
            )

        try:
            spec = importlib.util.spec_from_file_location(
                module_name, module_file
            )
            if spec is None or spec.loader is None:
                raise ImportError(
                    f"Could not load module '{module_name}' from {module_file}"
                )
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            if not hasattr(module, "register_agents"):
                raise AttributeError(
                    f"Module '{module_name}' does not expose a "
                    f"'register_agents' function"
                )

            proxy = _SyncRegistrationProxy(roots)
            registered: list[str] = module.register_agents(proxy)
            return registered
        finally:
            # Clean up sys.modules to avoid stale references
            for key in list(sys.modules):
                if key == module_name or key.startswith(module_name + "."):
                    del sys.modules[key]
            # Also remove the top-level package if we added it
            top_pkg = module_parts[0]
            if top_pkg in sys.modules:
                del sys.modules[top_pkg]


def _ensure_init_files(package_dir: Path) -> None:
    """Create ``__init__.py`` files in the package directory tree if missing."""
    if not package_dir.is_dir():
        return
    init = package_dir / "__init__.py"
    if not init.exists():
        init.write_text("")
    for child in package_dir.iterdir():
        if child.is_dir():
            _ensure_init_files(child)
