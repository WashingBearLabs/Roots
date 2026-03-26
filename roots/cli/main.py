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
        agent_type: str = str(agent.get("type", "local"))
        callback_url: str | None = agent.get("callback_url")  # type: ignore[assignment]
        entry: dict[str, Any] = {
            "name": str(agent.get("name", "unknown")),
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
                await client.get(str(callback_url))
            elapsed_ms = (time.monotonic() - start) * 1000
            entry["status"] = "healthy"
            entry["response_time_ms"] = round(elapsed_ms, 2)
        except Exception:
            elapsed_ms = (time.monotonic() - start) * 1000
            entry["status"] = "unhealthy"
            entry["response_time_ms"] = round(elapsed_ms, 2)
        results.append(entry)
    return results


@app.command()
def pack(
    ctx: typer.Context,
    process_path: str = typer.Argument(
        ...,
        help="Path to a YAML process definition file.",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path for the .root file.",
    ),
    version: Optional[str] = typer.Option(
        None,
        "--version",
        help="Package version (semver). Defaults to process version.",
    ),
    author: Optional[str] = typer.Option(
        None,
        "--author",
        help="Author name for the manifest.",
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="Package description. Defaults to process description.",
    ),
    include_defaults: Optional[str] = typer.Option(
        None,
        "--include-defaults",
        help="Path to a defaults directory to bundle into the package.",
    ),
    scaffold_defaults: bool = typer.Option(
        False,
        "--scaffold-defaults",
        help="Generate a defaults/ directory with stub agent implementations.",
    ),
) -> None:
    """Pack a process into a distributable .root package."""
    if scaffold_defaults:
        from roots.packaging.scaffold import scaffold_defaults as _scaffold

        try:
            defaults_dir = _scaffold(process_path)
        except Exception as exc:
            typer.echo(typer.style(f"Error: {exc}", fg=typer.colors.RED))
            raise typer.Exit(code=1)

        console = Console()
        console.print(f"\n[bold green]Scaffolded defaults:[/bold green] {defaults_dir}")
        agents_file = defaults_dir / "agents.py"
        if agents_file.exists():
            # Count stub functions
            content = agents_file.read_text()
            stub_count = content.count("async def ")
            console.print(f"  Agent stubs:  {stub_count}")
        console.print("  Next: fill in the stubs, then pack with --include-defaults defaults/")
        return

    from roots.packaging.pack import pack_process

    try:
        result = pack_process(
            process_path=process_path,
            output_path=output,
            version=version,
            author=author,
            description=description,
            include_defaults=include_defaults,
        )
    except Exception as exc:
        typer.echo(typer.style(f"Error: {exc}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    # Read the manifest back for summary
    from roots.packaging.archive import read_archive

    manifest, _contents = read_archive(result)
    size_kb = result.stat().st_size / 1024

    console = Console()
    console.print(f"\n[bold green]Package created:[/bold green] {result}")
    console.print(f"  Name:             {manifest.name}")
    console.print(f"  Version:          {manifest.package_version}")
    if manifest.author:
        console.print(f"  Author:           {manifest.author}")
    console.print(f"  Agent contracts:  {len(manifest.agent_contracts)}")
    console.print(f"  Config overrides: {len(manifest.config_overrides)}")
    console.print(f"  Archive size:     {size_kb:.1f} KB")


@app.command()
def inspect(
    ctx: typer.Context,
    package_path: str = typer.Argument(
        ...,
        help="Path to a .root package file.",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Output raw manifest as JSON (for scripting).",
    ),
) -> None:
    """Inspect a .root package and display its contents."""
    from roots.packaging.inspect import inspect_package

    try:
        inspect_package(package_path, output_json=output_json)
    except FileNotFoundError as exc:
        typer.echo(typer.style(str(exc), fg=typer.colors.RED))
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(typer.style(f"Error: {exc}", fg=typer.colors.RED))
        raise typer.Exit(code=1)


@app.command()
def install(
    ctx: typer.Context,
    package_path: str = typer.Argument(
        ...,
        help="Path to a .root package file.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing process if it already exists.",
    ),
    apply_defaults: bool = typer.Option(
        False,
        "--apply-defaults",
        help="Load and register default agents from the package.",
    ),
) -> None:
    """Install a .root package into the local environment."""
    import asyncio
    from pathlib import Path as _Path

    from roots.packaging.installer import (
        ContractReport,
        install_package as _install_package,
        load_package,
    )
    from roots.packaging.manifest import RootManifest

    path = _Path(package_path)
    if not path.is_file():
        typer.echo(typer.style(f"File not found: {package_path}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    storage = ctx.obj["storage"]

    try:
        roots_instance = create_roots_from_options(storage)
    except Exception as exc:
        typer.echo(typer.style(f"Error initializing storage: {exc}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    try:
        report = asyncio.run(
            roots_instance.install_package(
                archive_path=path,
                force=force,
                apply_defaults=apply_defaults,
            )
        )
    except ValueError as exc:
        typer.echo(typer.style(f"Error: {exc}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    # Re-load manifest for display info
    manifest, process, _contents = load_package(path)

    console = Console()
    console.print(
        f"\n[bold green]Installed:[/bold green] {manifest.name} v{manifest.package_version}"
    )

    # Agent Status section
    console.print("\n[bold]Agent Status:[/bold]")
    for match in report.satisfied:
        agent_type = match.registration.get("agent_type", "local")
        console.print(
            f"  [green]✓[/green] {match.contract.name:<25} — Satisfied ({agent_type})"
        )
    for contract in report.missing:
        desc = contract.description or "Required agent"
        console.print(
            f"  [red]✗[/red] {contract.name:<25} — MISSING — {desc}"
        )
    for contract in report.optional_missing:
        console.print(
            f"  [yellow]~[/yellow] {contract.name:<25} — Optional, not registered"
        )
    for mismatch in report.schema_mismatches:
        console.print(
            f"  [red]![/red] {mismatch.agent_name:<25} — Schema mismatch ({mismatch.direction}): {mismatch.details}"
        )

    # Configurable Parameters section
    if manifest.config_overrides:
        console.print("\n[bold]Configurable Parameters:[/bold]")
        for override in manifest.config_overrides:
            console.print(
                f"  {override.path} = {override.default_value!r}  "
                f"(override with roots config set ...)"
            )

    # Next steps section
    missing_names = [c.name for c in report.missing]
    console.print("\n[bold]Next steps:[/bold]")
    step = 1
    if missing_names:
        console.print(
            f"  {step}. Register missing agents: {', '.join(missing_names)}"
        )
        step += 1
    if manifest.config_overrides:
        console.print(
            f"  {step}. Optionally configure parameters "
            f"(see: roots config list {process.id})"
        )
        step += 1
    console.print(
        f"  {step}. Run: roots run {process.id} --work-item '{{...}}'"
    )


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


config_app = typer.Typer(help="Manage configuration overrides for installed processes.")
app.add_typer(config_app, name="config")



@config_app.command("list")
def config_list(
    ctx: typer.Context,
    process_id: str = typer.Argument(
        ...,
        help="Process ID to list available overrides for.",
    ),
) -> None:
    """Show available configuration overrides for an installed process."""
    from roots.packaging.extractor import extract_config_overrides

    storage = ctx.obj["storage"]
    roots_instance = create_roots_from_options(storage)

    import asyncio

    process = asyncio.run(roots_instance.storage.get_process(process_id))
    if process is None:
        typer.echo(
            typer.style(f"Process '{process_id}' not found", fg=typer.colors.RED)
        )
        raise typer.Exit(code=1)

    overrides = extract_config_overrides(process)

    if not overrides:
        typer.echo(f"No configurable parameters for process '{process_id}'.")
        return

    console = Console()
    table = Table(title=f"Configurable Parameters: {process_id}")
    table.add_column("Path", style="cyan")
    table.add_column("Current Value")
    table.add_column("Type")
    table.add_column("Constraints")
    table.add_column("Description")

    for override in overrides:
        constraints_str = ""
        if override.constraints:
            parts = []
            for k, v in override.constraints.items():
                parts.append(f"{k}={v}")
            constraints_str = ", ".join(parts)

        table.add_row(
            override.path,
            repr(override.default_value),
            override.value_type,
            constraints_str or "—",
            override.description,
        )

    console.print(table)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    process_id: str = typer.Argument(
        ...,
        help="Process ID to apply the override to.",
    ),
    path: str = typer.Argument(
        ...,
        help="Dot-notation path (e.g., nodes.triage.config.confidence_threshold).",
    ),
    value: str = typer.Argument(
        ...,
        help="New value to set.",
    ),
) -> None:
    """Apply a single configuration override and save to storage."""
    import asyncio
    from roots.packaging.config import ConfigError, apply_override
    from roots.packaging.extractor import extract_config_overrides

    storage = ctx.obj["storage"]
    roots_instance = create_roots_from_options(storage)

    process = asyncio.run(roots_instance.storage.get_process(process_id))
    if process is None:
        typer.echo(
            typer.style(f"Process '{process_id}' not found", fg=typer.colors.RED)
        )
        raise typer.Exit(code=1)

    overrides = extract_config_overrides(process)

    # Try to parse the value as JSON first (for numbers, bools), fall back to string
    parsed_value: Any = value
    try:
        parsed_value = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        updated = apply_override(process, path, parsed_value, config_overrides=overrides)
    except ConfigError as exc:
        typer.echo(typer.style(f"Error: {exc}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    # Save back to storage (delete + re-save since storage doesn't support upsert)
    async def _save() -> None:
        await roots_instance.storage.delete_process(process_id)
        await roots_instance.storage.save_process(updated)

    asyncio.run(_save())

    console = Console()
    console.print(f"[green]Set[/green] {path} = {parsed_value!r} on process '{process_id}'")


@config_app.command("apply")
def config_apply(
    ctx: typer.Context,
    process_id: str = typer.Argument(
        ...,
        help="Process ID to apply overrides to.",
    ),
    overrides_file: str = typer.Argument(
        ...,
        help="Path to a YAML overrides file.",
    ),
) -> None:
    """Apply configuration overrides from a YAML file."""
    import asyncio
    from pathlib import Path as _Path
    from roots.packaging.config import ConfigError, apply_overrides_from_file
    from roots.packaging.extractor import extract_config_overrides

    storage = ctx.obj["storage"]
    roots_instance = create_roots_from_options(storage)

    process = asyncio.run(roots_instance.storage.get_process(process_id))
    if process is None:
        typer.echo(
            typer.style(f"Process '{process_id}' not found", fg=typer.colors.RED)
        )
        raise typer.Exit(code=1)

    overrides_path = _Path(overrides_file)
    if not overrides_path.is_file():
        typer.echo(
            typer.style(f"File not found: {overrides_file}", fg=typer.colors.RED)
        )
        raise typer.Exit(code=1)

    config_overrides = extract_config_overrides(process)

    try:
        updated = apply_overrides_from_file(
            process, overrides_path, config_overrides=config_overrides
        )
    except ConfigError as exc:
        typer.echo(typer.style(f"Error: {exc}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    # Save back to storage
    async def _save() -> None:
        await roots_instance.storage.delete_process(process_id)
        await roots_instance.storage.save_process(updated)

    asyncio.run(_save())

    console = Console()
    console.print(
        f"[green]Applied overrides[/green] from '{overrides_file}' "
        f"to process '{process_id}'"
    )


packages_app = typer.Typer(help="Manage installed Root packages.")
app.add_typer(packages_app, name="packages")


@packages_app.command("list")
def packages_list(
    ctx: typer.Context,
) -> None:
    """Show all installed packages with wiring status."""
    import asyncio

    storage = ctx.obj["storage"]
    roots_instance = create_roots_from_options(storage)

    packages = asyncio.run(roots_instance.list_installed_packages())

    if not packages:
        typer.echo("No packages installed.")
        return

    console = Console()
    console.print("\n[bold]Installed Packages:[/bold]")
    for pkg in packages:
        wiring = f"{pkg.agents_wired}/{pkg.agents_total} agents wired"
        ready_mark = "  [green]Ready[/green]" if pkg.ready else ""
        # Format installed_at to date only if it's an ISO timestamp
        installed_date = pkg.installed_at[:10] if len(pkg.installed_at) >= 10 else pkg.installed_at
        console.print(
            f"  {pkg.package_id:<25} v{pkg.package_version:<8} "
            f"(installed {installed_date})  {wiring}{ready_mark}"
        )


@packages_app.command("status")
def packages_status(
    ctx: typer.Context,
    package_id: str = typer.Argument(
        ...,
        help="Package ID to show status for.",
    ),
) -> None:
    """Show detailed status for an installed package."""
    import asyncio

    storage = ctx.obj["storage"]
    roots_instance = create_roots_from_options(storage)

    status_info = asyncio.run(roots_instance.get_package_status(package_id))

    if status_info is None:
        typer.echo(
            typer.style(f"Package '{package_id}' not found", fg=typer.colors.RED)
        )
        raise typer.Exit(code=1)

    console = Console()
    console.print(f"\n[bold]Package:[/bold] {status_info.package_id}")
    console.print(f"[bold]Version:[/bold] {status_info.package_version}")
    console.print(f"[bold]Process:[/bold] {status_info.process_id}")
    console.print(f"[bold]Name:[/bold] {status_info.process_name}")
    console.print(f"[bold]Description:[/bold] {status_info.process_description}")
    console.print(f"[bold]Installed:[/bold] {status_info.installed_at}")
    console.print(f"[bold]Source:[/bold] {status_info.installed_from}")
    console.print(f"[bold]Active Runs:[/bold] {status_info.active_runs}")

    # Agent wiring status
    report = status_info.contract_report
    console.print("\n[bold]Agent Status:[/bold]")
    for match in report.satisfied:
        agent_type = match.registration.get("agent_type", "local")
        console.print(
            f"  [green]✓[/green] {match.contract.name:<25} — Satisfied ({agent_type})"
        )
    for contract in report.missing:
        desc = contract.description or "Required agent"
        console.print(
            f"  [red]✗[/red] {contract.name:<25} — MISSING — {desc}"
        )
    for contract in report.optional_missing:
        console.print(
            f"  [yellow]~[/yellow] {contract.name:<25} — Optional, not registered"
        )

    # Applied overrides
    if status_info.overrides:
        console.print("\n[bold]Configurable Parameters:[/bold]")
        for override in status_info.overrides:
            console.print(
                f"  {override['path']} = {override['value']!r} ({override['type']})"
            )


@packages_app.command("uninstall")
def packages_uninstall(
    ctx: typer.Context,
    package_id: str = typer.Argument(
        ...,
        help="Package ID to uninstall.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force uninstall even if there are active runs.",
    ),
) -> None:
    """Remove an installed package."""
    import asyncio

    storage = ctx.obj["storage"]
    roots_instance = create_roots_from_options(storage)

    try:
        removed = asyncio.run(
            roots_instance.uninstall_package(package_id, force=force)
        )
    except ValueError as exc:
        typer.echo(typer.style(f"Error: {exc}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    if not removed:
        typer.echo(
            typer.style(f"Package '{package_id}' not found", fg=typer.colors.RED)
        )
        raise typer.Exit(code=1)

    console = Console()
    console.print(f"[green]Uninstalled[/green] package '{package_id}'")


if __name__ == "__main__":
    app()
