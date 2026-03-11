"""Tests for trouter.health.remediation."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


from trouter.health.heartbeat import HeartbeatData
from trouter.health.remediation import (
    EscalationLevel,
    RemediationConfig,
    RemediationHandler,
    _escape_applescript,
    verify_freeze,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_heartbeat(session_id: str, age: float, status: str = "active") -> HeartbeatData:
    """Return a HeartbeatData whose unix_time is *age* seconds in the past."""
    return HeartbeatData(
        session_id=session_id,
        timestamp="2026-03-11T10:00:00",
        unix_time=time.time() - age,
        pid=12345,
        status=status,
        working_dir="/tmp/test",
        last_tool="Read",
        context_tokens=5000,
    )


# ---------------------------------------------------------------------------
# test_escalation_levels
# ---------------------------------------------------------------------------

class TestEscalationLevels:
    """get_escalation_level maps heartbeat age to the correct EscalationLevel."""

    def setup_method(self):
        self.config = RemediationConfig(
            warning_threshold=30.0,
            alert_threshold=60.0,
            compact_threshold=90.0,
            kill_threshold=120.0,
        )

    def test_healthy_returns_none_level(self, tmp_path):
        handler = RemediationHandler(config=self.config, health_dir=tmp_path)
        hb = _make_heartbeat("sess", age=10.0)
        assert handler.get_escalation_level(hb) == EscalationLevel.NONE

    def test_warning_range(self, tmp_path):
        handler = RemediationHandler(config=self.config, health_dir=tmp_path)
        hb = _make_heartbeat("sess", age=45.0)
        assert handler.get_escalation_level(hb) == EscalationLevel.WARNING

    def test_alert_range(self, tmp_path):
        handler = RemediationHandler(config=self.config, health_dir=tmp_path)
        hb = _make_heartbeat("sess", age=75.0)
        assert handler.get_escalation_level(hb) == EscalationLevel.ALERT

    def test_compact_range(self, tmp_path):
        handler = RemediationHandler(config=self.config, health_dir=tmp_path)
        hb = _make_heartbeat("sess", age=105.0)
        assert handler.get_escalation_level(hb) == EscalationLevel.COMPACT

    def test_kill_range(self, tmp_path):
        handler = RemediationHandler(config=self.config, health_dir=tmp_path)
        hb = _make_heartbeat("sess", age=150.0)
        assert handler.get_escalation_level(hb) == EscalationLevel.KILL

    def test_boundary_exactly_at_warning_threshold(self, tmp_path):
        """Age exactly equal to warning_threshold is WARNING, not NONE."""
        handler = RemediationHandler(config=self.config, health_dir=tmp_path)
        hb = _make_heartbeat("sess", age=30.0)
        level = handler.get_escalation_level(hb)
        assert level == EscalationLevel.WARNING

    def test_boundary_exactly_at_kill_threshold(self, tmp_path):
        """Age exactly equal to kill_threshold is KILL."""
        handler = RemediationHandler(config=self.config, health_dir=tmp_path)
        hb = _make_heartbeat("sess", age=120.0)
        assert handler.get_escalation_level(hb) == EscalationLevel.KILL


# ---------------------------------------------------------------------------
# test_debouncing
# ---------------------------------------------------------------------------

class TestDebouncing:
    """should_take_action returns False within the debounce window."""

    def test_first_call_returns_true(self, tmp_path):
        handler = RemediationHandler(health_dir=tmp_path)
        result = handler.should_take_action("sess-1", EscalationLevel.WARNING)
        assert result is True

    def test_second_call_within_window_returns_false(self, tmp_path):
        handler = RemediationHandler(health_dir=tmp_path)
        handler.should_take_action("sess-2", EscalationLevel.WARNING)
        # Immediately call again — still within debounce window
        result = handler.should_take_action("sess-2", EscalationLevel.WARNING)
        assert result is False

    def test_different_session_not_debounced(self, tmp_path):
        handler = RemediationHandler(health_dir=tmp_path)
        handler.should_take_action("sess-a", EscalationLevel.WARNING)
        # A *different* session must not be debounced
        result = handler.should_take_action("sess-b", EscalationLevel.WARNING)
        assert result is True

    def test_different_level_not_debounced(self, tmp_path):
        """Same session but different escalation level has its own debounce key."""
        handler = RemediationHandler(health_dir=tmp_path)
        handler.should_take_action("sess-c", EscalationLevel.WARNING)
        # ALERT for the same session should not be debounced
        result = handler.should_take_action("sess-c", EscalationLevel.ALERT)
        assert result is True

    def test_after_debounce_window_expires_returns_true(self, tmp_path):
        """After the debounce window passes, the action is allowed again."""
        handler = RemediationHandler(health_dir=tmp_path)
        handler._action_debounce = 0.0  # collapse window to 0 for this test
        handler.should_take_action("sess-d", EscalationLevel.WARNING)
        result = handler.should_take_action("sess-d", EscalationLevel.WARNING)
        assert result is True


# ---------------------------------------------------------------------------
# test_escape_applescript
# ---------------------------------------------------------------------------

class TestEscapeApplescript:
    """_escape_applescript must sanitise strings for AppleScript interpolation."""

    def test_plain_string_unchanged(self):
        assert _escape_applescript("hello world") == "hello world"

    def test_backslash_is_doubled(self):
        assert _escape_applescript("C:\\Users\\foo") == "C:\\\\Users\\\\foo"

    def test_double_quote_is_escaped(self):
        assert _escape_applescript('say "hi"') == 'say \\"hi\\"'

    def test_newlines_are_not_escaped(self):
        """AppleScript does NOT require \\n escaping — they should pass through."""
        s = "line1\nline2"
        result = _escape_applescript(s)
        assert "\n" in result

    def test_backslash_before_quote(self):
        """A \\\" sequence must become \\\\\\\" (backslash doubled, then quote escaped)."""
        s = '\\"'
        result = _escape_applescript(s)
        assert result == '\\\\\\"'

    def test_empty_string(self):
        assert _escape_applescript("") == ""

    def test_only_backslashes(self):
        assert _escape_applescript("\\\\") == "\\\\\\\\"


