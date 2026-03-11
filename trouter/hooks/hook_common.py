#!/usr/bin/env python3
"""
Shared utilities for Claude Code hooks.

Provides standardized I/O (stdin JSON parsing, stdout response),
data directory management, session state persistence, and JSONL logging.

All hooks in CLI/lib/hooks/ should import from this module rather than
reimplementing stdin/stdout patterns.
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HOOKS_DATA_DIR = Path.home() / ".claude" / "hooks_data"


def parse_hook_input() -> dict:
    """Read and parse JSON from stdin (Claude Code hook input).

    Claude Code sends hook data as a JSON object on stdin with fields like:
      session_id, transcript_path, cwd, hook_event_name,
      tool_name, tool_input (PreToolUse/PostToolUse),
      tool_output (PostToolUse), prompt (UserPromptSubmit).

    Returns empty dict on any parse failure (hooks must be resilient).
    """
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read().strip()
            if raw:
                return json.loads(raw)
    except Exception:
        pass
    return {}


def respond(result: str, message: str = "") -> None:
    """Write hook response JSON to stdout and exit.

    Args:
        result: "allow", "warn", or "block"
        message: Optional message shown to Claude (for warn/block)
    """
    out = {"result": result}
    if message:
        out["message"] = message
    print(json.dumps(out))
    sys.exit(0)


def ensure_data_dirs():
    """Create hooks_data subdirectories if missing."""
    for sub in ("sessions", "daily", "agent_invocations", "blocked"):
        (HOOKS_DATA_DIR / sub).mkdir(parents=True, exist_ok=True)


def append_jsonl(filepath: Path, record: dict):
    """Append a JSON record to a JSONL file (atomic per-line)."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _safe_session_id(session_id: str) -> str:
    """Sanitize session_id for use as a filename."""
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in session_id)


def get_session_path(session_id: str) -> Path:
    """Return path to session usage JSON file."""
    return HOOKS_DATA_DIR / "sessions" / f"{_safe_session_id(session_id)}.json"


def load_session_data(session_id: str) -> dict:
    """Load or initialize session usage data."""
    path = get_session_path(session_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "session_id": session_id,
        "started_at": datetime.now().isoformat(),
        "total_estimated_tokens": 0,
        "tool_usage_counts": {},
        "tool_token_totals": {},
        "agent_spawns": [],
        "warnings_issued": 0,
        "blocks_issued": 0,
        "prompt_count": 0,
    }


def save_session_data(session_id: str, data: dict):
    """Persist session usage data atomically (write-then-rename)."""
    path = get_session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to temp file then rename
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.rename(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def estimate_tokens(text: str) -> int:
    """Estimate token count from text using char-ratio heuristics.

    JSON-heavy content uses ~3 chars/token; general text ~4 chars/token.
    """
    if not text:
        return 0
    json_chars = text.count("{") + text.count("}") + text.count('"')
    json_ratio = json_chars / max(len(text), 1)
    divisor = 3 if json_ratio > 0.1 else 4
    return len(text) // divisor
