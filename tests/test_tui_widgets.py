"""Basic widget unit tests for trouter.tui.widgets.

These tests validate widget logic without mounting a full Textual app.
For rendering tests, use pytest-textual-snapshot.
"""


from trouter.tui.widgets.agent_card import AgentCard
from trouter.tui.widgets.health_bar import HealthBar
from trouter.tui.widgets.agent_grid import AgentGrid


class TestAgentCardStateBadge:
    """AgentCard._state_badge() returns correct Rich markup."""

    def test_healthy_badge(self):
        card = AgentCard(session_id="s1", state="healthy")
        assert card._state_badge() == "[green]ACTIVE[/]"

    def test_busy_badge(self):
        card = AgentCard(session_id="s1", state="busy")
        assert card._state_badge() == "[blue]BUSY[/]"

    def test_idle_badge(self):
        card = AgentCard(session_id="s1", state="idle")
        assert card._state_badge() == "[dim]IDLE[/]"

    def test_warning_badge(self):
        card = AgentCard(session_id="s1", state="warning")
        assert card._state_badge() == "[yellow]WARNING[/]"

    def test_frozen_badge(self):
        card = AgentCard(session_id="s1", state="frozen")
        assert card._state_badge() == "[red]FROZEN[/]"

    def test_error_badge(self):
        card = AgentCard(session_id="s1", state="error")
        assert card._state_badge() == "[red]ERROR[/]"

    def test_unknown_badge(self):
        card = AgentCard(session_id="s1", state="unknown")
        assert card._state_badge() == "[dim]---[/]"

    def test_unrecognised_state_fallback(self):
        card = AgentCard(session_id="s1", state="custom_state")
        assert card._state_badge() == "[dim]custom_state[/]"


class TestHealthBarPercentageClamp:
    """HealthBar clamps percentage to 0-100."""

    def test_normal_percentage(self):
        bar = HealthBar(percentage=50)
        assert bar._percentage == 50

    def test_zero_percentage(self):
        bar = HealthBar(percentage=0)
        assert bar._percentage == 0

    def test_full_percentage(self):
        bar = HealthBar(percentage=100)
        assert bar._percentage == 100

    def test_negative_clamped_to_zero(self):
        bar = HealthBar(percentage=-10)
        assert bar._percentage == 0

    def test_over_100_clamped_to_100(self):
        bar = HealthBar(percentage=150)
        assert bar._percentage == 100

    def test_default_is_100(self):
        bar = HealthBar()
        assert bar._percentage == 100


class TestAgentGridAddRemove:
    """AgentGrid card tracking (internal dict, no DOM mounting)."""

    def test_columns_for_width(self):
        grid = AgentGrid()
        assert grid._columns_for_width(40) == 1
        assert grid._columns_for_width(70) == 2
        assert grid._columns_for_width(120) == 3

    def test_add_agent_tracks_card(self):
        grid = AgentGrid()
        # add_agent stores the card in _cards, but mount() will fail
        # without a running app. We test the internal tracking dict directly.
        card = AgentCard(
            session_id="agent-1",
            display_name="Agent 1",
            state="healthy",
            context_pct=80,
        )
        grid._cards["agent-1"] = card

        assert "agent-1" in grid.card_ids
        assert grid.get_card("agent-1") is card

    def test_remove_agent_untracks_card(self):
        grid = AgentGrid()
        card = AgentCard(session_id="agent-2", state="idle")
        grid._cards["agent-2"] = card

        # Simulate removal (without DOM, we pop from _cards directly)
        removed = grid._cards.pop("agent-2", None)
        assert removed is card
        assert "agent-2" not in grid.card_ids
        assert grid.get_card("agent-2") is None

    def test_card_ids_property(self):
        grid = AgentGrid()
        grid._cards["a"] = AgentCard(session_id="a", state="healthy")
        grid._cards["b"] = AgentCard(session_id="b", state="busy")
        grid._cards["c"] = AgentCard(session_id="c", state="frozen")

        assert set(grid.card_ids) == {"a", "b", "c"}

    def test_empty_grid(self):
        grid = AgentGrid()
        assert grid.card_ids == []
        assert grid.get_card("anything") is None
