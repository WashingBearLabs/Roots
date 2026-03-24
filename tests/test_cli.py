"""Tests for CLI (US-001 scaffolding + US-002 serve command + US-003 validate)."""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner
from unittest.mock import AsyncMock, MagicMock, patch

from roots.cli.main import app, _is_postgres_dsn, _create_roots_from_options

runner = CliRunner()


def _mock_serve():
    """Return stacked patches that mock create_roots_from_options and uvicorn.run."""
    mock_roots = MagicMock()
    return (
        patch("roots.cli.main.create_roots_from_options", return_value=mock_roots),
        patch("roots.cli.main.uvicorn.run"),
    )


# --- US-001: CLI Scaffolding ---


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
    p1, p2 = _mock_serve()
    with p1, p2:
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0


def test_verbose_option():
    """-v / --verbose is accepted."""
    p1, p2 = _mock_serve()
    with p1, p2:
        result = runner.invoke(app, ["--verbose", "serve"])
        assert result.exit_code == 0
        result = runner.invoke(app, ["-v", "serve"])
        assert result.exit_code == 0


def test_storage_option_custom():
    """--storage accepts custom paths."""
    p1, p2 = _mock_serve()
    with p1, p2:
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


# --- US-002: roots serve Command ---


def test_serve_starts_uvicorn_with_defaults():
    """serve calls uvicorn.run with default host/port."""
    mock_roots = MagicMock()
    mock_app = MagicMock()
    with (
        patch("roots.cli.main.create_roots_from_options", return_value=mock_roots),
        patch("roots.cli.main.uvicorn.run") as mock_uvicorn,
        patch("roots.cli.main.create_app", return_value=mock_app),
    ):
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0
        mock_uvicorn.assert_called_once_with(
            mock_app, host="0.0.0.0", port=8200, log_level="info", reload=False,
        )


def test_serve_custom_host_port():
    """serve accepts --host and --port options."""
    mock_roots = MagicMock()
    mock_app = MagicMock()
    with (
        patch("roots.cli.main.create_roots_from_options", return_value=mock_roots),
        patch("roots.cli.main.uvicorn.run") as mock_uvicorn,
        patch("roots.cli.main.create_app", return_value=mock_app),
    ):
        result = runner.invoke(app, ["serve", "--host", "127.0.0.1", "--port", "9000"])
        assert result.exit_code == 0
        mock_uvicorn.assert_called_once_with(
            mock_app, host="127.0.0.1", port=9000, log_level="info", reload=False,
        )


def test_serve_reload_flag():
    """serve accepts --reload flag."""
    mock_roots = MagicMock()
    mock_app = MagicMock()
    with (
        patch("roots.cli.main.create_roots_from_options", return_value=mock_roots),
        patch("roots.cli.main.uvicorn.run") as mock_uvicorn,
        patch("roots.cli.main.create_app", return_value=mock_app),
    ):
        result = runner.invoke(app, ["serve", "--reload"])
        assert result.exit_code == 0
        mock_uvicorn.assert_called_once_with(
            mock_app, host="0.0.0.0", port=8200, log_level="info", reload=True,
        )


def test_serve_banner_shows_url_and_storage():
    """serve prints startup banner with URL and storage backend."""
    mock_roots = MagicMock()
    with (
        patch("roots.cli.main.create_roots_from_options", return_value=mock_roots),
        patch("roots.cli.main.uvicorn.run"),
    ):
        result = runner.invoke(app, ["serve", "--host", "127.0.0.1", "--port", "9000"])
        assert result.exit_code == 0
        assert "http://127.0.0.1:9000" in result.output
        assert "roots.db" in result.output


def test_serve_banner_storage_custom():
    """serve banner shows custom storage path."""
    mock_roots = MagicMock()
    with (
        patch("roots.cli.main.create_roots_from_options", return_value=mock_roots),
        patch("roots.cli.main.uvicorn.run"),
    ):
        result = runner.invoke(app, ["--storage", "/data/my.db", "serve"])
        assert result.exit_code == 0
        assert "/data/my.db" in result.output


def test_serve_banner_postgres_label():
    """serve banner shows 'PostgreSQL' for postgres DSN."""
    mock_roots = MagicMock()
    with (
        patch("roots.cli.main.create_roots_from_options", return_value=mock_roots),
        patch("roots.cli.main.uvicorn.run"),
    ):
        result = runner.invoke(
            app, ["--storage", "postgresql://localhost/roots", "serve"]
        )
        assert result.exit_code == 0
        assert "PostgreSQL" in result.output


