"""Main dashboard screen — agent grid + sidebar + dispatch log."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Header, Static

from trouter.tui.keybindings import FOOTER_HINTS
from trouter.tui.widgets.agent_card import AgentCard
from trouter.tui.widgets.agent_grid import AgentGrid
from trouter.tui.widgets.dispatch_log import DispatchLog
from trouter.tui.widgets.pool_panel import PoolPanel
from trouter.tui.widgets.stats_panel import StatsPanel
from trouter.tui.widgets.watchdog_panel import WatchdogPanel


class Sidebar(Vertical):
    """Collapsible sidebar with watchdog, pool, and stats panels."""

    DEFAULT_CSS = """
    Sidebar {
        width: 30;
        min-width: 24;
        dock: right;
        padding: 0;
    }
    Sidebar.collapsed {
        display: none;
    }
    """


class DashboardScreen(Screen):
    """Primary dashboard: grid of agent cards, sidebar, dispatch log."""

    DEFAULT_CSS = """
    DashboardScreen {
        layout: vertical;
    }
    DashboardScreen #main-area {
        height: 1fr;
    }
    DashboardScreen #grid-area {
        width: 1fr;
    }
    DashboardScreen #footer-hints {
        dock: bottom;
        height: 1;
        background: $primary-background;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("d", "noop", "Dashboard"),
        ("s", "switch_stats", "Stats"),
        ("enter", "open_detail", "Detail"),
        ("h", "toggle_card", "Hide"),
        ("c", "compact_agent", "Compact"),
        ("k", "kill_agent", "Kill"),
        ("n", "new_dispatch", "Palette"),
        ("r", "refresh", "Refresh"),
        ("b", "toggle_sidebar", "Sidebar"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-area"):
            with Vertical(id="grid-area"):
                yield AgentGrid(id="agent-grid")
                yield DispatchLog(id="dispatch-log")
            with Sidebar(id="sidebar"):
                yield WatchdogPanel(id="watchdog-panel")
                yield PoolPanel(id="pool-panel")
                yield StatsPanel(id="stats-panel")
        yield Static(FOOTER_HINTS, id="footer-hints")

    @property
    def grid(self) -> AgentGrid:
        return self.query_one("#agent-grid", AgentGrid)

    @property
    def dispatch_log(self) -> DispatchLog:
        return self.query_one("#dispatch-log", DispatchLog)

    @property
    def watchdog_panel(self) -> WatchdogPanel:
        return self.query_one("#watchdog-panel", WatchdogPanel)

    @property
    def pool_panel(self) -> PoolPanel:
        return self.query_one("#pool-panel", PoolPanel)

    @property
    def stats_panel(self) -> StatsPanel:
        return self.query_one("#stats-panel", StatsPanel)

    def on_agent_card_selected(self, message: AgentCard.Selected) -> None:
        """Open agent detail view when a card is clicked."""
        from trouter.tui.screens.agent_detail import AgentDetailScreen

        self.app.push_screen(AgentDetailScreen(session_id=message.session_id))

    def action_noop(self) -> None:
        pass

    def action_open_detail(self) -> None:
        """Open detail view for the focused agent card."""
        focused = self.app.focused
        if isinstance(focused, AgentCard):
            from trouter.tui.screens.agent_detail import AgentDetailScreen

            self.app.push_screen(AgentDetailScreen(session_id=focused.session_id))

    def action_switch_stats(self) -> None:
        self.app.switch_screen("stats")

    def action_toggle_card(self) -> None:
        """Toggle visibility of the focused card."""
        focused = self.app.focused
        if isinstance(focused, AgentCard):
            focused.visible_card = not focused.visible_card

    def action_compact_agent(self) -> None:
        focused = self.app.focused
        if isinstance(focused, AgentCard):
            self.dispatch_log.append(
                f"[yellow]COMPACT[/] → {focused.session_id}"
            )
            self.app.compact_agent(focused.session_id)

    def action_kill_agent(self) -> None:
        focused = self.app.focused
        if isinstance(focused, AgentCard):
            self.dispatch_log.append(
                f"[red]KILL[/] → {focused.session_id}"
            )
            self.app.kill_agent(focused.session_id)

    def action_new_dispatch(self) -> None:
        from trouter.tui.widgets.command_palette import CommandPalette

        self.app.push_screen(CommandPalette(), callback=self._handle_command)

    def action_refresh(self) -> None:
        self.app.refresh_agents()

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.toggle_class("collapsed")

    def _handle_command(self, result: str | None) -> None:
        if result:
            self.dispatch_log.append(f"[blue]CMD[/] {result}")
            self.app.handle_palette_command(result)
