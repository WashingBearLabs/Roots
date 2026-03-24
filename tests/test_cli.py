"""Tests for CLI (US-001 scaffolding + US-002 serve + US-003 validate + US-004 run)."""

from __future__ import annotations

import json

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


# --- US-004: roots run Command ---


def _make_mock_roots(final_status="completed"):
    """Return a mock Roots instance for run tests."""
    mock = AsyncMock()
    mock.load_process = AsyncMock()
    mock.start_run = AsyncMock(
        return_value=MagicMock(id="run-123")
    )
    mock.execute_run = AsyncMock()
    mock.get_run = AsyncMock(
        return_value=MagicMock(status=final_status)
    )
    mock.close = AsyncMock()
    return mock


def _patch_run(mock_roots):
    """Return a context manager that patches Roots construction and backends."""
    return patch(
        "roots.cli.main._run_process",
        side_effect=None,
    )


def _write_process_yaml(tmp_path):
    """Write a minimal valid process YAML file and return its path."""
    f = tmp_path / "proc.yaml"
    f.write_text(yaml.dump(_valid_process_dict()))
    return f


def test_run_with_file_path(tmp_path):
    """run loads process from file path and executes."""
    proc_file = _write_process_yaml(tmp_path)
    mock_roots = _make_mock_roots("completed")

    with (
        patch("roots.cli.main.SqliteBackend") as mock_backend_cls,
        patch("roots.cli.main.Roots", return_value=mock_roots),
    ):
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend

        result = runner.invoke(app, ["run", str(proc_file)])
        assert result.exit_code == 0
        mock_roots.load_process.assert_awaited_once_with(str(proc_file))
        mock_roots.start_run.assert_awaited_once()
        mock_roots.execute_run.assert_awaited_once_with("run-123")
        mock_roots.close.assert_awaited_once()


def test_run_with_process_id():
    """run looks up process by ID when argument is not a file path."""
    mock_roots = _make_mock_roots("completed")

    with (
        patch("roots.cli.main.SqliteBackend") as mock_backend_cls,
        patch("roots.cli.main.Roots", return_value=mock_roots),
    ):
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend

        result = runner.invoke(app, ["run", "my-process-id"])
        assert result.exit_code == 0
        mock_roots.load_process.assert_not_awaited()
        mock_roots.start_run.assert_awaited_once()
        args = mock_roots.start_run.call_args
        assert args[0][0] == "my-process-id"


def test_run_work_item_json_string():
    """run accepts --work-item as JSON string."""
    mock_roots = _make_mock_roots("completed")

    with (
        patch("roots.cli.main.SqliteBackend") as mock_backend_cls,
        patch("roots.cli.main.Roots", return_value=mock_roots),
    ):
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend

        result = runner.invoke(
            app, ["run", "proc-1", "--work-item", '{"key": "value"}']
        )
        assert result.exit_code == 0
        args = mock_roots.start_run.call_args
        assert args[0][1] == {"key": "value"}


def test_run_work_item_from_file(tmp_path):
    """run accepts --work-item as path to a JSON file."""
    mock_roots = _make_mock_roots("completed")
    wi_file = tmp_path / "work.json"
    wi_file.write_text(json.dumps({"from": "file"}))

    with (
        patch("roots.cli.main.SqliteBackend") as mock_backend_cls,
        patch("roots.cli.main.Roots", return_value=mock_roots),
    ):
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend

        result = runner.invoke(
            app, ["run", "proc-1", "--work-item", str(wi_file)]
        )
        assert result.exit_code == 0
        args = mock_roots.start_run.call_args
        assert args[0][1] == {"from": "file"}


def test_run_wait_blocks_and_prints_result():
    """run --wait (default) blocks until completion and prints result."""
    mock_roots = _make_mock_roots("completed")

    with (
        patch("roots.cli.main.SqliteBackend") as mock_backend_cls,
        patch("roots.cli.main.Roots", return_value=mock_roots),
    ):
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend

        result = runner.invoke(app, ["run", "proc-1"])
        assert result.exit_code == 0
        assert "run-123" in result.output
        assert "completed" in result.output
        mock_roots.execute_run.assert_awaited_once()


def test_run_no_wait_prints_id_and_exits():
    """run --no-wait prints run ID and exits immediately."""
    mock_roots = _make_mock_roots("completed")

    with (
        patch("roots.cli.main.SqliteBackend") as mock_backend_cls,
        patch("roots.cli.main.Roots", return_value=mock_roots),
    ):
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend

        result = runner.invoke(app, ["run", "proc-1", "--no-wait"])
        assert result.exit_code == 0
        assert "run-123" in result.output
        mock_roots.execute_run.assert_not_awaited()


