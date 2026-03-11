"""Context usage progress bar widget."""

from textual.widgets import ProgressBar, Static
from textual.app import ComposeResult


class HealthBar(Static):
    """Compact context usage bar with percentage label."""

    DEFAULT_CSS = """
    HealthBar {
        height: 1;
        layout: horizontal;
    }
    HealthBar .health-label {
        width: 5;
        text-align: right;
        color: $text-muted;
    }
    HealthBar ProgressBar {
        width: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, percentage: int = 100, **kwargs):
        super().__init__(**kwargs)
        self._percentage = max(0, min(100, percentage))

    def compose(self) -> ComposeResult:
        bar = ProgressBar(total=100, show_eta=False, show_percentage=False)
        bar.advance(self._percentage)
        yield bar
        yield Static(f"{self._percentage}%", classes="health-label")

    def update_percentage(self, pct: int) -> None:
        """Update the displayed percentage."""
        self._percentage = max(0, min(100, pct))
        try:
            bar = self.query_one(ProgressBar)
            bar.progress = self._percentage
            label = self.query(".health-label").first(Static)
            label.update(f"{self._percentage}%")
        except Exception:
            pass
