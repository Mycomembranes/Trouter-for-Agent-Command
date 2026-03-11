#!/usr/bin/env python3
"""Heartbeat Writer. PostToolUse/UserPromptSubmit. Env: CLAUDE_SESSION_ID, CLAUDE_PROJECT_HASH, WATCHDOG_HEALTH_DIR, ITERM_WINDOW_*."""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

try:
    from trouter.health.heartbeat import HeartbeatManager, get_session_id
except ImportError:
    import time
    from datetime import datetime

    def get_session_id():
        session_id = os.environ.get('CLAUDE_SESSION_ID')
        if session_id:
            return session_id
        project_hash = os.environ.get('CLAUDE_PROJECT_HASH')
        if project_hash:
            return f"project_{project_hash[:12]}"
        return f"pid_{os.getpid()}"

    class HeartbeatManager:
        def __init__(self, health_dir=None):
            self.health_dir = health_dir or Path.home() / ".claude" / "terminal_health"
            self.heartbeats_dir = self.health_dir / "heartbeats"
            self.heartbeats_dir.mkdir(parents=True, exist_ok=True)

        def write_heartbeat(
            self,
            session_id,
            status="active",
            pid=None,
            working_dir=None,
            last_tool=None,
            context_tokens=None,
            window_number=None,
            window_name=None,
            state=None,
            context_pct=None,
        ):
            safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in session_id)
            path = self.heartbeats_dir / f"{safe_id}.heartbeat"
            data = {
                'session_id': session_id,
                'timestamp': datetime.now().isoformat(),
                'unix_time': time.time(),
                'pid': pid or os.getpid(),
                'status': status,
                'working_dir': working_dir or os.getcwd(),
                'last_tool': last_tool,
                'context_tokens': context_tokens,
                'window_number': window_number,
                'window_name': window_name,
                'state': state,
                'context_pct': context_pct,
            }
            path.write_text(json.dumps(data, indent=2))
            return data

logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger(__name__)

def get_window_info() -> tuple:
    """Return (window_number, window_name)."""
    win_num = int(n) if (n := os.environ.get('ITERM_WINDOW_NUMBER')) and n.isdigit() else None
    win_name = os.environ.get('ITERM_WINDOW_NAME') or os.environ.get('ITERM_SESSION_NAME')
    if win_num is not None or win_name:
        return (win_num, win_name)
    try:
        from CLI.mcp.iterm.client import ItermController
        windows = ItermController().list_windows()
        if len(windows) == 1:
            w = windows[0]
            return (w.window_number, w.window_name)
    except Exception as e:
        logger.debug(f"Could not get iTerm window info: {e}")
    return (None, None)

def detect_state(
    hook_type: str = None,
    last_tool: str = None,
    tool_output: str = None,
    explicit_state: str = None,
) -> str:
    """Returns: idle, busy, plan_mode, compact_mode, frozen."""
    if explicit_state and explicit_state in ('idle', 'busy', 'plan_mode', 'compact_mode', 'frozen'):
        return explicit_state
    output_lower = (tool_output or '').lower()
    if 'plan mode' in output_lower or 'plan_mode' in output_lower:
        return 'plan_mode'
    if 'compact mode' in output_lower or 'compact_mode' in output_lower or '/compact' in output_lower:
        return 'compact_mode'
    if hook_type == 'PostToolUse' and last_tool:
        return 'busy'
    if hook_type == 'UserPromptSubmit':
        return 'busy'
    return 'idle'

def parse_context_pct(text: str) -> Optional[int]:
    """Parse context % from output. Returns 0-100 or None."""
    if not text:
        return None
    for pat in [
        r'[Cc]ontext\s+left[^:]*:\s*(\d+)\s*%',
        r'[Cc]ontext[^:]*:\s*(\d+)\s*%',
        r'context_pct[^:]*:\s*(\d+)',
        r'(\d+)\s*%\s*context',
    ]:
        m = re.search(pat, text)
        if m:
            pct = int(m.group(1))
            if 0 <= pct <= 100:
                return pct
    return None

def parse_hook_input() -> dict:
    """Parse JSON from stdin."""
    try:
        if not sys.stdin.isatty() and (d := sys.stdin.read().strip()):
            return json.loads(d)
    except Exception:
        pass
    return {}

def write_heartbeat(
    tool_name: str = None,
    context_tokens: int = None,
    status: str = "active",
    state: str = None,
    context_pct: int = None,
    hook_type: str = None,
    tool_output: str = None,
    hook_data: dict = None,
) -> bool:
    """Write heartbeat for current session. Returns True on success."""
    try:
        h = os.environ.get('WATCHDOG_HEALTH_DIR')
        health_dir = Path(h) if h else None
        manager = HeartbeatManager(health_dir=health_dir)
        session_id = get_session_id()
        window_number, window_name = get_window_info()
        hd = hook_data or {}
        ht = hook_type or hd.get('hook_type')
        to = tool_output or hd.get('tool_output', '')
        lt = tool_name or hd.get('tool_name')
        resolved_state = detect_state(hook_type=ht, last_tool=lt, tool_output=to, explicit_state=state)
        resolved_context_pct = context_pct if context_pct is not None else (parse_context_pct(to) if to else None)
        manager.write_heartbeat(
            session_id=session_id,
            status=status,
            last_tool=lt,
            context_tokens=context_tokens or hd.get('context_tokens'),
            window_number=window_number,
            window_name=window_name,
            state=resolved_state,
            context_pct=resolved_context_pct,
        )
        logger.debug(f"Heartbeat written for {session_id} state={resolved_state}")
        return True
    except Exception as e:
        logger.warning(f"Failed to write heartbeat: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Write heartbeat for terminal watchdog')
    parser.add_argument('--tool', '-t', help='Last tool name')
    parser.add_argument('--tokens', '-n', type=int, help='Context token count')
    parser.add_argument('--status', '-s', default='active', help='Session status (legacy)')
    parser.add_argument('--state', help='Session state: idle|busy|plan_mode|compact_mode|frozen')
    parser.add_argument('--context-pct', type=int, help='Context usage percentage (0-100)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    hook_data = parse_hook_input()
    success = write_heartbeat(
        tool_name=args.tool or hook_data.get('tool_name'),
        context_tokens=args.tokens or hook_data.get('context_tokens'),
        status=args.status,
        state=args.state,
        context_pct=args.context_pct,
        hook_type=hook_data.get('hook_type'),
        tool_output=hook_data.get('tool_output'),
        hook_data=hook_data,
    )
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
