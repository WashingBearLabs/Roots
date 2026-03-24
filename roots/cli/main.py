"""Roots CLI — command-line interface for the process orchestration framework."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx
import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from roots import Roots, __version__
from roots.api.app import create_app
from roots.events.sinks import StdoutSink
from roots.storage.base import RunRecord
from roots.storage.sqlite import SqliteBackend
from roots.storage.postgres import PostgresBackend

app = typer.Typer(help="Roots — A process orchestration framework.")
agents_app = typer.Typer(help="List and inspect registered agents.")
app.add_typer(agents_app, name="agents")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"roots {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    storage: str = typer.Option(
        "roots.db",
        help="Storage backend: SQLite file path or PostgreSQL DSN.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output.",
    ),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Roots — A process orchestration framework."""
    ctx.ensure_object(dict)
    ctx.obj["storage"] = storage
    ctx.obj["verbose"] = verbose


def _is_postgres_dsn(value: str) -> bool:
    """Detect whether a storage string is a PostgreSQL DSN."""
    return value.startswith("postgresql://") or value.startswith("postgres://")


async def _create_roots_from_options(storage: str) -> Roots:
    """Create and initialize a Roots instance from CLI options."""
    if _is_postgres_dsn(storage):
        backend = PostgresBackend(storage)
    else:
        backend = SqliteBackend(storage)
    await backend.initialize()
    return Roots(storage=backend)


def create_roots_from_options(storage: str) -> Roots:
    """Synchronous wrapper for creating a Roots instance from CLI options."""
    import asyncio

    return asyncio.run(_create_roots_from_options(storage))


@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option(
        "127.0.0.1",
        help="Host to bind the server to. Use 0.0.0.0 to expose to network (no auth in v1 — use with caution).",
    ),
    port: int = typer.Option(
        8200,
        help="Port to listen on.",
    ),
    reload: bool = typer.Option(
        False,
        help="Enable auto-reload for development.",
    ),
) -> None:
    """Start the Roots HTTP API server."""
    storage = ctx.obj["storage"]
    roots_instance = create_roots_from_options(storage)

    app_instance = create_app(roots_instance)

    backend_label = storage if not _is_postgres_dsn(storage) else "PostgreSQL"
    typer.echo(f"Roots server starting...")
    typer.echo(f"  URL:     http://{host}:{port}")
    typer.echo(f"  Storage: {backend_label}")
    typer.echo("")

    uvicorn.run(app_instance, host=host, port=port, log_level="info", reload=reload)


