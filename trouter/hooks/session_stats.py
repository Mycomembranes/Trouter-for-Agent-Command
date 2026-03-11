"""Helpers for reading per-session hook usage data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class SessionUsage:
    """Normalized usage data for a single session."""

    session_id: str
    tokens_in: int
    tokens_out: int
    tool_calls: int
    duration_seconds: int


def _parse_iso_datetime(value: object) -> datetime | None:
    """Best-effort ISO timestamp parsing."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def normalize_session_usage(session_id: str, data: dict) -> SessionUsage:
    """Normalize legacy and current hook session schemas into one record."""
    tool_usage_counts = data.get("tool_usage_counts") or {}
    tool_token_totals = data.get("tool_token_totals") or {}

    tool_calls = int(
        data.get("tool_calls")
        or sum(int(count or 0) for count in tool_usage_counts.values())
    )
    tokens_in = int(data.get("tokens_in") or data.get("total_estimated_tokens") or 0)
    tokens_out = int(
        data.get("tokens_out")
        or sum(int(total or 0) for total in tool_token_totals.values())
    )

    started_at = _parse_iso_datetime(data.get("started_at"))
    last_active = _parse_iso_datetime(data.get("last_active")) or started_at
    duration_seconds = int(data.get("duration_s") or 0)
    if duration_seconds <= 0 and started_at and last_active:
        duration_seconds = max(0, int((last_active - started_at).total_seconds()))

    return SessionUsage(
        session_id=session_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tool_calls=tool_calls,
        duration_seconds=duration_seconds,
    )


def load_session_usage(session_dir: Path, limit: int | None = None) -> list[SessionUsage]:
    """Load normalized usage records from a hook sessions directory."""
    if not session_dir.exists() or not session_dir.is_dir():
        return []

    records: list[SessionUsage] = []
    files = sorted(session_dir.glob("*.json"), reverse=True)
    if limit is not None:
        files = files[:limit]

    for path in files:
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        records.append(normalize_session_usage(path.stem, data))

    return records


def summarize_session_usage(records: list[SessionUsage]) -> dict[str, int]:
    """Aggregate normalized usage records for dashboard/sidebar totals."""
    return {
        "sessions": len(records),
        "tokens_in": sum(record.tokens_in for record in records),
        "tokens_out": sum(record.tokens_out for record in records),
        "tool_calls": sum(record.tool_calls for record in records),
    }
