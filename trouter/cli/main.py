"""Main Typer CLI for trouter — agent pool management and monitoring."""

import json
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="trouter",
    help="Agent Command Center — monitor, dispatch, and manage AI coding agents.",
    no_args_is_help=True,
)

console = Console()

HEARTBEAT_DIR = Path.home() / ".claude" / "terminal_health" / "heartbeats"
WATCHDOG_STATUS_FILE = Path.home() / ".claude" / "terminal_health" / "status" / "watchdog.status"
WATCHDOG_PID_FILE = Path.home() / ".claude" / "terminal_health" / "status" / "watchdog.pid"


def _resolve_cli_bin() -> Path:
    """Resolve the cursor-agent binary path.

    Search order:
    1. TROUTER_CLI_BIN env var (explicit override)
    2. Sibling to config: <config_dir>/../bin/cursor-agent
    3. Package fallback: trouter/shell/cursor_agent.sh
    """
    env_bin = os.environ.get("TROUTER_CLI_BIN")
    if env_bin:
        p = Path(env_bin).expanduser()
        if p.exists():
            return p

    from trouter.core.config import find_config_path
    config_path = find_config_path()
    cli_bin = config_path.parent.parent / "bin" / "cursor-agent"
    if cli_bin.exists():
        return cli_bin

    return Path(__file__).resolve().parent.parent / "shell" / "cursor_agent.sh"


# ---------------------------------------------------------------------------
# trouter dashboard
# ---------------------------------------------------------------------------


@app.command()
def dashboard() -> None:
    """Launch the interactive TUI dashboard."""
    from trouter.tui.app import run_dashboard

    run_dashboard()