def test_run_exit_code_failed():
    """run exits with code 1 when run fails."""
    mock_roots = _make_mock_roots("failed")

    with (
        patch("roots.cli.main.SqliteBackend") as mock_backend_cls,
        patch("roots.cli.main.Roots", return_value=mock_roots),
    ):
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend

        result = runner.invoke(app, ["run", "proc-1"])
        assert result.exit_code == 1


def test_run_exit_code_paused():
    """run exits with code 2 when run is paused (checkpoint)."""
    mock_roots = _make_mock_roots("paused")

    with (
        patch("roots.cli.main.SqliteBackend") as mock_backend_cls,
        patch("roots.cli.main.Roots", return_value=mock_roots),
    ):
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend

        result = runner.invoke(app, ["run", "proc-1"])
        assert result.exit_code == 2


def test_run_events_use_stdout_sink():
    """run creates Roots with StdoutSink for event output."""
    mock_roots = _make_mock_roots("completed")

    with (
        patch("roots.cli.main.SqliteBackend") as mock_backend_cls,
        patch("roots.cli.main.Roots", return_value=mock_roots) as mock_roots_cls,
    ):
        mock_backend = AsyncMock()
        mock_backend_cls.return_value = mock_backend

        result = runner.invoke(app, ["run", "proc-1"])
        assert result.exit_code == 0
        call_kwargs = mock_roots_cls.call_args[1]
        sinks = call_kwargs["event_sinks"]
        assert len(sinks) == 1
        from roots.events.sinks import StdoutSink

        assert isinstance(sinks[0], StdoutSink)


