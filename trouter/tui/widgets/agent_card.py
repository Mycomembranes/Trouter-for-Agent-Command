"""Agent card widget — shows name, state, context %, and task preview."""

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from trouter.tui.widgets.health_bar import HealthBar


class AgentCard(Static):
    """A card representing a single agent in the grid."""

    DEFAULT_CSS = """
    AgentCard {
        border: solid $primary;
        padding: 0 1;
        height: auto;
        min-height: 5;
        margin: 0 1 1 0;
    }
    AgentCard.healthy {
        border: solid $success;
    }
    AgentCard.warning {
        border: solid $warning;
    }
    AgentCard.frozen {
        border: solid $error;
    }
    AgentCard.idle {
        border: solid #666666;
        opacity: 0.6;
    }
    AgentCard.hidden {
        display: none;
    }
    AgentCard:focus {
        border: double $accent;
    }
    AgentCard .card-header {
        text-style: bold;
    }
    AgentCard .card-state {
        color: $text-muted;
    }
    AgentCard .card-task {
        color: $text-muted;
        max-height: 2;
    }
    """

    session_id: reactive[str] = reactive("")
    state: reactive[str] = reactive("unknown")
    context_pct: reactive[int] = reactive(100)
    task_preview: reactive[str] = reactive("")
    visible_card: reactive[bool] = reactive(True)

    class Selected(Message):
        """Emitted when a card is clicked/selected."""
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(
        self,
        session_id: str,
        display_name: str = "",
        state: str = "unknown",
        context_pct: int = 100,
        task_preview: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.session_id = session_id
        self._display_name = display_name or session_id
        self.state = state
        self.context_pct = context_pct
        self.task_preview = task_preview
        self.can_focus = True

    def compose(self) -> ComposeResult:
        yield Static(self._display_name, classes="card-header")
        yield Static(self._state_badge(), classes="card-state")
        yield HealthBar(self.context_pct)
        yield Static(self.task_preview[:60] if self.task_preview else "", classes="card-task")

    def _state_badge(self) -> str:
        badges = {
            "healthy": "[green]ACTIVE[/]",
            "busy": "[blue]BUSY[/]",
            "idle": "[dim]IDLE[/]",
            "warning": "[yellow]WARNING[/]",
            "frozen": "[red]FROZEN[/]",
            "error": "[red]ERROR[/]",
            "unknown": "[dim]---[/]",
        }
        return badges.get(self.state, f"[dim]{self.state}[/]")

    def watch_state(self, value: str) -> None:
        """Update CSS classes when state changes."""
        for cls in ("healthy", "warning", "frozen", "idle"):
            self.remove_class(cls)
        if value in ("healthy", "busy"):
            self.add_class("healthy")
        elif value == "warning":
            self.add_class("warning")
        elif value in ("frozen", "error"):
            self.add_class("frozen")
        elif value == "idle":
            self.add_class("idle")

    def watch_visible_card(self, value: bool) -> None:
        if value:
            self.remove_class("hidden")
        else:
            self.add_class("hidden")

    def on_click(self) -> None:
        self.post_message(self.Selected(self.session_id))

    def update_data(
        self,
        state: str | None = None,
        context_pct: int | None = None,
        task_preview: str | None = None,
    ) -> None:
        """Update card data reactively."""
        if state is not None:
            self.state = state
            try:
                self.query_one(".card-state", Static).update(self._state_badge())
            except Exception:
                pass
        if context_pct is not None:
            self.context_pct = context_pct
            try:
                self.query_one(HealthBar).update_percentage(context_pct)
            except Exception:
                pass
        if task_preview is not None:
            self.task_preview = task_preview
            try:
                self.query_one(".card-task", Static).update(task_preview[:60])
            except Exception:
                pass
