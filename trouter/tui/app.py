"""Main Textual TUI application — Agent Command Center."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from textual.app import App
from textual.binding import Binding

from trouter.hooks.session_stats import load_session_usage, summarize_session_usage
from trouter.tui.screens.dashboard import DashboardScreen
from trouter.tui.screens.agent_detail import AgentDetailScreen
from trouter.tui.screens.stats import StatsScreen
from trouter.tui.widgets.command_palette import CommandPalette

logger = logging.getLogger(__name__)


def _read_json(path: Path) -> dict | None:
    """Load a JSON file, returning None on parse or I/O failure."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _heartbeat_snapshots(heartbeat_dir: Path) -> list[dict]:
    """Load the most recent heartbeat payload for each session."""
    if not heartbeat_dir.exists():
        return []

    latest_by_session: dict[str, tuple[tuple[float, int], dict]] = {}
    for pattern in ("*.heartbeat", "*.json"):
        for path in heartbeat_dir.glob(pattern):
            data = _read_json(path)
            if not data:
                continue
            session_id = str(data.get("session_id") or path.stem)
            try:
                modified = path.stat().st_mtime
            except OSError:
                modified = 0.0
            rank = (modified, 1 if path.suffix == ".heartbeat" else 0)
            previous = latest_by_session.get(session_id)
            if previous is None or rank >= previous[0]:
                latest_by_session[session_id] = (rank, data)

    return [entry[1] for entry in latest_by_session.values()]


def _heartbeat_for_session(heartbeat_dir: Path, session_id: str) -> dict | None:
    """Find the latest heartbeat payload for a specific session."""
    for heartbeat in _heartbeat_snapshots(heartbeat_dir):
        if heartbeat.get("session_id") == session_id:
            return heartbeat
    return None


def _agent_state(data: dict, now: float | None = None) -> str:
    """Map heartbeat age and explicit state into a dashboard card state."""
    now = now if now is not None else time.time()
    age = now - float(data.get("unix_time", 0) or 0)
    if age < 30:
        state = "healthy"
    elif age < 60:
        state = "warning"
    elif age < 90:
        state = "idle"
    else:
        state = "frozen"

    explicit = str(data.get("state") or "").lower()
    explicit_map = {
        "busy": "busy",
        "plan_mode": "busy",
        "compact_mode": "busy",
        "error": "error",
        "frozen": "frozen",
        "idle": "idle",
    }
    return explicit_map.get(explicit, state)


def _pool_state(data: dict, now: float | None = None) -> str:
    """Map heartbeat data into standby-pool style state counts."""
    explicit = str(data.get("state") or "").lower()
    if explicit in {"busy", "plan_mode", "compact_mode"}:
        return "BUSY"
    if explicit == "error":
        return "ERROR"

    now = now if now is not None else time.time()
    age = now - float(data.get("unix_time", 0) or 0)
    if age < 90:
        return "STANDBY"
    return "OFFLINE"


def _task_preview(data: dict) -> str:
    """Extract the best available task preview from heartbeat payloads."""
    return str(data.get("task_preview") or data.get("status") or "")