# ---------------------------------------------------------------------------
# test_verify_freeze_process_not_found
# ---------------------------------------------------------------------------

class TestVerifyFreezeProcessNotFound:
    """ProcessLookupError (no such process) means freeze is confirmed."""

    def test_process_lookup_error_returns_true(self):
        with patch("os.kill", side_effect=ProcessLookupError):
            result = verify_freeze("test-session", 99999)
        assert result is True

    def test_permission_error_falls_through_to_tmux_check(self):
        """PermissionError means the process exists; tmux check decides outcome."""
        with patch("os.kill", side_effect=PermissionError):
            # tmux returns non-zero (session unknown) -> conservative False
            with patch(
                "subprocess.run",
                return_value=MagicMock(returncode=1),
            ):
                result = verify_freeze("test-session", 99999)
        # Conservative path: uncertain state -> False
        assert result is False

    def test_tmux_timeout_returns_true(self):
        """If tmux capture-pane times out, the session is confirmed frozen."""
        import subprocess
        with patch("os.kill", side_effect=PermissionError):
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=5),
            ):
                result = verify_freeze("frozen-session", 99999)
        assert result is True

    def test_tmux_capture_success_returns_false(self):
        """tmux responds promptly -> session is probably fine -> return False."""
        with patch("os.kill", side_effect=PermissionError):
            with patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0),
            ):
                result = verify_freeze("live-session", 99999)
        assert result is False


# ---------------------------------------------------------------------------
# test_checkpoint_save
# ---------------------------------------------------------------------------

