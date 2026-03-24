"""Roots CLI — command-line interface for the process orchestration framework."""

from __future__ import annotations

from typing import Optional

import typer
import uvicorn

from roots import Roots, __version__
from roots.api.app import create_app
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


@app.command()
def run(ctx: typer.Context) -> None:
    """Execute a process run."""
    typer.echo("run command (not yet implemented)")


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
