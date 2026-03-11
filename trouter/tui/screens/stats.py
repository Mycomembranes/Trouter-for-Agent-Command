"""Token/credit deep stats screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from trouter.hooks.session_stats import load_session_usage, summarize_session_usage


class StatsScreen(Screen):
    """Per-session token usage, tool counts, and credit burn."""

    DEFAULT_CSS = """
    StatsScreen {
        layout: vertical;
    }
    StatsScreen #stats-summary {
        height: auto;
        padding: 1;
        border: solid $primary;
        margin: 1;
    }
    StatsScreen .stats-title {
        text-style: bold;
    }
    StatsScreen DataTable {
        height: 1fr;
        margin: 0 1 1 1;
    }
    """

    BINDINGS = [
        ("d", "switch_dashboard", "Dashboard"),
        ("escape", "switch_dashboard", "Back"),
        ("r", "refresh_stats", "Refresh"),
    ]

    SESSIONS_DIR = Path.home() / ".claude" / "hooks_data" / "sessions"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="stats-summary"):
            yield Static("Session Stats", classes="stats-title")
            yield Static("Loading...", id="stats-totals")
        yield DataTable(id="stats-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#stats-table", DataTable)
        table.add_columns("Session", "Tokens In", "Tokens Out", "Tools", "Duration")
        self.action_refresh_stats()

    def action_switch_dashboard(self) -> None:
        self.app.switch_screen("dashboard")

    def action_refresh_stats(self) -> None:
        """Load session stats from hooks_data."""
        table = self.query_one("#stats-table", DataTable)
        table.clear()

        records = load_session_usage(self.SESSIONS_DIR, limit=50)
        totals = summarize_session_usage(records)

        for record in records:
            dur = record.duration_seconds
            dur_str = f"{dur // 60}m{dur % 60}s" if dur else "—"
            table.add_row(
                record.session_id[:16],
                f"{record.tokens_in:,}",
                f"{record.tokens_out:,}",
                str(record.tool_calls),
                dur_str,
            )

        totals_text = (
            f"Sessions: {totals['sessions']}  |  "
            f"Tokens in: {totals['tokens_in']:,}  out: {totals['tokens_out']:,}  |  "
            f"Tool calls: {totals['tool_calls']:,}"
        )
        try:
            self.query_one("#stats-totals", Static).update(totals_text)
        except Exception:
            pass
