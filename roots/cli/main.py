"""Roots CLI — command-line interface for the process orchestration framework."""

from __future__ import annotations

import json
from typing import Optional

import typer
import uvicorn

from roots import Roots, __version__
from roots.api.app import create_app
from roots.events.sinks import StdoutSink
from roots.storage.sqlite import SqliteBackend
from roots.storage.postgres import PostgresBackend

app = typer.Typer(help="Roots — A process orchestration framework.")


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
        "0.0.0.0",
        help="Host to bind the server to.",
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


def _parse_work_item(value: str) -> dict:
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


@app.command()
def status(ctx: typer.Context) -> None:
    """Show status of runs."""
    typer.echo("status command (not yet implemented)")


@app.command()
def agents(ctx: typer.Context) -> None:
    """List and inspect registered agents."""
    typer.echo("agents command (not yet implemented)")


if __name__ == "__main__":
    app()
