"""Responsive grid of agent cards."""

from textual import events
from textual.containers import Container

from trouter.tui.widgets.agent_card import AgentCard


class AgentGrid(Container):
    """Responsive CSS grid layout for agent cards."""

    DEFAULT_CSS = """
    AgentGrid {
        layout: grid;
        grid-size: 3;
        grid-gutter: 1;
        padding: 1;
        height: auto;
        min-height: 10;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cards: dict[str, AgentCard] = {}

    @staticmethod
    def _columns_for_width(width: int) -> int:
        """Choose the number of grid columns for the available width."""
        if width < 50:
            return 1
        if width < 80:
            return 2
        return 3

    def _update_grid_columns(self, width: int) -> None:
        """Apply responsive column sizing without relying on CSS media queries."""
        self.styles.grid_size_columns = self._columns_for_width(width)

    def on_mount(self) -> None:
        """Set the initial grid column count."""
        self._update_grid_columns(self.size.width)

    def on_resize(self, event: events.Resize) -> None:
        """Keep the grid responsive as the terminal size changes."""
        self._update_grid_columns(event.size.width)

    def add_agent(
        self,
        session_id: str,
        display_name: str = "",
        state: str = "unknown",
        context_pct: int = 100,
        task_preview: str = "",
    ) -> AgentCard:
        """Add a new agent card to the grid."""
        if session_id in self._cards:
            card = self._cards[session_id]
            card.update_data(state=state, context_pct=context_pct, task_preview=task_preview)
            return card

        card = AgentCard(
            session_id=session_id,
            display_name=display_name,
            state=state,
            context_pct=context_pct,
            task_preview=task_preview,
        )
        self._cards[session_id] = card
        self.mount(card)
        return card

    def remove_agent(self, session_id: str) -> None:
        """Remove an agent card from the grid."""
        card = self._cards.pop(session_id, None)
        if card:
            card.remove()

    def update_agent(self, session_id: str, **kwargs) -> None:
        """Update an existing agent card."""
        card = self._cards.get(session_id)
        if card:
            card.update_data(**kwargs)

    def get_card(self, session_id: str) -> AgentCard | None:
        return self._cards.get(session_id)

    @property
    def card_ids(self) -> list[str]:
        return list(self._cards.keys())
