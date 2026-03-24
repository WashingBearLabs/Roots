"""Tests for CLI Scaffolding (US-001)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner
from unittest.mock import AsyncMock, patch

from roots.cli.main import app, _is_postgres_dsn, _create_roots_from_options

runner = CliRunner()


def test_help_shows_all_subcommands():
    """roots --help shows all subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("serve", "validate", "run", "status", "agents"):
        assert cmd in result.output


def test_version_flag():
    """roots --version shows version."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "roots 0.1.0" in result.output


def test_storage_option_default():
    """--storage defaults to roots.db."""
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0


def test_verbose_option():
    """-v / --verbose is accepted."""
    result = runner.invoke(app, ["--verbose", "serve"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["-v", "serve"])
    assert result.exit_code == 0


def test_storage_option_custom():
    """--storage accepts custom paths."""
    result = runner.invoke(app, ["--storage", "/tmp/custom.db", "serve"])
    assert result.exit_code == 0


def test_subcommand_help_serve():
    """roots serve --help works."""
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "Start the Roots HTTP API server" in result.output


def test_subcommand_help_validate():
    """roots validate --help works."""
    result = runner.invoke(app, ["validate", "--help"])
    assert result.exit_code == 0
    assert "Validate process definition YAML files" in result.output


def test_is_postgres_dsn():
    """Postgres DSN detection works correctly."""
    assert _is_postgres_dsn("postgresql://localhost/roots") is True
    assert _is_postgres_dsn("postgres://localhost/roots") is True
    assert _is_postgres_dsn("roots.db") is False
    assert _is_postgres_dsn("/tmp/data.db") is False


@pytest.mark.asyncio
async def test_create_roots_sqlite():
    """Helper creates Roots with SqliteBackend for file paths."""
    roots = await _create_roots_from_options(":memory:")
    try:
        from roots.storage.sqlite import SqliteBackend

        assert isinstance(roots.storage, SqliteBackend)
    finally:
        await roots.close()


@pytest.mark.asyncio
async def test_create_roots_postgres_dsn():
    """Helper detects postgres DSN and uses PostgresBackend."""
    with patch(
        "roots.cli.main.PostgresBackend"
    ) as mock_cls:
        mock_backend = AsyncMock()
        mock_cls.return_value = mock_backend
        roots = await _create_roots_from_options(
            "postgresql://localhost/roots"
        )
        mock_cls.assert_called_once_with("postgresql://localhost/roots")
        mock_backend.initialize.assert_awaited_once()
        await roots.close()
