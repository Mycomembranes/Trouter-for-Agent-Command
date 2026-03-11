"""Tests for normalized hook session usage loading."""

import json

from trouter.hooks.session_stats import (
    load_session_usage,
    normalize_session_usage,
    summarize_session_usage,
)


class TestNormalizeSessionUsage:
    """Session usage normalization across current and legacy schemas."""

    def test_normalizes_current_hook_schema(self):
        record = normalize_session_usage(
            "session-a",
            {
                "started_at": "2026-03-11T10:00:00",
                "last_active": "2026-03-11T10:03:15",
                "total_estimated_tokens": 1200,
                "tool_usage_counts": {"Read": 2, "Bash": 1},
                "tool_token_totals": {"Read": 150, "Bash": 75},
            },
        )

        assert record.session_id == "session-a"
        assert record.tokens_in == 1200
        assert record.tokens_out == 225
        assert record.tool_calls == 3
        assert record.duration_seconds == 195

    def test_preserves_legacy_totals_when_present(self):
        record = normalize_session_usage(
            "session-b",
            {
                "tokens_in": 500,
                "tokens_out": 300,
                "tool_calls": 7,
                "duration_s": 90,
            },
        )

        assert record.tokens_in == 500
        assert record.tokens_out == 300
        assert record.tool_calls == 7
        assert record.duration_seconds == 90


class TestLoadSessionUsage:
    """Filesystem loading and aggregate summaries."""

    def test_loads_and_summarizes_records(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "alpha.json").write_text(
            json.dumps(
                {
                    "started_at": "2026-03-11T10:00:00",
                    "last_active": "2026-03-11T10:01:00",
                    "total_estimated_tokens": 100,
                    "tool_usage_counts": {"Read": 1},
                    "tool_token_totals": {"Read": 20},
                }
            )
        )
        (sessions_dir / "beta.json").write_text(
            json.dumps(
                {
                    "tokens_in": 50,
                    "tokens_out": 15,
                    "tool_calls": 2,
                    "duration_s": 30,
                }
            )
        )

        records = load_session_usage(sessions_dir)
        totals = summarize_session_usage(records)

        assert {record.session_id for record in records} == {"alpha", "beta"}
        assert totals == {
            "sessions": 2,
            "tokens_in": 150,
            "tokens_out": 35,
            "tool_calls": 3,
        }

    def test_ignores_invalid_json(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "bad.json").write_text("{not valid json")

        assert load_session_usage(sessions_dir) == []
