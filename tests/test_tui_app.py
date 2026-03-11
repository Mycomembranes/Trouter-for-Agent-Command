"""Tests for TUI data normalization helpers."""

import asyncio
import json
import time

from trouter.tui.app import (
    TrouterApp,
    _agent_state,
    _format_uptime,
    _heartbeat_for_session,
    _heartbeat_snapshots,
    _pool_state,
    _watchdog_status,
)


class TestHeartbeatSnapshots:
    """Heartbeat file discovery should understand current and legacy layouts."""

    def test_prefers_latest_file_per_session(self, tmp_path):
        heartbeats_dir = tmp_path / "heartbeats"
        heartbeats_dir.mkdir()

        old_json = heartbeats_dir / "agent-1.json"
        old_json.write_text(json.dumps({"session_id": "agent-1", "unix_time": 1}))

        new_hb = heartbeats_dir / "agent-1.heartbeat"
        new_hb.write_text(json.dumps({"session_id": "agent-1", "unix_time": 2}))

        snapshots = _heartbeat_snapshots(heartbeats_dir)

        assert snapshots == [{"session_id": "agent-1", "unix_time": 2}]
        assert _heartbeat_for_session(heartbeats_dir, "agent-1") == snapshots[0]


class TestAgentStateHelpers:
    """Heartbeat state mapping for dashboard and pool summaries."""

    def test_agent_state_uses_explicit_busy_modes(self):
        data = {"unix_time": 0, "state": "plan_mode"}
        assert _agent_state(data, now=10) == "busy"

    def test_agent_state_falls_back_to_age_based_freeze(self):
        data = {"unix_time": 0}
        assert _agent_state(data, now=120) == "frozen"

    def test_pool_state_maps_error_and_age(self):
        assert _pool_state({"unix_time": 0, "state": "error"}, now=10) == "ERROR"
        assert _pool_state({"unix_time": 0}, now=10) == "STANDBY"
        assert _pool_state({"unix_time": 0}, now=200) == "OFFLINE"


class TestWatchdogStatusHelpers:
    """Watchdog status normalization across schema versions."""

    def test_watchdog_status_uses_current_daemon_fields(self):
        status = _watchdog_status(
            {
                "running": True,
                "uptime_seconds": 125,
                "checks_performed": 8,
                "actions_taken": 2,
            }
        )

        assert status == {
            "running": True,
            "uptime": "2m05s",
            "checks": 8,
            "actions": 2,
            "alerts": [],
        }

    def test_format_uptime_short_values(self):
        assert _format_uptime(9) == "9s"
        assert _format_uptime(3661) == "1h01m"


class TestTrouterAppStartup:
    """Basic startup coverage for the Textual app."""

    def test_dashboard_mounts(self):
        async def run_app() -> None:
            app = TrouterApp()
            async with app.run_test() as pilot:
                await pilot.pause()
                assert app.screen is not None

        asyncio.run(run_app())

    def test_dashboard_polls_and_mounts_agent_cards(self, tmp_path):
        heartbeats = tmp_path / "heartbeats"
        heartbeats.mkdir()
        (heartbeats / "agent-a.heartbeat").write_text(
            json.dumps(
                {
                    "session_id": "agent-a",
                    "unix_time": time.time(),
                    "status": "active",
                    "context_pct": 50,
                }
            )
        )

        async def run_app() -> None:
            app = TrouterApp()
            app.HEARTBEAT_DIR = heartbeats
            async with app.run_test() as pilot:
                await pilot.pause()
                app._poll_heartbeats()
                await pilot.pause()
                assert "agent-a" in app.screen.grid.card_ids

        asyncio.run(run_app())
