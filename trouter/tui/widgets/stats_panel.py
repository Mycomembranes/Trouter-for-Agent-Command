"""Token usage and credit stats panel."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class StatsPanel(Vertical):
    """Shows aggregate token usage, tool calls, and credit burn."""

    DEFAULT_CSS = """
    StatsPanel {
        height: auto;
        min-height: 6;
        border: solid $primary;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    StatsPanel .sp-title {
        text-style: bold;
    }
    StatsPanel .sp-row {
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tokens_in = 0
        self._tokens_out = 0
        self._tool_calls = 0
        self._sessions = 0

    def compose(self) -> ComposeResult:
        yield Static("Stats", classes="sp-title")
        yield Static(self._stats_text(), classes="sp-row", id="sp-body")

    def _stats_text(self) -> str:
        return (
            f"Sessions: {self._sessions}\n"
            f"Tokens in: {self._tokens_in:,}  out: {self._tokens_out:,}\n"
            f"Tool calls: {self._tool_calls:,}"
        )

    def update_stats(
        self,
        tokens_in: int = 0,
        tokens_out: int = 0,
        tool_calls: int = 0,
        sessions: int = 0,
    ) -> None:
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self._tool_calls = tool_calls
        self._sessions = sessions
        try:
            self.query_one("#sp-body", Static).update(self._stats_text())
        except Exception:
            pass
