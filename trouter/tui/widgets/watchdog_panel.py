"""Watchdog daemon status panel."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static



class WatchdogPanel(Vertical):
    """Shows watchdog daemon uptime, check count, and recent alerts."""

    DEFAULT_CSS = """
    WatchdogPanel {
        height: auto;
        min-height: 6;
        border: solid $primary;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    WatchdogPanel .wp-title {
        text-style: bold;
        color: $text;
    }
    WatchdogPanel .wp-row {
        color: $text-muted;
    }
    WatchdogPanel .wp-alert {
        color: $warning;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._running = False
        self._uptime = "—"
        self._checks = 0
        self._actions = 0
        self._alerts: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static("Watchdog", classes="wp-title")
        yield Static(self._status_line(), classes="wp-row", id="wp-status")
        yield Static("", classes="wp-alert", id="wp-alerts")

    def _status_line(self) -> str:
        icon = "[green]●[/]" if self._running else "[red]○[/]"
        return f"{icon} uptime {self._uptime}  checks {self._checks}  actions {self._actions}"

    def update_status(
        self,
        running: bool = False,
        uptime: str = "—",
        checks: int = 0,
        actions: int = 0,
        alerts: list[str] | None = None,
    ) -> None:
        self._running = running
        self._uptime = uptime
        self._checks = checks
        self._actions = actions
        if alerts is not None:
            self._alerts = alerts[-3:]  # keep last 3
        try:
            self.query_one("#wp-status", Static).update(self._status_line())
            alert_text = "\n".join(self._alerts) if self._alerts else ""
            self.query_one("#wp-alerts", Static).update(alert_text)
        except Exception:
            pass
