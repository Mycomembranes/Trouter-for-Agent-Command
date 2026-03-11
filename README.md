# Trouter

Trouter is a terminal-first agent command center for routing work, tracking session health, and monitoring multi-agent activity from one place.



## What works

- `trouter dashboard` launches a Textual TUI with:
  - live agent cards sourced from heartbeat files
  - a detail screen per session
  - watchdog, pool, and usage side panels
  - compact / kill actions for a selected session
- `trouter dispatch "task"` classifies a task into a swarm tier and hands it to the standby pool with the selected model preserved
- `trouter status`, `trouter health`, `trouter pool`, and `trouter config` provide CLI inspection and maintenance
- The watchdog daemon reads the same heartbeat format as the dashboard and writes a shared status file consumed by both the CLI and TUI
- Hook usage data from `trouter/hooks/usage_tracker.py` is normalized into the stats screen and sidebar instead of being silently ignored

## Install

```bash
cd /Users/mukshudahamed/claude_rotifer/trouter
pip install -e .
pip install -e ".[dev]"
```

## Quick Start

```bash
# Show available commands
python -m trouter --help

# Open the dashboard
trouter dashboard

# Inspect current sessions
trouter status

# Dispatch a task through the standby pool
trouter dispatch "Refactor the auth module"

# Inspect pool state and config
trouter pool
trouter config
```

## Configuration

Trouter looks for config in this order:

1. `TROUTER_CONFIG`
2. [`etc/cursor_config.json`](/Users/mukshudahamed/claude_rotifer/trouter/etc/cursor_config.json)
3. `~/claude_rotifer/CLI/etc/cursor_config.json`

Useful environment variables:

- `TROUTER_CONFIG`: explicit config path
- `TROUTER_CLI_BIN`: override the agent binary used by the pool
- `WATCHDOG_HEALTH_DIR`: override the heartbeat/watchdog root
- `CURSOR_SKIP_CONTEXT=1`: disable prompt-context enrichment

## Runtime Data

Trouter expects these on-disk locations:

- Heartbeats: `~/.claude/terminal_health/heartbeats/*.heartbeat`
- Watchdog status: `~/.claude/terminal_health/status/watchdog.status`
- Hook usage sessions: `~/.claude/hooks_data/sessions/*.json`

Legacy `*.json` heartbeat files are still read for compatibility, but `.heartbeat` is the canonical format.

## Platform Notes

- The dashboard and CLI are cross-platform Python.
- `Open Terminal` and iTerm alert flows are macOS/iTerm-specific.
- Native dispatch expects a Cursor agent binary to be available; local fallback expects a `claude` CLI binary.

## Development

```bash
pytest -q
python -m compileall trouter tests
```

The test suite covers config handling, dispatch utilities, standby-pool behavior, watchdog/remediation logic, heartbeat parsing, TUI widget logic, and the file-format normalization paths used by the dashboard.
