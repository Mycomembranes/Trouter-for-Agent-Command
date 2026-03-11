"""Ctrl+P fuzzy command palette."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option


# Default commands available in the palette
DEFAULT_COMMANDS = [
    ("compact", "Compact selected agent"),
    ("kill", "Kill selected agent"),
    ("refresh", "Refresh all agents"),
    ("stats", "Switch to stats view"),
    ("dashboard", "Switch to dashboard"),
    ("hide-idle", "Hide idle agents"),
    ("show-all", "Show all agents"),
    ("quit", "Quit trouter"),
]


class CommandSelected(Message):
    """Emitted when a command is selected from the palette."""

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command


class CommandPalette(ModalScreen[str | None]):
    """Fuzzy command finder overlay (Ctrl+P)."""

    DEFAULT_CSS = """
    CommandPalette {
        align: center middle;
    }
    CommandPalette > Vertical {
        width: 60;
        max-width: 80%;
        height: auto;
        max-height: 60%;
        border: thick $accent;
        background: $surface;
        padding: 1;
    }
    CommandPalette Input {
        margin: 0 0 1 0;
    }
    CommandPalette OptionList {
        height: auto;
        max-height: 20;
    }
    """

    BINDINGS = [("escape", "dismiss_palette", "Close")]

    def __init__(self, commands: list[tuple[str, str]] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._commands = commands or DEFAULT_COMMANDS

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(placeholder="Type a command...", id="cp-input")
            yield OptionList(
                *[Option(f"{cmd}  [dim]{desc}[/]", id=cmd) for cmd, desc in self._commands],
                id="cp-list",
            )

    def on_mount(self) -> None:
        self.query_one("#cp-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter commands as user types."""
        query = event.value.lower().strip()
        option_list = self.query_one("#cp-list", OptionList)
        option_list.clear_options()
        for cmd, desc in self._commands:
            if not query or query in cmd.lower() or query in desc.lower():
                option_list.add_option(Option(f"{cmd}  [dim]{desc}[/]", id=cmd))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        cmd_id = event.option.id
        if cmd_id:
            self.dismiss(cmd_id)

    def action_dismiss_palette(self) -> None:
        self.dismiss(None)
