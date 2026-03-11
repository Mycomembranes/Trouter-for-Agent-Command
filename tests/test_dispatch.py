"""Tests for trouter.core.dispatch."""

import json
from pathlib import Path


from trouter.core.dispatch import (
    DISPATCH_NATIVE,
    HealthStatus,
    invalidate_dispatch_mode_cache,
    make_clean_env,
    map_cursor_model_to_claude,
    read_dispatch_mode,
)


class TestHealthStatus:
    """HealthStatus constant sanity checks."""

    def test_health_status_creation(self):
        assert HealthStatus.HEALTHY == "healthy"
        assert HealthStatus.WARNING == "warning"
        assert HealthStatus.FROZEN == "frozen"
        assert HealthStatus.COMPLETED == "completed"
        assert HealthStatus.ALERT == "alert"
        assert HealthStatus.EXITED == "exited"
        assert HealthStatus.DEGRADED == "degraded"
        assert HealthStatus.STALE == "stale"
        assert HealthStatus.RUNNING == "running"
        assert HealthStatus.RECENT_EXIT == "recent_exit"
        assert HealthStatus.STOPPED == "stopped"


class TestMapCursorModelToClaude:
    """Verify cursor model -> claude flag mapping."""

    def test_composer_maps_to_sonnet(self):
        assert map_cursor_model_to_claude("composer-1.5") == ["--model", "sonnet"]

    def test_codex_low_maps_to_haiku(self):
        assert map_cursor_model_to_claude("gpt-5.3-codex-low") == ["--model", "haiku"]
        assert map_cursor_model_to_claude("gpt-5.3-codex-low-fast") == ["--model", "haiku"]

    def test_codex_high_maps_to_sonnet(self):
        assert map_cursor_model_to_claude("gpt-5.3-codex-high") == ["--model", "sonnet"]
        assert map_cursor_model_to_claude("gpt-5.3-codex-high-fast") == ["--model", "sonnet"]
        assert map_cursor_model_to_claude("gpt-5.3-codex") == ["--model", "sonnet"]
        assert map_cursor_model_to_claude("gpt-5.3-codex-fast") == ["--model", "sonnet"]

    def test_codex_xhigh_maps_to_opus(self):
        assert map_cursor_model_to_claude("gpt-5.3-codex-xhigh") == ["--model", "opus"]
        assert map_cursor_model_to_claude("gpt-5.3-codex-xhigh-fast") == ["--model", "opus"]

    def test_unknown_model_returns_empty(self):
        assert map_cursor_model_to_claude("unknown-model") == []
        assert map_cursor_model_to_claude("") == []


class TestMakeCleanEnv:
    """Verify environment sanitisation."""

    def test_make_clean_env(self, monkeypatch):
        monkeypatch.setenv("CLAUDECODE", "secret_token")
        monkeypatch.setenv("CURSOR_API_KEY", "api_key_123")
        monkeypatch.setenv("PATH", "/usr/bin")

        env = make_clean_env()

        # Sensitive vars must be stripped
        assert "CLAUDECODE" not in env
        assert "CURSOR_API_KEY" not in env

        # PATH must be normalised with .local/bin prepended
        local_bin = str(Path.home() / ".local/bin")
        assert env["PATH"].startswith(local_bin)
        # Original PATH preserved at end
        assert "/usr/bin" in env["PATH"]

    def test_make_clean_env_missing_vars(self, monkeypatch):
        """Should not error when env vars are absent."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CURSOR_API_KEY", raising=False)

        env = make_clean_env()
        assert "CLAUDECODE" not in env
        assert "CURSOR_API_KEY" not in env
        assert "PATH" in env


class TestReadDispatchMode:
    """Verify dispatch_mode config reading with filesystem isolation."""

    def setup_method(self):
        invalidate_dispatch_mode_cache()

    def test_read_dispatch_mode_default(self):
        """Empty config_path returns the native default."""
        assert read_dispatch_mode("") == DISPATCH_NATIVE

    def test_read_dispatch_mode_from_file(self, tmp_path):
        cfg = tmp_path / "cursor_config.json"
        cfg.write_text(json.dumps({"dispatch_mode": "local"}))

        result = read_dispatch_mode(str(cfg))
        assert result == "local"

    def test_read_dispatch_mode_missing_file(self, tmp_path):
        """Non-existent file returns native default."""
        result = read_dispatch_mode(str(tmp_path / "nonexistent.json"))
        assert result == DISPATCH_NATIVE

    def test_read_dispatch_mode_invalid_json(self, tmp_path):
        cfg = tmp_path / "bad.json"
        cfg.write_text("{not valid json")

        result = read_dispatch_mode(str(cfg))
        assert result == DISPATCH_NATIVE

    def test_read_dispatch_mode_missing_key(self, tmp_path):
        """Config file without dispatch_mode key returns native default."""
        cfg = tmp_path / "cursor_config.json"
        cfg.write_text(json.dumps({"enabled": True}))

        result = read_dispatch_mode(str(cfg))
        assert result == DISPATCH_NATIVE