@app.command()
def validate(
    ctx: typer.Context,
    path: str = typer.Argument(
        ...,
        help="YAML file or directory to validate.",
    ),
) -> None:
    """Validate process definition YAML files."""
    from pathlib import Path as _Path

    from roots.core.validator import validate_process_yaml

    target = _Path(path)
    if target.is_dir():
        files = sorted(
            p for p in target.iterdir()
            if p.suffix in (".yaml", ".yml") and p.is_file()
        )
    elif target.is_file():
        files = [target]
    else:
        typer.echo(typer.style(f"Path not found: {path}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    if not files:
        typer.echo(typer.style(f"No YAML files found in {path}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    has_errors = False
    for filepath in files:
        errors = validate_process_yaml(filepath)
        if errors:
            has_errors = True
            typer.echo(typer.style(f"✗ {filepath}", fg=typer.colors.RED))
            for error in errors:
                typer.echo(f"  {error}")
        else:
            typer.echo(typer.style(f"✓ {filepath}", fg=typer.colors.GREEN))

    if has_errors:
        raise typer.Exit(code=1)


def _parse_work_item(value: str) -> dict[str, Any]:
    """Parse a work item from a JSON string or file path."""
    from pathlib import Path as _Path

    path = _Path(value)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


async def _run_process(
    storage: str, process_arg: str, work_item_str: str, wait: bool
) -> None:
    """Execute a process run asynchronously."""
    from pathlib import Path as _Path

    work_item_data = _parse_work_item(work_item_str)

    if _is_postgres_dsn(storage):
        backend = PostgresBackend(storage)
    else:
        backend = SqliteBackend(storage)
    await backend.initialize()

    roots_instance = Roots(storage=backend, event_sinks=[StdoutSink(compact=True)])

    try:
        target = _Path(process_arg)
        if target.is_file():
            await roots_instance.load_process(str(target))
            from roots.core.validator import load_process_yaml

            process_def = load_process_yaml(str(target))
            process_id = process_def.id
        else:
            process_id = process_arg

        run_record = await roots_instance.start_run(process_id, work_item_data)

        if not wait:
            typer.echo(run_record.id)
            return

        await roots_instance.execute_run(run_record.id)

        final_run = await roots_instance.get_run(run_record.id)
        assert final_run is not None
        final_status = final_run.status

        typer.echo(f"Run {run_record.id} {final_status}")

        if final_status == "completed":
            raise typer.Exit(code=0)
        elif final_status == "paused":
            raise typer.Exit(code=2)
        else:
            raise typer.Exit(code=1)
    finally:
        await roots_instance.close()


@app.command()
def run(
    ctx: typer.Context,
    process: str = typer.Argument(
        ...,
        help="Process ID or path to a YAML process definition file.",
    ),
    work_item: str = typer.Option(
        "{}",
        "--work-item",
        help="Work item as a JSON string or path to a JSON file.",
    ),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="Block until run completes (default) or exit immediately.",
    ),
) -> None:
    """Execute a process run."""
    import asyncio

    asyncio.run(_run_process(ctx.obj["storage"], process, work_item, wait))


async def _list_runs(
    storage: str,
    process_filter: str | None,
    status_filter: str | None,
    limit: int,
) -> list[RunRecord]:
    """Fetch runs from storage with optional filters."""
    if _is_postgres_dsn(storage):
        backend = PostgresBackend(storage)
    else:
        backend = SqliteBackend(storage)
    await backend.initialize()
    try:
        runs = await backend.list_runs(
            process_id=process_filter, status=status_filter
        )
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[:limit]
    finally:
        await backend.close()


async def _get_run_detail(storage: str, run_id: str) -> None:
    """Print detailed info for a single run."""
    if _is_postgres_dsn(storage):
        backend = PostgresBackend(storage)
    else:
        backend = SqliteBackend(storage)
    await backend.initialize()
    try:
        run = await backend.get_run(run_id)
        if run is None:
            typer.echo(typer.style(f"Run not found: {run_id}", fg=typer.colors.RED))
            raise typer.Exit(code=1)

        console = Console()
        console.print(f"\n[bold]Run:[/bold] {run.id}")
        console.print(f"[bold]Process:[/bold] {run.process_id}")
        console.print(f"[bold]Status:[/bold] {run.status}")
        console.print(f"[bold]Current Node:[/bold] {run.current_node_id or '—'}")
        console.print(f"[bold]Created:[/bold] {run.created_at}")
        console.print(f"[bold]Updated:[/bold] {run.updated_at}")

        # Work item state (truncated)
        state_str = json.dumps(run.work_item_state)
        if len(state_str) > 200:
            state_str = state_str[:200] + "..."
        console.print(f"\n[bold]Work Item State:[/bold] {state_str}")

        # Recent history events
        events = await backend.list_history_events(run.id)
        if events:
            console.print(f"\n[bold]Recent History ({len(events)} events):[/bold]")
            table = Table()
            table.add_column("Type")
            table.add_column("Node")
            table.add_column("Time")
            for event in events[-10:]:
                table.add_row(
                    event.event_type,
                    event.node_id or "—",
                    str(event.created_at),
                )
            console.print(table)
    finally:
        await backend.close()


@app.command()
def status(
    ctx: typer.Context,
    run_id: Optional[str] = typer.Argument(
        None,
        help="Run ID for detailed view. Omit to list all runs.",
    ),
    process: Optional[str] = typer.Option(
        None,
        "--process",
        help="Filter by process ID.",
    ),
    status_filter: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by run status.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        help="Maximum number of runs to show.",
    ),
) -> None:
    """Show status of runs."""
    import asyncio

    storage = ctx.obj["storage"]

    if run_id is not None:
        asyncio.run(_get_run_detail(storage, run_id))
        return

    runs = asyncio.run(_list_runs(storage, process, status_filter, limit))

    if not runs:
        typer.echo("No runs found.")
        return

    console = Console()
    table = Table()
    table.add_column("run_id")
    table.add_column("process_id")
    table.add_column("status")
    table.add_column("current_node")
    table.add_column("created_at")
    table.add_column("updated_at")

    for r in runs:
        table.add_row(
            r.id,
            r.process_id,
            r.status,
            r.current_node_id or "—",
            str(r.created_at),
            str(r.updated_at),
        )

    console.print(table)


async def _list_agents(storage: str) -> list[dict[str, Any]]:
    """Fetch agents from storage."""
    if _is_postgres_dsn(storage):
        backend = PostgresBackend(storage)
    else:
        backend = SqliteBackend(storage)
    await backend.initialize()
    try:
        return await backend.list_agents()
    finally:
        await backend.close()


async def _check_agent_health(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ping remote agents and return health results."""
    results = []
    for agent in agents:
        agent_type = agent.get("type", "local")
        callback_url = agent.get("callback_url")
        entry = {
            "name": agent.get("name", "unknown"),
            "type": agent_type,
            "callback_url": callback_url,
        }
        if agent_type != "remote" or not callback_url:
            entry["status"] = "healthy"
            entry["response_time_ms"] = None
            results.append(entry)
            continue

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.get(callback_url)
            elapsed_ms = (time.monotonic() - start) * 1000
            entry["status"] = "healthy"
            entry["response_time_ms"] = round(elapsed_ms, 2)
        except Exception:
            elapsed_ms = (time.monotonic() - start) * 1000
            entry["status"] = "unhealthy"
            entry["response_time_ms"] = round(elapsed_ms, 2)
        results.append(entry)
    return results


@agents_app.callback(invoke_without_command=True)
def agents_list(
    ctx: typer.Context,
) -> None:
    """List all registered agents."""
    if ctx.invoked_subcommand is not None:
        return

    import asyncio

    storage = ctx.obj["storage"]
    agent_list = asyncio.run(_list_agents(storage))

    if not agent_list:
        typer.echo("No agents registered.")
        return

    console = Console()
    table = Table()
    table.add_column("name")
    table.add_column("type")
    table.add_column("callback_url")
    table.add_column("registered_at")

    for a in agent_list:
        table.add_row(
            a.get("name", ""),
            a.get("type", ""),
            a.get("callback_url", "—") or "—",
            a.get("created_at", "—"),
        )

    console.print(table)


@agents_app.command()
def health(
    ctx: typer.Context,
) -> None:
    """Check health of all remote agents."""
    import asyncio

    storage = ctx.obj["storage"]
    agent_list = asyncio.run(_list_agents(storage))

    if not agent_list:
        typer.echo("No agents registered.")
        return

    results = asyncio.run(_check_agent_health(agent_list))

    console = Console()
    table = Table()
    table.add_column("name")
    table.add_column("type")
    table.add_column("status")
    table.add_column("response_time_ms")

    for r in results:
        rt = str(r["response_time_ms"]) if r["response_time_ms"] is not None else "—"
        table.add_row(r["name"], r["type"], r["status"], rt)

    console.print(table)


if __name__ == "__main__":
    app()
