"""Single agent detail/focus screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, RichLog, Static

from trouter.tui.widgets.health_bar import HealthBar


class AgentDetailScreen(Screen):
    """Full detail view for a single agent — heartbeat data, output tail, actions."""

    DEFAULT_CSS = """
    AgentDetailScreen {
        layout: vertical;
    }
    AgentDetailScreen #detail-header {
        height: auto;
        padding: 1;
        border: solid $primary;
        margin: 1;
    }
    AgentDetailScreen .detail-title {
        text-style: bold;
    }
    AgentDetailScreen .detail-meta {
        color: $text-muted;
    }
    AgentDetailScreen #detail-output {
        height: 1fr;
        margin: 0 1;
        border: solid $primary;
    }
    AgentDetailScreen #detail-actions {
        height: auto;
        layout: horizontal;
        padding: 1;
        align: center middle;
    }
    AgentDetailScreen #detail-actions Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("c", "compact", "Compact"),
        ("k", "kill", "Kill"),
    ]

    def __init__(self, session_id: str = "", **kwargs):
        super().__init__(**kwargs)
        self._session_id = session_id
        self._state = "unknown"
        self._context_pct = 100
        self._pid = 0
        self._task = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="detail-header"):
            yield Static(f"Agent: {self._session_id}", classes="detail-title")
            yield Static(self._meta_text(), classes="detail-meta", id="detail-meta")
            yield HealthBar(self._context_pct, id="detail-health")
        yield RichLog(highlight=True, markup=True, wrap=True, id="detail-output")
        with Horizontal(id="detail-actions"):
            yield Button("Compact", variant="warning", id="btn-compact")
            yield Button("Kill", variant="error", id="btn-kill")
            yield Button("Open Terminal", variant="primary", id="btn-open")
            yield Button("Back", variant="default", id="btn-back")
        yield Footer()

    def _meta_text(self) -> str:
        task_preview = (self._task or "")[:60]
        return (
            f"State: {self._state}  |  Context: {self._context_pct}%  |  "
            f"PID: {self._pid}  |  Task: {task_preview}"
        )

    def update_detail(
        self,
        state: str = "unknown",
        context_pct: int = 100,
        pid: int = 0,
        task: str = "",
        output_lines: list[str] | None = None,
    ) -> None:
        self._state = state
        self._context_pct = context_pct
        self._pid = pid
        self._task = task
        try:
            self.query_one("#detail-meta", Static).update(self._meta_text())
            self.query_one("#detail-health", HealthBar).update_percentage(context_pct)
            if output_lines:
                log = self.query_one("#detail-output", RichLog)
                for line in output_lines:
                    log.write(line)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-compact":
            self.action_compact()
        elif event.button.id == "btn-kill":
            self.action_kill()
        elif event.button.id == "btn-open":
            self.app.open_terminal(self._session_id)
        elif event.button.id == "btn-back":
            self.action_go_back()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_compact(self) -> None:
        self.app.compact_agent(self._session_id)

    def action_kill(self) -> None:
        self.app.kill_agent(self._session_id)
        self.app.pop_screen()
