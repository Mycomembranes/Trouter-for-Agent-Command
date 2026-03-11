"""Tests for trouter.core.pool."""

import json
import threading
import time

from trouter.core.models import AgentState
from trouter.core.pool import StandbyConfig, StandbyPool


class TestStandbyConfig:
    """StandbyConfig dataclass defaults."""

    def test_standby_config_defaults(self):
        cfg = StandbyConfig()

        assert cfg.codex_slots == 0
        assert cfg.claude_slots == 0
        assert cfg.composer_slots == 2
        assert cfg.check_interval == 10
        assert cfg.auto_compact is True
        assert cfg.compact_threshold == 20
        assert cfg.task_timeout == 600

    def test_standby_config_custom(self):
        cfg = StandbyConfig(codex_slots=3, composer_slots=5, task_timeout=120)
        assert cfg.codex_slots == 3
        assert cfg.composer_slots == 5
        assert cfg.task_timeout == 120


class TestStandbyPoolAddAgent:
    """Pool initialisation and slot creation."""

    def test_standby_pool_add_agent(self, tmp_path):
        """Pool creates the correct number and types of agent slots."""
        cfg = StandbyConfig(codex_slots=1, claude_slots=1, composer_slots=2)
        pool = StandbyPool(cfg, cli_bin=tmp_path / "bin" / "cli")

        summary = pool.summary()
        ids = {s["id"] for s in summary}

        assert "codex-1" in ids
        assert "claude-1" in ids
        assert "composer-1" in ids
        assert "composer-2" in ids
        assert len(summary) == 4

        # All slots start in STANDBY
        for slot in summary:
            assert slot["state"] == AgentState.STANDBY.value

    def test_empty_pool(self, tmp_path):
        """Zero-slot config creates an empty pool."""
        cfg = StandbyConfig(codex_slots=0, claude_slots=0, composer_slots=0)
        pool = StandbyPool(cfg, cli_bin=tmp_path / "bin" / "cli")
        assert pool.summary() == []


class TestStandbyPoolDispatch:
    """Dispatch without real subprocess execution."""

    def test_standby_pool_dispatch(self, tmp_path, monkeypatch):
        """Dispatch changes agent state from STANDBY to BUSY, then ERROR when binary missing."""
        # Ensure binary resolution always returns empty so the background thread
        # fails fast instead of finding a real binary on the developer machine.
        monkeypatch.setattr(
            "trouter.core.pool.resolve_native_agent", lambda: ""
        )
        monkeypatch.setattr(
            "trouter.core.pool.resolve_claude_bin", lambda: ""
        )

        cfg = StandbyConfig(composer_slots=1)
        pool = StandbyPool(cfg, cli_bin=tmp_path / "bin" / "cli")

        # dispatch() returns True and transitions the slot to BUSY immediately.
        result = pool.dispatch("composer-1", "echo hello")
        assert result is True

        # Wait for the background thread to complete (binary not found -> fast failure).
        for _ in range(30):
            time.sleep(0.1)
            summary = {s["id"]: s for s in pool.summary()}
            if summary["composer-1"]["state"] != AgentState.BUSY.value:
                break

        # After the thread completes, the slot should be in ERROR (no binary found)
        summary = {s["id"]: s for s in pool.summary()}
        assert summary["composer-1"]["state"] == AgentState.ERROR.value

    def test_dispatch_nonexistent_agent(self, tmp_path):
        """Dispatching to an unknown agent ID returns False."""
        cfg = StandbyConfig(composer_slots=1)
        pool = StandbyPool(cfg, cli_bin=tmp_path / "bin" / "cli")

        assert pool.dispatch("nonexistent-99", "task") is False

    def test_dispatch_auto_selects_composer(self, tmp_path):
        """dispatch_auto prefers composer slots."""
        cfg = StandbyConfig(codex_slots=1, composer_slots=1)
        pool = StandbyPool(cfg, cli_bin=tmp_path / "bin" / "cli")

        agent_id = pool.dispatch_auto("task")
        assert agent_id == "composer-1"

    def test_dispatch_respects_shutdown(self, tmp_path):
        """After shutdown, dispatch returns False."""
        cfg = StandbyConfig(composer_slots=1)
        pool = StandbyPool(cfg, cli_bin=tmp_path / "bin" / "cli")

        pool.shutdown()
        assert pool.dispatch("composer-1", "task") is False
        assert pool.dispatch_auto("task") is None

    def test_dispatch_auto_passes_selected_model(self, tmp_path, monkeypatch):
        """dispatch_auto must forward the chosen model to the worker thread."""
        seen: dict[str, str] = {}
        called = threading.Event()

        def fake_run(self, agent_id: str, task: str, model_id: str) -> None:
            seen["agent_id"] = agent_id
            seen["task"] = task
            seen["model_id"] = model_id
            called.set()

        monkeypatch.setattr(StandbyPool, "_run_agent_task", fake_run)

        pool = StandbyPool(StandbyConfig(composer_slots=1), cli_bin=tmp_path / "bin" / "cli")
        agent_id = pool.dispatch_auto("security audit", model_id="gpt-5.3-codex-xhigh")

        assert agent_id == "composer-1"
        assert called.wait(timeout=1)
        assert seen == {
            "agent_id": "composer-1",
            "task": "security audit",
            "model_id": "gpt-5.3-codex-xhigh",
        }

    def test_pool_uses_explicit_config_path_for_dispatch_mode(self, tmp_path):
        """Pool config lookup should use the provided config_path, not cli_bin-relative guesses."""
        config_path = tmp_path / "etc" / "cursor_config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(json.dumps({"dispatch_mode": "local"}))

        pool = StandbyPool(
            StandbyConfig(composer_slots=1),
            cli_bin=tmp_path / "bin" / "cursor-agent",
            config_path=config_path,
        )

        assert pool._read_dispatch_mode() == "local"


class TestAgentState:
    """AgentState enum members."""

    def test_agent_state_enum(self):
        assert AgentState.STANDBY.value == "STANDBY"
        assert AgentState.BUSY.value == "BUSY"
        assert AgentState.ERROR.value == "ERROR"
        assert AgentState.OFFLINE.value == "OFFLINE"

        # Verify all four states exist
        assert len(AgentState) == 4

    def test_agent_state_identity(self):
        """Enum members compare correctly."""
        assert AgentState.STANDBY is AgentState.STANDBY
        assert AgentState.STANDBY != AgentState.BUSY