# ---------------------------------------------------------------------------
# trouter status
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Quick health summary of all active sessions."""
    from trouter.health.heartbeat import HeartbeatManager

    mgr = HeartbeatManager()
    summary = mgr.get_health_summary()

    console.print()
    console.print("[bold]Session Health Summary[/bold]")
    console.print()

    if summary["total_sessions"] == 0:
        console.print("[dim]No active sessions found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Session", style="white")
    table.add_column("Health", justify="center")
    table.add_column("Age (s)", justify="right")
    table.add_column("Status", style="dim")
    table.add_column("PID", justify="right", style="dim")

    health_styles = {
        "healthy": "[green]healthy[/green]",
        "warning": "[yellow]warning[/yellow]",
        "frozen": "[red]frozen[/red]",
    }

    for session in summary["sessions"]:
        health_label = health_styles.get(session["health"], session["health"])
        table.add_row(
            session["session_id"][:30],
            health_label,
            str(session["age_seconds"]),
            session["status"],
            str(session["pid"]),
        )

    console.print(table)
    console.print()
    console.print(
        f"[bold green]{summary['healthy']}[/] healthy  "
        f"[bold yellow]{summary['warning']}[/] warning  "
        f"[bold red]{summary['frozen']}[/] frozen  "
        f"[dim]({summary['total_sessions']} total)[/]"
    )


# ---------------------------------------------------------------------------
# trouter dispatch
# ---------------------------------------------------------------------------


@app.command()
def dispatch(
    task: str = typer.Argument(..., help="Task description to send to the agent pool."),
    agent_type: str = typer.Option("auto", "--type", "-t", help="Agent type: auto, composer, codex, claude."),
) -> None:
    """Send a task to the agent pool for execution."""
    from trouter.core.models import select_swarm_tier
    from trouter.core.pool import StandbyPool, StandbyConfig
    from trouter.core.config import find_config_path, TrouterConfig

    tier_name, model = select_swarm_tier(task)

    console.print()
    console.print("[bold]Dispatch Info[/bold]")
    console.print(f"  Task:       {task}")
    console.print(f"  Tier:       [cyan]{tier_name}[/cyan]")
    console.print(f"  Model:      [cyan]{model}[/cyan]")
    console.print(f"  Agent type: {agent_type}")
    console.print()

    config_path = find_config_path()
    cfg = TrouterConfig.from_file(config_path)

    console.print(f"  Dispatch mode: [bold]{cfg.dispatch_mode}[/bold]")
    console.print(f"  Config:        {config_path}")
    console.print()

    cli_bin = _resolve_cli_bin()
    pool = StandbyPool(StandbyConfig(), cli_bin=cli_bin, config_path=config_path)
    slot_id = pool.dispatch_auto(task, prefer_type=agent_type, model_id=model)

    if slot_id:
        console.print(f"[green]Dispatched to slot [bold]{slot_id}[/bold][/green]")
    else:
        console.print("[yellow]No available agent slots. Task queued for next free slot.[/yellow]")


# ---------------------------------------------------------------------------
# trouter pool
# ---------------------------------------------------------------------------


@app.command()
def pool() -> None:
    """Show agent pool slots and their current states."""
    from trouter.core.pool import StandbyPool, StandbyConfig
    from trouter.core.config import find_config_path

    cli_bin = _resolve_cli_bin()
    p = StandbyPool(StandbyConfig(), cli_bin=cli_bin, config_path=find_config_path())
    slots = p.summary()

    console.print()
    console.print("[bold]Agent Pool[/bold]")
    console.print()

    if not slots:
        console.print("[dim]No pool slots configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Slot ID", style="white")
    table.add_column("Type", style="dim")
    table.add_column("State", justify="center")
    table.add_column("Model", style="dim")
    table.add_column("PID", justify="right", style="dim")
    table.add_column("Tasks Done", justify="right")
    table.add_column("Current Task")

    state_styles = {
        "STANDBY": "[green]STANDBY[/green]",
        "BUSY": "[yellow]BUSY[/yellow]",
        "ERROR": "[red]ERROR[/red]",
        "OFFLINE": "[dim]OFFLINE[/dim]",
    }

    for slot in slots:
        state_label = state_styles.get(slot["state"], slot["state"])
        pid_str = str(slot["pid"]) if slot["pid"] else "-"
        task_str = (slot["current_task"] or "-")[:50]
        table.add_row(
            slot["id"],
            slot["type"],
            state_label,
            slot.get("model") or "-",
            pid_str,
            str(slot["tasks_completed"]),
            task_str,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# trouter health
# ---------------------------------------------------------------------------


@app.command()
def health() -> None:
    """Show watchdog daemon status and overall session health."""
    from trouter.health.daemon import WatchdogDaemon

    console.print()
    console.print("[bold]Watchdog Status[/bold]")
    console.print()

    # Check if daemon is running
    is_running = WatchdogDaemon.is_running()

    if WATCHDOG_STATUS_FILE.exists():
        try:
            data = json.loads(WATCHDOG_STATUS_FILE.read_text())
            running_label = "[green]running[/green]" if data.get("running") else "[red]stopped[/red]"
            console.print(f"  Daemon:     {running_label}")
            console.print(f"  PID:        {data.get('pid', '-')}")
            console.print(f"  Uptime:     {data.get('uptime_seconds', 0):.0f}s")
            console.print(f"  Checks:     {data.get('checks_performed', 0)}")
            console.print(f"  Actions:    {data.get('actions_taken', 0)}")
            console.print(f"  Monitoring: {data.get('sessions_monitored', 0)} sessions")
            if data.get("last_check"):
                console.print(f"  Last check: {data['last_check']}")
        except (json.JSONDecodeError, KeyError):
            console.print("[yellow]Watchdog status file is corrupt.[/yellow]")
    elif is_running:
        console.print("[green]Daemon is running[/green] (no status file yet)")
    else:
        console.print("[dim]Watchdog daemon is not running.[/dim]")
        console.print("[dim]Start with: python -m trouter.health.daemon[/dim]")

    # Also show session health summary
    console.print()
    status()


# ---------------------------------------------------------------------------
# trouter config
# ---------------------------------------------------------------------------


@app.command()
def config(
    key: Optional[str] = typer.Argument(None, help="Config key to read or set (e.g., dispatch_mode)."),
    value: Optional[str] = typer.Option(None, "--set", "-s", help="Value to set for the given key."),
    path: Optional[str] = typer.Option(None, "--path", "-p", help="Path to cursor_config.json."),
) -> None:
    """Read or write trouter / cursor_config.json settings."""
    from trouter.core.config import find_config_path, TrouterConfig

    config_path = Path(path).expanduser() if path else find_config_path()

    console.print()
    console.print(f"[bold]Config[/bold]  [dim]{config_path}[/dim]")
    console.print()

    cfg = TrouterConfig.from_file(config_path)

    if key is None:
        # Show all config
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Key", style="white")
        table.add_column("Value")

        fields = {
            "dispatch_mode": cfg.dispatch_mode,
            "enabled": str(cfg.enabled),
            "model_override": cfg.model_override or "(none)",
            "default_model": cfg.default_model,
            "allowed_models": ", ".join(cfg.allowed_models) if cfg.allowed_models else "(none)",
            "composer_only": str(cfg.composer_only),
            "composer_augmented": str(cfg.composer_augmented),
            "credit_target_monthly": str(cfg.credit_target_monthly),
            "locked": str(cfg.locked),
        }

        for k, v in fields.items():
            table.add_row(k, v)

        console.print(table)
        return

    # Read or set a specific key
    if not hasattr(cfg, key):
        console.print(f"[red]Unknown config key: {key}[/red]")
        raise typer.Exit(code=1)

    if value is None:
        # Read
        console.print(f"  {key} = [cyan]{getattr(cfg, key)}[/cyan]")
        return

    # Set
    current = getattr(cfg, key)
    if isinstance(current, bool):
        parsed_value = value.lower() in ("true", "1", "yes")
    elif isinstance(current, int):
        parsed_value = int(value)
    elif isinstance(current, list):
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError:
            console.print(f"[red]Value for list field '{key}' must be valid JSON (e.g. '[\"a\",\"b\"]').[/red]")
            raise typer.Exit(code=1)
        if not isinstance(parsed_value, list):
            console.print(f"[red]Value for '{key}' must be a JSON array.[/red]")
            raise typer.Exit(code=1)
    else:
        parsed_value = value

    setattr(cfg, key, parsed_value)
    cfg.to_file(config_path)
    console.print(f"  [green]Set {key} = {parsed_value}[/green]")