def test_run_help():
    """roots run --help works."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "Execute a process run" in result.output


# --- US-005: roots status and roots agents Commands ---


def _make_run_record(
    run_id="run-001",
    process_id="proc-1",
    status="running",
    current_node_id="node-a",
):
    """Return a RunRecord for testing."""
    from datetime import datetime, timezone
    from roots.storage.base import RunRecord

    now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    return RunRecord(
        id=run_id,
        process_id=process_id,
        status=status,
        current_node_id=current_node_id,
        work_item_state={"key": "value"},
        created_at=now,
        updated_at=now,
    )


def _make_history_event(event_type="node_entered", node_id="node-a"):
    """Return a HistoryEvent for testing."""
    from datetime import datetime, timezone
    from roots.storage.base import HistoryEvent

    now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    return HistoryEvent(
        id=1,
        run_id="run-001",
        event_type=event_type,
        node_id=node_id,
        data={},
        created_at=now,
    )


def _patch_status_backend(
    runs=None, run_detail=None, history_events=None
):
    """Return context managers that patch the backend for status tests."""
    mock_backend = AsyncMock()
    mock_backend.list_runs = AsyncMock(return_value=runs or [])
    mock_backend.get_run = AsyncMock(return_value=run_detail)
    mock_backend.list_history_events = AsyncMock(
        return_value=history_events or []
    )
    mock_backend.close = AsyncMock()
    return patch("roots.cli.main.SqliteBackend", return_value=mock_backend)


def test_status_lists_runs():
    """status lists runs in a formatted table."""
    runs = [
        _make_run_record("run-001", "proc-1", "running", "node-a"),
        _make_run_record("run-002", "proc-2", "completed", None),
    ]
    with _patch_status_backend(runs=runs):
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "run-001" in result.output
        assert "run-002" in result.output
        assert "proc-1" in result.output
        assert "running" in result.output


def test_status_empty():
    """status shows message when no runs found."""
    with _patch_status_backend(runs=[]):
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "No runs found" in result.output


def test_status_filter_process():
    """status --process filters by process ID."""
    runs = [_make_run_record("run-001", "proc-1")]
    with _patch_status_backend(runs=runs) as mock_cls:
        result = runner.invoke(app, ["status", "--process", "proc-1"])
        assert result.exit_code == 0
        mock_backend = mock_cls.return_value
        mock_backend.list_runs.assert_awaited_once_with(
            process_id="proc-1", status=None
        )


def test_status_filter_status():
    """status --status filters by run status."""
    runs = [_make_run_record("run-001", status="completed")]
    with _patch_status_backend(runs=runs) as mock_cls:
        result = runner.invoke(app, ["status", "--status", "completed"])
        assert result.exit_code == 0
        mock_backend = mock_cls.return_value
        mock_backend.list_runs.assert_awaited_once_with(
            process_id=None, status="completed"
        )


def test_status_detail_view():
    """status <run_id> shows detailed run info."""
    run = _make_run_record("run-001", "proc-1", "running", "node-a")
    events = [_make_history_event("node_entered", "node-a")]
    with _patch_status_backend(run_detail=run, history_events=events):
        result = runner.invoke(app, ["status", "run-001"])
        assert result.exit_code == 0
        assert "run-001" in result.output
        assert "proc-1" in result.output
        assert "running" in result.output
        assert "node-a" in result.output
        assert "node_entered" in result.output


def test_status_detail_not_found():
    """status <run_id> exits 1 when run not found."""
    with _patch_status_backend(run_detail=None):
        result = runner.invoke(app, ["status", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


def test_status_table_columns():
    """status table includes expected columns."""
    runs = [_make_run_record()]
    with _patch_status_backend(runs=runs):
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "run_id" in result.output
        assert "process_id" in result.output
        assert "status" in result.output
        assert "current_node" in result.output
        assert "created_at" in result.output
        assert "updated_at" in result.output


def _patch_agents_backend(agents=None):
    """Return context manager that patches backend for agents tests."""
    mock_backend = AsyncMock()
    mock_backend.list_agents = AsyncMock(return_value=agents or [])
    mock_backend.close = AsyncMock()
    return patch("roots.cli.main.SqliteBackend", return_value=mock_backend)


def test_agents_lists_agents():
    """agents lists all registered agents."""
    agent_list = [
        {
            "name": "summarizer",
            "type": "local",
            "callback_url": None,
            "created_at": "2025-01-15T12:00:00+00:00",
        },
        {
            "name": "classifier",
            "type": "remote",
            "callback_url": "http://agent.example.com/classify",
            "created_at": "2025-01-15T12:00:00+00:00",
        },
    ]
    with _patch_agents_backend(agents=agent_list):
        result = runner.invoke(app, ["agents"])
        assert result.exit_code == 0
        assert "summarizer" in result.output
        assert "classifier" in result.output
        assert "local" in result.output
        assert "remote" in result.output


def test_agents_empty():
    """agents shows message when no agents registered."""
    with _patch_agents_backend(agents=[]):
        result = runner.invoke(app, ["agents"])
        assert result.exit_code == 0
        assert "No agents registered" in result.output


def test_agents_table_columns():
    """agents table includes expected columns."""
    agent_list = [
        {
            "name": "test-agent",
            "type": "local",
            "callback_url": None,
            "created_at": "2025-01-15T12:00:00+00:00",
        }
    ]
    with _patch_agents_backend(agents=agent_list):
        result = runner.invoke(app, ["agents"])
        assert result.exit_code == 0
        assert "name" in result.output
        assert "type" in result.output
        assert "callback_url" in result.output
        assert "registered_at" in result.output


def test_agents_health_pings_remote():
    """agents health pings remote agents and shows status."""
    agent_list = [
        {
            "name": "remote-agent",
            "type": "remote",
            "callback_url": "http://agent.example.com/health",
            "created_at": "2025-01-15T12:00:00+00:00",
        },
    ]
    with _patch_agents_backend(agents=agent_list):
        with patch("roots.cli.main.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(
                return_value=False
            )
            mock_client.get = AsyncMock()

            result = runner.invoke(app, ["agents", "health"])
            assert result.exit_code == 0
            assert "remote-agent" in result.output
            assert "healthy" in result.output


def test_agents_health_unhealthy():
    """agents health shows unhealthy for unreachable agents."""
    agent_list = [
        {
            "name": "broken-agent",
            "type": "remote",
            "callback_url": "http://unreachable.example.com",
            "created_at": "2025-01-15T12:00:00+00:00",
        },
    ]
    with _patch_agents_backend(agents=agent_list):
        with patch("roots.cli.main.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(
                return_value=False
            )
            mock_client.get = AsyncMock(
                side_effect=Exception("Connection refused")
            )

            result = runner.invoke(app, ["agents", "health"])
            assert result.exit_code == 0
            assert "broken-agent" in result.output
            assert "unhealthy" in result.output


def test_agents_health_empty():
    """agents health shows message when no agents registered."""
    with _patch_agents_backend(agents=[]):
        result = runner.invoke(app, ["agents", "health"])
        assert result.exit_code == 0
        assert "No agents registered" in result.output


def test_agents_health_local_always_healthy():
    """agents health marks local agents as healthy without pinging."""
    agent_list = [
        {
            "name": "local-agent",
            "type": "local",
            "callback_url": None,
            "created_at": "2025-01-15T12:00:00+00:00",
        }
    ]
    with _patch_agents_backend(agents=agent_list):
        result = runner.invoke(app, ["agents", "health"])
        assert result.exit_code == 0
        assert "local-agent" in result.output
        assert "healthy" in result.output


def test_status_help():
    """roots status --help works."""
    result = runner.invoke(app, ["status", "--help"])
    assert result.exit_code == 0
    assert "Show status of runs" in result.output


def test_agents_help():
    """roots agents --help works."""
    result = runner.invoke(app, ["agents", "--help"])
    assert result.exit_code == 0