def _format_uptime(seconds: float) -> str:
    """Render uptime seconds into a compact human-readable string."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _watchdog_status(data: dict) -> dict[str, object]:
    """Normalize watchdog status payloads across old and current schemas."""
    return {
        "running": bool(data.get("running", False)),
        "uptime": _format_uptime(float(data.get("uptime_seconds") or data.get("uptime") or 0)),
        "checks": int(data.get("checks_performed") or data.get("checks") or 0),
        "actions": int(data.get("actions_taken") or data.get("actions") or 0),
        "alerts": list(data.get("recent_alerts") or []),
    }


class TrouterApp(App):
    """Agent Command Center — monitor, dispatch, and manage AI coding agents."""

    TITLE = "Trouter — Agent Command Center"
    CSS = """
    Screen {
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Commands", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    SCREENS = {
        "dashboard": DashboardScreen,
        "agent_detail": AgentDetailScreen,
        "stats": StatsScreen,
    }

    HEARTBEAT_DIR = Path.home() / ".claude" / "terminal_health" / "heartbeats"
    WATCHDOG_STATUS_FILE = Path.home() / ".claude" / "terminal_health" / "status" / "watchdog.status"
    SESSIONS_DIR = Path.home() / ".claude" / "hooks_data" / "sessions"
    POLL_INTERVAL = 2.0

    def on_mount(self) -> None:
        """Start on dashboard and begin polling heartbeats."""
        self.push_screen("dashboard")
        self.set_interval(self.POLL_INTERVAL, self._poll_heartbeats)

    # ── Heartbeat polling ──────────────────────────────────────────────

    def _poll_heartbeats(self) -> None:
        """Read heartbeat files and update the agent grid."""
        screen = self.screen

        # If viewing agent detail, poll that agent's heartbeat directly
        if isinstance(screen, AgentDetailScreen):
            self._poll_detail_screen(screen)
            return

        if not isinstance(screen, DashboardScreen):
            return

        grid = screen.grid
        seen: set[str] = set()

        for data in _heartbeat_snapshots(self.HEARTBEAT_DIR):
            sid = str(data.get("session_id") or "")
            if not sid:
                continue
            seen.add(sid)
            grid.add_agent(
                session_id=sid,
                display_name=sid[:20],
                state=_agent_state(data),
                context_pct=int(data.get("context_pct", 100) or 100),
                task_preview=_task_preview(data),
            )

        # Remove stale cards
        for card_id in list(grid.card_ids):
            if card_id not in seen:
                grid.remove_agent(card_id)

        # Update sidebar panels
        self._update_sidebar(screen, len(seen))

    def _poll_detail_screen(self, screen: AgentDetailScreen) -> None:
        """Read heartbeat for the agent shown on the detail screen."""
        sid = screen._session_id
        data = _heartbeat_for_session(self.HEARTBEAT_DIR, sid)
        if data is None:
            return
        screen.update_detail(
            state=_agent_state(data),
            context_pct=int(data.get("context_pct", 100) or 100),
            pid=int(data.get("pid", 0) or 0),
            task=_task_preview(data),
        )

    def _update_sidebar(self, screen: DashboardScreen, session_count: int) -> None:
        """Update watchdog, pool, and stats panels."""
        try:
            if self.WATCHDOG_STATUS_FILE.exists():
                wd = _watchdog_status(_read_json(self.WATCHDOG_STATUS_FILE) or {})
                screen.watchdog_panel.update_status(
                    running=wd.get("running", False),
                    uptime=wd.get("uptime", "—"),
                    checks=wd.get("checks", 0),
                    actions=wd.get("actions", 0),
                    alerts=wd.get("alerts", []),
                )
            else:
                screen.watchdog_panel.update_status(running=False)
        except Exception as e:
            logger.debug(f"Watchdog panel update failed: {e}")

        try:
            usage = summarize_session_usage(load_session_usage(self.SESSIONS_DIR))
            screen.stats_panel.update_stats(
                tokens_in=usage["tokens_in"],
                tokens_out=usage["tokens_out"],
                tool_calls=usage["tool_calls"],
                sessions=session_count,
            )
        except Exception as e:
            logger.debug(f"Stats panel update failed: {e}")

        try:
            state_counts: dict[str, int] = {}
            for data in _heartbeat_snapshots(self.HEARTBEAT_DIR):
                state = _pool_state(data)
                state_counts[state] = state_counts.get(state, 0) + 1
            screen.pool_panel.update_slots(state_counts)
        except Exception as e:
            logger.debug(f"Pool panel update failed: {e}")

    # ── Agent actions ──────────────────────────────────────────────────

    def compact_agent(self, session_id: str) -> None:
        """Send /compact to an agent session."""
        try:
            from trouter.health.remediation import RemediationHandler

            handler = RemediationHandler()
            handler.send_compact(session_id)
        except Exception as e:
            self._log_dispatch(f"[red]compact failed: {e}[/]")

    def kill_agent(self, session_id: str) -> None:
        """Kill an agent session."""
        try:
            from trouter.health.remediation import RemediationHandler

            handler = RemediationHandler()
            handler.kill_session(session_id)
        except Exception as e:
            self._log_dispatch(f"[red]kill failed: {e}[/]")

    def open_terminal(self, session_id: str) -> None:
        """Open the agent's terminal window (macOS iTerm/Terminal)."""
        try:
            # Sanitize session_id for AppleScript interpolation
            safe_id = session_id.replace("\\", "\\\\").replace('"', '\\"')
            script = f'''
tell application "iTerm2"
    activate
    repeat with aWindow in windows
        tell aWindow
            repeat with aTab in tabs
                tell aTab
                    repeat with aSession in sessions
                        if name of aSession contains "{safe_id}" then
                            select aTab
                            return
                        end if
                    end repeat
                end tell
            end repeat
        end tell
    end repeat
end tell
'''
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, timeout=5
            )
            if result.returncode != 0:
                logger.debug(f"open_terminal osascript failed: {result.stderr}")
        except Exception as e:
            logger.debug(f"open_terminal failed: {e}")

    def refresh_agents(self) -> None:
        """Force an immediate heartbeat poll."""
        self._poll_heartbeats()

    def handle_palette_command(self, command: str) -> None:
        """Execute a command selected from the palette."""
        if command == "dashboard":
            self.switch_screen("dashboard")
        elif command == "stats":
            self.switch_screen("stats")
        elif command == "refresh":
            self.refresh_agents()
        elif command == "quit":
            self.exit()
        elif command == "compact":
            screen = self.screen
            if isinstance(screen, DashboardScreen):
                focused = self.focused
                from trouter.tui.widgets.agent_card import AgentCard
                if isinstance(focused, AgentCard):
                    self.compact_agent(focused.session_id)
        elif command == "kill":
            screen = self.screen
            if isinstance(screen, DashboardScreen):
                focused = self.focused
                from trouter.tui.widgets.agent_card import AgentCard
                if isinstance(focused, AgentCard):
                    self.kill_agent(focused.session_id)
        elif command == "hide-idle":
            self._hide_by_state("idle")
        elif command == "show-all":
            self._show_all()
        else:
            self._log_dispatch(f"[dim]unknown command: {command}[/]")

    def _hide_by_state(self, state: str) -> None:
        screen = self.screen
        if isinstance(screen, DashboardScreen):
            for card_id in screen.grid.card_ids:
                card = screen.grid.get_card(card_id)
                if card and card.state == state:
                    card.visible_card = False

    def _show_all(self) -> None:
        screen = self.screen
        if isinstance(screen, DashboardScreen):
            for card_id in screen.grid.card_ids:
                card = screen.grid.get_card(card_id)
                if card:
                    card.visible_card = True

    def _log_dispatch(self, text: str) -> None:
        screen = self.screen
        if isinstance(screen, DashboardScreen):
            try:
                screen.dispatch_log.append(text)
            except Exception:
                pass

    def action_command_palette(self) -> None:
        self.push_screen(CommandPalette(), callback=self._palette_callback)

    def _palette_callback(self, result: str | None) -> None:
        if result:
            self.handle_palette_command(result)


def run_dashboard():
    """Entry point for `trouter-dashboard` and `trouter dashboard`."""
    app = TrouterApp()
    app.run()