def test_serve_configures_storage():
    """serve passes --storage option to create_roots_from_options."""
    mock_roots = MagicMock()
    with (
        patch(
            "roots.cli.main.create_roots_from_options", return_value=mock_roots,
        ) as mock_create,
        patch("roots.cli.main.uvicorn.run"),
    ):
        result = runner.invoke(app, ["--storage", "/tmp/test.db", "serve"])
        assert result.exit_code == 0
        mock_create.assert_called_once_with("/tmp/test.db")


def test_serve_creates_app_with_roots():
    """serve passes roots instance to create_app."""
    mock_roots = MagicMock()
    mock_app = MagicMock()
    with (
        patch("roots.cli.main.create_roots_from_options", return_value=mock_roots),
        patch("roots.cli.main.uvicorn.run"),
        patch("roots.cli.main.create_app", return_value=mock_app) as mock_create_app,
    ):
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0
        mock_create_app.assert_called_once_with(mock_roots)


# --- US-003: roots validate Command ---


def _valid_process_dict():
    """Return a minimal valid process definition dict."""
    return {
        "id": "proc-1",
        "name": "Test Process",
        "version": "1.0.0",
        "entry_point": "start",
        "nodes": [
            {
                "id": "start",
                "type": "agent",
                "label": "Start Agent",
                "config": {"agent": "summarizer", "output_key": "summary"},
            },
            {
                "id": "done",
                "type": "end",
                "label": "End",
                "config": {"status": "completed"},
            },
        ],
        "edges": [{"from": "start", "to": "done"}],
    }


def test_validate_valid_file(tmp_path):
    """validate shows green checkmark and exits 0 for valid file."""
    f = tmp_path / "good.yaml"
    f.write_text(yaml.dump(_valid_process_dict()))
    result = runner.invoke(app, ["validate", str(f)])
    assert result.exit_code == 0
    assert "✓" in result.output
    assert str(f) in result.output


def test_validate_invalid_file(tmp_path):
    """validate shows red X with error details and exits 1 for invalid file."""
    f = tmp_path / "bad.yaml"
    f.write_text("id: proc-bad\nname: Bad\n")
    result = runner.invoke(app, ["validate", str(f)])
    assert result.exit_code == 1
    assert "✗" in result.output
    assert str(f) in result.output


def test_validate_invalid_file_shows_error_details(tmp_path):
    """validate error output includes field details."""
    data = _valid_process_dict()
    data["entry_point"] = "nonexistent"
    f = tmp_path / "bad.yaml"
    f.write_text(yaml.dump(data))
    result = runner.invoke(app, ["validate", str(f)])
    assert result.exit_code == 1
    assert "entry_point" in result.output or "nonexistent" in result.output


def test_validate_directory(tmp_path):
    """validate scans directory for all .yaml/.yml files."""
    good = tmp_path / "good.yaml"
    good.write_text(yaml.dump(_valid_process_dict()))
    bad = tmp_path / "bad.yml"
    bad.write_text("not: a process\n")
    txt = tmp_path / "readme.txt"
    txt.write_text("ignore me")
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 1
    assert "✓" in result.output
    assert "✗" in result.output
    # txt file should not appear
    assert "readme.txt" not in result.output


def test_validate_directory_all_valid(tmp_path):
    """validate exits 0 when all files in directory are valid."""
    for name in ("a.yaml", "b.yml"):
        (tmp_path / name).write_text(yaml.dump(_valid_process_dict()))
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 0


def test_validate_nonexistent_path():
    """validate exits 1 for nonexistent path."""
    result = runner.invoke(app, ["validate", "/nonexistent/path.yaml"])
    assert result.exit_code == 1


def test_validate_yaml_syntax_error(tmp_path):
    """validate reports YAML syntax errors."""
    f = tmp_path / "broken.yaml"
    f.write_text(":\n  bad: [yaml\n")
    result = runner.invoke(app, ["validate", str(f)])
    assert result.exit_code == 1
    assert "✗" in result.output


def test_validate_empty_directory(tmp_path):
    """validate exits 1 for directory with no YAML files."""
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 1


def test_validate_error_includes_node_context(tmp_path):
    """validate errors include node ID context for node-level failures."""
    data = _valid_process_dict()
    # Remove required config field to trigger node-level validation error
    data["nodes"][0]["config"] = {}
    f = tmp_path / "bad.yaml"
    f.write_text(yaml.dump(data))
    result = runner.invoke(app, ["validate", str(f)])
    assert result.exit_code == 1
    assert "start" in result.output or "agent" in result.output
