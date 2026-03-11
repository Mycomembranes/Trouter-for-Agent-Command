"""Scrolling dispatch event log widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static, RichLog


class DispatchLog(VerticalScroll):
    """Scrolling log of dispatch events, agent output, and alerts."""

    DEFAULT_CSS = """
    DispatchLog {
        height: 1fr;
        min-height: 6;
        border: solid $primary;
        padding: 0 1;
    }
    DispatchLog .dl-title {
        text-style: bold;
        dock: top;
    }
    DispatchLog RichLog {
        height: 1fr;
    }
    """

    MAX_LINES = 500

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._line_count = 0

    def compose(self) -> ComposeResult:
        yield Static("Dispatch Log", classes="dl-title")
        yield RichLog(highlight=True, markup=True, wrap=True, id="dl-log")

    def append(self, text: str) -> None:
        """Append a line to the dispatch log."""
        try:
            log = self.query_one("#dl-log", RichLog)
            log.write(text)
            self._line_count += 1
            if self._line_count > self.MAX_LINES:
                log.clear()
                log.write("[dim]--- log trimmed ---[/]")
                self._line_count = 1
        except Exception:
            pass

    def clear_log(self) -> None:
        try:
            self.query_one("#dl-log", RichLog).clear()
            self._line_count = 0
        except Exception:
            pass
