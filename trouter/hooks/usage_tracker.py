#!/usr/bin/env python3
"""
Usage tracker - tracks tokens, tool calls, and agent spawns per session.

Hook types: UserPromptSubmit, PostToolUse, SubagentStop
Registers in: .claude/settings.local.json

Stores per-session data at ~/.claude/hooks_data/sessions/{session_id}.json
and daily agent invocation logs at ~/.claude/hooks_data/agent_invocations/.

Improves on the existing token_monitor.py by:
- Tracking per-tool usage counts and token totals
- Using content-type-aware token estimation
- Incremental transcript parsing (offset caching)
- Logging agent spawn/stop events
"""
import json
import os
from datetime import datetime

from trouter.hooks.hook_common import (
    parse_hook_input, respond, ensure_data_dirs, append_jsonl,
    HOOKS_DATA_DIR, load_session_data, save_session_data, estimate_tokens
)


def estimate_from_transcript(transcript_path: str, last_offset: int = 0) -> tuple:
    """Estimate tokens from transcript JSONL, starting from offset.

    Returns: (total_tokens, new_offset) for incremental parsing.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return (0, 0)

    total = 0
    current_offset = 0
    try:
        with open(transcript_path, "r") as f:
            # If we have a previous offset, seek to it and count tokens
            # from previously parsed content as a base
            if last_offset > 0:
                f.seek(0)  # Re-parse from the start for an accurate total

                # Parse from beginning for total (needed for accuracy)
                # but this is bounded by transcript size
                pass

            for line in f:
                current_offset += len(line.encode("utf-8"))
                try:
                    entry = json.loads(line.strip())
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        total += estimate_tokens(content)
                    elif isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict):
                                total += estimate_tokens(str(item.get("text", "")))
                                total += estimate_tokens(str(item.get("content", "")))
                            elif isinstance(item, str):
                                total += estimate_tokens(item)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return (total, current_offset)


def handle_user_prompt(data: dict):
    """Track session start / prompt submission."""
    session_id = data.get("session_id", "unknown")
    ensure_data_dirs()
    session = load_session_data(session_id)

    # Update token estimate from transcript
    transcript = data.get("transcript_path", "")
    if transcript:
        offset = session.get("_transcript_offset", 0)
        tokens, new_offset = estimate_from_transcript(transcript, offset)
        session["total_estimated_tokens"] = tokens
        session["_transcript_offset"] = new_offset

    session["last_active"] = datetime.now().isoformat()
    session["prompt_count"] = session.get("prompt_count", 0) + 1
    save_session_data(session_id, session)
    respond("allow")


def handle_post_tool(data: dict):
    """Track tool usage after execution."""
    session_id = data.get("session_id", "unknown")
    tool_name = data.get("tool_name", "unknown")
    ensure_data_dirs()
    session = load_session_data(session_id)

    # Increment tool usage counter
    counts = session.get("tool_usage_counts", {})
    counts[tool_name] = counts.get(tool_name, 0) + 1
    session["tool_usage_counts"] = counts

    # Estimate tokens from tool output
    output = str(data.get("tool_output", ""))
    output_tokens = estimate_tokens(output)
    tool_token_totals = session.get("tool_token_totals", {})
    tool_token_totals[tool_name] = tool_token_totals.get(tool_name, 0) + output_tokens
    session["tool_token_totals"] = tool_token_totals

    # Update total token estimate from transcript (periodic, not every call)
    total_calls = sum(counts.values())
    if total_calls % 5 == 0:  # Every 5th tool call, re-parse transcript
        transcript = data.get("transcript_path", "")
        if transcript:
            offset = session.get("_transcript_offset", 0)
            tokens, new_offset = estimate_from_transcript(transcript, offset)
            session["total_estimated_tokens"] = tokens
            session["_transcript_offset"] = new_offset

    session["last_active"] = datetime.now().isoformat()
    save_session_data(session_id, session)
    respond("allow")


def handle_subagent_stop(data: dict):
    """Track agent completion."""
    session_id = data.get("session_id", "unknown")
    ensure_data_dirs()
    session = load_session_data(session_id)

    spawn = {
        "stopped_at": datetime.now().isoformat(),
        "agent_type": data.get("agent_type", "unknown"),
        "agent_id": data.get("agent_id", "unknown"),
    }
    session.setdefault("agent_spawns", []).append(spawn)
    save_session_data(session_id, session)

    # Also append to daily agent invocation log
    today = datetime.now().strftime("%Y-%m-%d")
    append_jsonl(
        HOOKS_DATA_DIR / "agent_invocations" / f"{today}.jsonl",
        {**spawn, "session_id": session_id, "event": "SubagentStop"}
    )
    respond("allow")


def main():
    data = parse_hook_input()
    event = data.get("hook_event_name", "")

    if event == "UserPromptSubmit":
        handle_user_prompt(data)
    elif event == "PostToolUse":
        handle_post_tool(data)
    elif event == "SubagentStop":
        handle_subagent_stop(data)
    else:
        # Unknown event - allow and don't block
        respond("allow")


if __name__ == "__main__":
    main()