class TestCheckpointSave:
    """_save_checkpoint creates a file with the expected JSON structure."""

    def test_checkpoint_save(self, tmp_path):
        handler = RemediationHandler(health_dir=tmp_path)
        hb = _make_heartbeat("chk-session", age=130.0)
        hb.last_tool = "Bash"
        hb.context_tokens = 42000
        hb.working_dir = "/workspace/project"

        path_str = handler._save_checkpoint(hb)

        assert path_str is not None, "_save_checkpoint must return a file path"

        checkpoint_file = Path(path_str)
        assert checkpoint_file.exists(), "checkpoint file must be created on disk"

        data = json.loads(checkpoint_file.read_text())
        assert data["session_id"] == "chk-session"
        assert data["working_dir"] == "/workspace/project"
        assert data["pid"] == hb.pid
        assert data["last_tool"] == "Bash"
        assert data["context_tokens"] == 42000
        assert data["reason"] == "freeze_recovery"
        assert "timestamp" in data

    def test_checkpoint_saved_in_checkpoints_subdir(self, tmp_path):
        handler = RemediationHandler(health_dir=tmp_path)
        hb = _make_heartbeat("dir-check-session", age=130.0)

        path_str = handler._save_checkpoint(hb)
        checkpoint_file = Path(path_str)

        assert checkpoint_file.parent.name == "checkpoints"
        assert checkpoint_file.parent.parent == tmp_path

    def test_checkpoint_filename_contains_session_id(self, tmp_path):
        handler = RemediationHandler(health_dir=tmp_path)
        hb = _make_heartbeat("my-special-session", age=130.0)

        path_str = handler._save_checkpoint(hb)
        checkpoint_file = Path(path_str)

        assert "my-special-session" in checkpoint_file.name


# ---------------------------------------------------------------------------
# test_handle_heartbeat_none_for_healthy
# ---------------------------------------------------------------------------

class TestHandleHeartbeatNoneForHealthy:
    """handle_heartbeat returns None when heartbeat is below warning threshold."""

    def test_handle_heartbeat_none_for_healthy(self, tmp_path):
        config = RemediationConfig(warning_threshold=30.0)
        handler = RemediationHandler(config=config, health_dir=tmp_path)

        hb = _make_heartbeat("healthy-sess", age=5.0)
        result = handler.handle_heartbeat(hb)

        assert result is None

    def test_handle_heartbeat_returns_action_for_warning(self, tmp_path):
        """Crossing the warning threshold returns a RemediationAction (not None)."""
        config = RemediationConfig(warning_threshold=30.0)
        handler = RemediationHandler(config=config, health_dir=tmp_path)

        hb = _make_heartbeat("warn-sess", age=45.0)
        result = handler.handle_heartbeat(hb)

        assert result is not None
        assert result.level == "WARNING"
        assert result.session_id == "warn-sess"
        assert result.success is True

    def test_handle_heartbeat_debounce_after_first(self, tmp_path):
        """Second call for the same session within debounce window -> None."""
        config = RemediationConfig(warning_threshold=30.0)
        handler = RemediationHandler(config=config, health_dir=tmp_path)

        hb = _make_heartbeat("debounced-sess", age=45.0)
        first = handler.handle_heartbeat(hb)
        second = handler.handle_heartbeat(hb)

        assert first is not None
        assert second is None

    def test_handle_heartbeat_alert_writes_file(self, tmp_path):
        """ALERT level must write an alert file to the alerts directory."""
        config = RemediationConfig(
            warning_threshold=30.0,
            alert_threshold=60.0,
        )
        handler = RemediationHandler(config=config, health_dir=tmp_path)

        hb = _make_heartbeat("alert-sess", age=75.0)
        result = handler.handle_heartbeat(hb)

        assert result is not None
        assert result.level == "ALERT"

        alert_files = list((tmp_path / "alerts").glob("alert_alert-sess_*.json"))
        assert len(alert_files) == 1, "Exactly one alert file must be created"

        alert_data = json.loads(alert_files[0].read_text())
        assert alert_data["session_id"] == "alert-sess"
