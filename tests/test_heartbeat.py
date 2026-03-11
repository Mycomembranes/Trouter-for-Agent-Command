"""Tests for trouter.health.heartbeat."""

import json
import os
import time


from trouter.health.heartbeat import HeartbeatData, HeartbeatManager, get_session_id


class TestHeartbeatDataCreation:
    """HeartbeatData dataclass construction and serialisation."""

    def test_heartbeat_data_creation(self):
        now = time.time()
        hb = HeartbeatData(
            session_id="test-session",
            timestamp="2026-03-11T10:00:00",
            unix_time=now,
            pid=12345,
            status="active",
            working_dir="/tmp/test",
            last_tool="Read",
            context_tokens=5000,
        )

        assert hb.session_id == "test-session"
        assert hb.pid == 12345
        assert hb.status == "active"
        assert hb.working_dir == "/tmp/test"
        assert hb.last_tool == "Read"
        assert hb.context_tokens == 5000
        # Optional fields default to None
        assert hb.window_number is None
        assert hb.window_name is None
        assert hb.state is None
        assert hb.context_pct is None

    def test_heartbeat_data_roundtrip(self):
        """to_json -> from_json produces identical data."""
        now = time.time()
        original = HeartbeatData(
            session_id="roundtrip",
            timestamp="2026-03-11T10:00:00",
            unix_time=now,
            pid=999,
            status="idle",
            working_dir="/home/user",
            last_tool="Bash",
            context_tokens=10000,
            window_number=2,
            window_name="Dev",
            state="busy",
            context_pct=42,
        )

        restored = HeartbeatData.from_json(original.to_json())

        assert restored.session_id == original.session_id
        assert restored.pid == original.pid
        assert restored.status == original.status
        assert restored.window_number == original.window_number
        assert restored.window_name == original.window_name
        assert restored.state == original.state
        assert restored.context_pct == original.context_pct

    def test_from_json_ignores_unknown_fields(self):
        """Unknown keys in JSON are silently ignored."""
        raw = json.dumps({
            "session_id": "test",
            "timestamp": "2026-03-11T10:00:00",
            "unix_time": time.time(),
            "pid": 1,
            "status": "active",
            "working_dir": "/tmp",
            "extra_field": "should_be_ignored",
            "another_unknown": 42,
        })
        hb = HeartbeatData.from_json(raw)
        assert hb.session_id == "test"
        assert not hasattr(hb, "extra_field")

    def test_age_seconds(self):
        """age_seconds returns a positive value for a past timestamp."""
        past = time.time() - 60
        hb = HeartbeatData(
            session_id="old",
            timestamp="2026-03-11T09:59:00",
            unix_time=past,
            pid=1,
            status="active",
            working_dir="/tmp",
        )
        age = hb.age_seconds()
        assert age >= 59  # allow small float drift


class TestHeartbeatManagerWriteRead:
    """HeartbeatManager write/read cycle using tmp_path for isolation."""

    def test_heartbeat_manager_write_read(self, tmp_path):
        mgr = HeartbeatManager(health_dir=tmp_path)

        written = mgr.write_heartbeat(
            session_id="mgr-test",
            status="active",
            pid=54321,
            working_dir="/tmp/mgr",
            last_tool="Edit",
            context_tokens=7000,
        )
        assert written.session_id == "mgr-test"
        assert written.pid == 54321

        # Read it back
        read_back = mgr.get_heartbeat("mgr-test")
        assert read_back is not None
        assert read_back.session_id == "mgr-test"
        assert read_back.pid == 54321
        assert read_back.status == "active"
        assert read_back.last_tool == "Edit"

    def test_write_overwrites_previous(self, tmp_path):
        mgr = HeartbeatManager(health_dir=tmp_path)

        mgr.write_heartbeat("overwrite-test", status="active", pid=100)
        mgr.write_heartbeat("overwrite-test", status="idle", pid=200)

        hb = mgr.get_heartbeat("overwrite-test")
        assert hb is not None
        assert hb.status == "idle"
        assert hb.pid == 200

    def test_get_all_heartbeats(self, tmp_heartbeat_dir):
        """Uses the conftest fixture with 3 pre-written heartbeats."""
        mgr = HeartbeatManager(health_dir=tmp_heartbeat_dir)
        all_hb = mgr.get_all_heartbeats()
        assert len(all_hb) == 3

        ids = {hb.session_id for hb in all_hb}
        assert ids == {"agent_healthy", "agent_warning", "agent_frozen"}

    def test_get_nonexistent_heartbeat(self, tmp_path):
        mgr = HeartbeatManager(health_dir=tmp_path)
        assert mgr.get_heartbeat("does-not-exist") is None

    def test_remove_heartbeat(self, tmp_path):
        mgr = HeartbeatManager(health_dir=tmp_path)
        mgr.write_heartbeat("to-remove", status="active")

        assert mgr.remove_heartbeat("to-remove") is True
        assert mgr.get_heartbeat("to-remove") is None
        assert mgr.remove_heartbeat("to-remove") is False  # already gone

    def test_health_summary(self, tmp_heartbeat_dir):
        mgr = HeartbeatManager(health_dir=tmp_heartbeat_dir)
        summary = mgr.get_health_summary()

        assert summary["total_sessions"] == 3
        assert summary["healthy"] == 1
        assert summary["warning"] == 1
        assert summary["frozen"] == 1


class TestGetSessionId:
    """get_session_id priority: env var > project hash > PID."""

    def test_get_session_id_explicit(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_SESSION_ID", "explicit-123")
        monkeypatch.delenv("CLAUDE_PROJECT_HASH", raising=False)

        assert get_session_id() == "explicit-123"

    def test_get_session_id_project_hash(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_HASH", "abcdef1234567890")

        result = get_session_id()
        assert result == "project_abcdef123456"

    def test_get_session_id_fallback_pid(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_HASH", raising=False)

        result = get_session_id()
        assert result == f"pid_{os.getpid()}"
