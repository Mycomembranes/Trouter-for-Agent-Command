"""StandbyPool slot summary panel."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class PoolPanel(Vertical):
    """Shows standby pool slot counts by state."""

    DEFAULT_CSS = """
    PoolPanel {
        height: auto;
        min-height: 5;
        border: solid $primary;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    PoolPanel .pp-title {
        text-style: bold;
    }
    PoolPanel .pp-row {
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._slots: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        yield Static("Pool", classes="pp-title")
        yield Static(self._slots_line(), classes="pp-row", id="pp-slots")

    def _slots_line(self) -> str:
        if not self._slots:
            return "[dim]no slots[/]"
        parts = []
        colors = {
            "STANDBY": "green",
            "BUSY": "blue",
            "ERROR": "red",
            "OFFLINE": "dim",
        }
        for state, count in sorted(self._slots.items()):
            c = colors.get(state, "white")
            parts.append(f"[{c}]{state}:{count}[/]")
        return "  ".join(parts)

    def update_slots(self, slots: dict[str, int]) -> None:
        self._slots = slots
        try:
            self.query_one("#pp-slots", Static).update(self._slots_line())
        except Exception:
            pass
