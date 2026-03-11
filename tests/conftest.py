"""Shared pytest fixtures for the trouter test suite."""

import json
import time
from pathlib import Path

import pytest

from trouter.core.config import TrouterConfig


@pytest.fixture
def tmp_heartbeat_dir(tmp_path: Path) -> Path:
    """Create a temp directory with three fake heartbeat files.

    Agents:
        agent_healthy  -- heartbeat written just now  (< 30 s old)
        agent_warning  -- heartbeat written 45 s ago  (30-60 s)
        agent_frozen   -- heartbeat written 120 s ago (> 60 s)
    """
    hb_dir = tmp_path / "heartbeats"
    hb_dir.mkdir()

    now = time.time()

    healthy = {
        "session_id": "agent_healthy",
        "timestamp": "2026-03-11T10:00:00",
        "unix_time": now,
        "pid": 1001,
        "status": "active",
        "working_dir": "/tmp/healthy",
        "last_tool": "Read",
        "context_tokens": 5000,
    }

    warning = {
        "session_id": "agent_warning",
        "timestamp": "2026-03-11T09:59:15",
        "unix_time": now - 45,
        "pid": 1002,
        "status": "active",
        "working_dir": "/tmp/warning",
        "last_tool": "Bash",
        "context_tokens": 12000,
    }

    frozen = {
        "session_id": "agent_frozen",
        "timestamp": "2026-03-11T09:58:00",
        "unix_time": now - 120,
        "pid": 1003,
        "status": "idle",
        "working_dir": "/tmp/frozen",
        "last_tool": None,
        "context_tokens": 80000,
    }

    for data in (healthy, warning, frozen):
        path = hb_dir / f"{data['session_id']}.heartbeat"
        path.write_text(json.dumps(data, indent=2))

    return tmp_path  # parent dir so HeartbeatManager gets health_dir=tmp_path


@pytest.fixture
def mock_config() -> TrouterConfig:
    """Return a TrouterConfig populated with test defaults."""
    return TrouterConfig(
        dispatch_mode="local",
        enabled=True,
        model_override="",
        default_model="composer-1.5",
        allowed_models=["composer-1.5", "gpt-5.3-codex"],
        composer_only=False,
        composer_augmented=True,
        credit_target_monthly=100,
        locked=False,
    )


@pytest.fixture
def fake_session_data() -> dict:
    """Return sample session stats JSON matching the dashboard schema."""
    return {
        "total_sessions": 3,
        "healthy": 1,
        "warning": 1,
        "frozen": 1,
        "sessions": [
            {
                "session_id": "agent_healthy",
                "age_seconds": 2.0,
                "status": "active",
                "pid": 1001,
                "health": "healthy",
            },
            {
                "session_id": "agent_warning",
                "age_seconds": 45.0,
                "status": "active",
                "pid": 1002,
                "health": "warning",
            },
            {
                "session_id": "agent_frozen",
                "age_seconds": 120.0,
                "status": "idle",
                "pid": 1003,
                "health": "frozen",
            },
        ],
    }
