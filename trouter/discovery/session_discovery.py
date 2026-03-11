#!/usr/bin/env python3
"""
Session Discovery Protocol
==========================

Enables trouter and other tools to reliably discover and identify Claude Code
sessions via heartbeat files in ~/.claude/terminal_health/.

Protocol:
1. Heartbeat files in ~/.claude/terminal_health/heartbeats/ include session metadata
2. Optional session_*.json files in health dir provide richer metadata
3. Discovery returns ClaudeSession with session ID, window number, state, context level
4. trouter uses discovery before attempting cross-session control

References:
- doc/PROPOSED_SOLUTIONS.md (Issue 2: trouter Session Access Failures)
- CLI/lib/watchdog/heartbeat.py (HeartbeatManager, HeartbeatData)
- CLI/mcp/iterm/client.py (ItermController, parse_stats for mode detection)
"""

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Union

# Default health directory
DEFAULT_HEALTH_DIR = Path.home() / ".claude" / "terminal_health"


class SessionState(str, Enum):
    """Claude session state as detected from heartbeat and screen content."""

    IDLE = "idle"
    BUSY = "busy"
    PLAN_MODE = "plan_mode"
    COMPACT_MODE = "compact_mode"
    FROZEN = "frozen"
    UNKNOWN = "unknown"


# Responsiveness threshold: heartbeat older than this is considered non-responsive
DEFAULT_RESPONSIVE_THRESHOLD_SECONDS = 120.0


@dataclass
class ClaudeSession:
    """
    Claude Code session info discovered from heartbeat files.

    Attributes:
        session_id: Unique session identifier (e.g., iterm_win1, project_abc123).
        window_number: iTerm window number (1-indexed), derived from session_id or metadata.
        window_name: Display name of the iTerm window.
        tty: Terminal device path (e.g., /dev/ttys001).
        state: Session state: idle, busy, plan_mode, compact_mode, or frozen.
        context_pct: Context usage percentage (0-100), if available.
        last_heartbeat: Unix timestamp of last heartbeat, or None.
        is_responsive: True if heartbeat is recent (within threshold).
        working_dir: Session working directory, if available.
        pid: Process ID of the session, if available.
        raw_status: Original status string from heartbeat (active, idle, completing).
    """

    session_id: str
    window_number: int
    window_name: str = ""
    tty: str = ""
    state: str = SessionState.UNKNOWN.value
    context_pct: Optional[int] = None
    last_heartbeat: Optional[float] = None
    is_responsive: bool = True
    working_dir: Optional[str] = None
    pid: Optional[int] = None
    raw_status: str = ""

    def __post_init__(self) -> None:
        """Ensure state is a valid string."""
        if isinstance(self.state, SessionState):
            self.state = self.state.value

    def heartbeat_age_seconds(self) -> float:
        """
        Get age of last heartbeat in seconds.

        Returns:
            Seconds since last heartbeat, or float('inf') if no heartbeat.
        """
        if self.last_heartbeat is None:
            return float("inf")
        return time.time() - self.last_heartbeat


class SessionDiscovery:
    """
    Discover and identify Claude Code sessions via heartbeat files.

    Sessions are discovered by reading:
    1. heartbeat files in ~/.claude/terminal_health/heartbeats/*.heartbeat
    2. Optional session_*.json files in ~/.claude/terminal_health/ (richer metadata)
    3. Optional status/iterm_monitor.json (window name, tty from iTerm monitor)

    Example::

        >>> discovery = SessionDiscovery()
        >>> sessions = discovery.discover_sessions()
        >>> for s in sessions:
        ...     print(f"{s.window_name}: {s.state}, responsive={s.is_responsive}")
        >>> session = discovery.find_session_by_name("GPU Setup")
    """

    def __init__(
        self,
        health_dir: Optional[Union[Path, str]] = None,
        responsive_threshold_seconds: float = DEFAULT_RESPONSIVE_THRESHOLD_SECONDS,
        iterm_controller: Optional[object] = None,  # Ignored; kept for API compatibility
    ) -> None:
        """
        Initialize session discovery.

        Args:
            health_dir: Base directory for health data. Defaults to ~/.claude/terminal_health.
            responsive_threshold_seconds: Heartbeat age (seconds) beyond which a session
                is considered non-responsive (frozen). Default: 120.
            iterm_controller: Unused; kept for backward compatibility with callers that pass it.
        """
        self.health_dir = Path(health_dir) if health_dir else DEFAULT_HEALTH_DIR
        self.heartbeats_dir = self.health_dir / "heartbeats"
        self.responsive_threshold = responsive_threshold_seconds
        self._status_path = self.health_dir / "status" / "iterm_monitor.json"

    def discover_sessions(self) -> List[ClaudeSession]:
        """
        Find all active Claude sessions via heartbeat files.

        Reads heartbeats from heartbeats/*.heartbeat and optionally merges with
        session_*.json and iterm_monitor status for richer metadata.

        Returns:
            List of ClaudeSession objects, sorted by window_number.
        """
        sessions_dict: Dict[str, ClaudeSession] = {}

        # 1. Read heartbeat files (primary source)
        self._discover_from_heartbeats(sessions_dict)

        # 2. Merge with session_*.json if present (richer metadata)
        self._merge_from_session_json(sessions_dict)

        # 3. Enrich from iterm_monitor status
        self._enrich_from_iterm_status(sessions_dict)

        # 4. Ensure all sessions have valid state
        for sess in sessions_dict.values():
            if not sess.state or sess.state == SessionState.UNKNOWN.value:
                sess.state = self._infer_state(sess)

        return sorted(sessions_dict.values(), key=lambda s: s.window_number)

    def _discover_from_heartbeats(
        self, sessions_dict: Dict[str, ClaudeSession]
    ) -> None:
        """Populate sessions from heartbeats/*.heartbeat files."""
        self.heartbeats_dir.mkdir(parents=True, exist_ok=True)

        for hb_path in self.heartbeats_dir.glob("*.heartbeat"):
            try:
                with open(hb_path, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            session_id = data.get("session_id") or hb_path.stem
            unix_time = data.get("unix_time") or data.get("timestamp")
            if isinstance(unix_time, str):
                unix_time = 0.0  # Cannot parse ISO string without dateutil
            elif unix_time is None:
                unix_time = 0.0

            window_number = self._derive_window_number(session_id, data)
            raw_status = data.get("status", "active")

            age = time.time() - unix_time if unix_time else float("inf")
            is_responsive = age < self.responsive_threshold

            state = self._map_status_to_state(raw_status, data)
            if age >= self.responsive_threshold:
                state = SessionState.FROZEN.value

            session = ClaudeSession(
                session_id=session_id,
                window_number=window_number,
                window_name=data.get("window_name", ""),
                tty=data.get("tty", ""),
                state=state,
                context_pct=data.get("context_pct") or data.get("context_left"),
                last_heartbeat=unix_time if unix_time else None,
                is_responsive=is_responsive,
                working_dir=data.get("working_dir"),
                pid=data.get("pid"),
                raw_status=raw_status,
            )
            sessions_dict[session_id] = session

    def _merge_from_session_json(
        self, sessions_dict: Dict[str, ClaudeSession]
    ) -> None:
        """Merge richer metadata from session_*.json files."""
        for json_path in self.health_dir.glob("session_*.json"):
            try:
                with open(json_path, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            session_id = data.get("session_id")
            if not session_id:
                continue

            if session_id in sessions_dict:
                sess = sessions_dict[session_id]
                sess.window_number = data.get("window_number", sess.window_number)
                sess.window_name = data.get("window_name", sess.window_name)
                sess.tty = data.get("tty", sess.tty)
                sess.state = data.get("state", sess.state)
                sess.context_pct = data.get("context_pct", sess.context_pct)
                ts = data.get("timestamp", data.get("last_heartbeat"))
                if ts is not None:
                    sess.last_heartbeat = float(ts)
                    sess.is_responsive = (
                        time.time() - sess.last_heartbeat
                    ) < self.responsive_threshold
            else:
                # New session from json-only
                ts = data.get("timestamp", data.get("last_heartbeat")) or 0.0
                age = time.time() - ts if ts else float("inf")
                sessions_dict[session_id] = ClaudeSession(
                    session_id=session_id,
                    window_number=data.get("window_number", 0),
                    window_name=data.get("window_name", ""),
                    tty=data.get("tty", ""),
                    state=data.get("state", SessionState.UNKNOWN.value),
                    context_pct=data.get("context_pct"),
                    last_heartbeat=ts if ts else None,
                    is_responsive=age < self.responsive_threshold,
                )

    def _enrich_from_iterm_status(
        self, sessions_dict: Dict[str, ClaudeSession]
    ) -> None:
        """Enrich sessions with window_name, tty from iterm_monitor status."""
        try:
            if not self._status_path.exists():
                return
            data = json.loads(self._status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        windows = data.get("windows", [])
        for win in windows:
            session_id = win.get("session_id") or f"iterm_win{win.get('window', 0)}"
            if session_id in sessions_dict:
                sess = sessions_dict[session_id]
                sess.window_name = win.get("name", sess.window_name)
                sess.tty = win.get("tty", sess.tty)
                sess.window_number = win.get("window", sess.window_number)
                ctx = win.get("context_left")
                if ctx is not None and sess.context_pct is None:
                    sess.context_pct = ctx
                mode = win.get("mode", "").lower()
                if mode == "plan" and sess.state != SessionState.FROZEN.value:
                    sess.state = SessionState.PLAN_MODE.value
            elif session_id not in sessions_dict:
                sess = ClaudeSession(
                    session_id=session_id,
                    window_number=win.get("window", 0),
                    window_name=win.get("name", ""),
                    tty=win.get("tty", ""),
                    state=SessionState.UNKNOWN.value,
                    context_pct=win.get("context_left"),
                    last_heartbeat=None,
                    is_responsive=False,
                )
                sessions_dict[session_id] = sess

    def _derive_window_number(self, session_id: str, data: dict) -> int:
        """Derive window number from session_id or data."""
        wn = data.get("window_number")
        if wn is not None:
            return int(wn)
        match = re.search(r"iterm_win(\d+)", session_id, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r"win(?:dow)?[-_]?(\d+)", session_id, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return 0

    def _map_status_to_state(self, raw_status: str, data: dict) -> str:
        """Map raw heartbeat status and metadata to SessionState.

        Prefers explicit 'state' field from enhanced heartbeat (heartbeat_writer).
        Falls back to status/mode when state not present.
        """
        # Use explicit state from enhanced heartbeat metadata when present
        explicit_state = (data.get("state") or "").lower()
        if explicit_state in (
            SessionState.IDLE.value,
            SessionState.BUSY.value,
            SessionState.PLAN_MODE.value,
            SessionState.COMPACT_MODE.value,
            SessionState.FROZEN.value,
        ):
            return explicit_state

        raw_lower = (raw_status or "").lower()
        mode = (data.get("mode") or "").lower()

        if mode == "plan":
            return SessionState.PLAN_MODE.value
        if "compact" in raw_lower or "compact" in mode:
            return SessionState.COMPACT_MODE.value
        if raw_lower in ("idle", "waiting"):
            return SessionState.IDLE.value
        if raw_lower in ("active", "busy", "running", "completing", "edit_review"):
            return SessionState.BUSY.value

        return (
            SessionState.IDLE.value if raw_lower == "idle" else SessionState.BUSY.value
        )

    def _infer_state(self, session: ClaudeSession) -> str:
        """Infer state when not explicitly set."""
        if not session.is_responsive:
            return SessionState.FROZEN.value
        raw_lower = (session.raw_status or "").lower()
        if raw_lower == "idle":
            return SessionState.IDLE.value
        return SessionState.BUSY.value

    def _check_responsive(self, last_heartbeat: Optional[float]) -> bool:
        """
        Check if session is responsive based on heartbeat age.

        Args:
            last_heartbeat: Unix timestamp of last heartbeat, or None.

        Returns:
            True if heartbeat is within responsive_threshold.
        """
        if last_heartbeat is None:
            return False
        return (time.time() - last_heartbeat) < self.responsive_threshold

    def find_session_by_name(
        self,
        name: Union[str, Pattern[str]],
        case_sensitive: bool = False,
    ) -> Optional[ClaudeSession]:
        """
        Find session by window name pattern matching.

        Args:
            name: String to match (substring) or compiled regex pattern.
            case_sensitive: If True, match case-sensitively (ignored when name is regex).

        Returns:
            First matching ClaudeSession, or None if not found.
        """
        sessions = self.discover_sessions()

        if isinstance(name, re.Pattern):
            for sess in sessions:
                if name.search(sess.window_name or ""):
                    return sess
            for sess in sessions:
                if name.search(sess.session_id):
                    return sess
            return None

        name_str = str(name)
        if not case_sensitive:
            name_str = name_str.lower()

        for sess in sessions:
            target = sess.window_name or sess.session_id
            if not case_sensitive:
                target = target.lower()
            if name_str in target:
                return sess
        return None

    def find_session_by_window(self, window_number: int) -> Optional[ClaudeSession]:
        """
        Find session by iTerm window number.

        Args:
            window_number: 1-indexed iTerm window number.

        Returns:
            Matching ClaudeSession, or None.
        """
        for sess in self.discover_sessions():
            if sess.window_number == window_number:
                return sess
        return None

    def get_responsive_sessions(self) -> List[ClaudeSession]:
        """Return only sessions that are responsive (heartbeat within threshold)."""
        return [s for s in self.discover_sessions() if s.is_responsive]

    def get_frozen_sessions(self) -> List[ClaudeSession]:
        """Return only sessions considered frozen (stale heartbeat)."""
        return [s for s in self.discover_sessions() if not s.is_responsive]


# =============================================================================
# CLI entry point
# =============================================================================


def _main() -> int:
    """CLI for session discovery."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Discover Claude sessions via heartbeat files",
        epilog="Heartbeats: ~/.claude/terminal_health/heartbeats/",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="list",
        choices=["list", "find", "responsive", "frozen"],
        help="Command: list (default), find, responsive, frozen",
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="For 'find': substring to match in window name",
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        default=DEFAULT_RESPONSIVE_THRESHOLD_SECONDS,
        help=f"Responsive threshold in seconds (default: {DEFAULT_RESPONSIVE_THRESHOLD_SECONDS})",
    )
    args = parser.parse_args()

    discovery = SessionDiscovery(responsive_threshold_seconds=args.threshold)

    if args.command == "list":
        sessions = discovery.discover_sessions()
        if args.json:
            out = [
                {
                    "session_id": s.session_id,
                    "window_number": s.window_number,
                    "window_name": s.window_name,
                    "state": s.state,
                    "context_pct": s.context_pct,
                    "is_responsive": s.is_responsive,
                    "heartbeat_age": s.heartbeat_age_seconds(),
                }
                for s in sessions
            ]
            print(json.dumps(out, indent=2))
        else:
            for s in sessions:
                age = s.heartbeat_age_seconds()
                age_str = f"{age:.0f}s" if age < float("inf") else "never"
                resp = "✓" if s.is_responsive else "✗"
                print(
                    f"  [{resp}] Win {s.window_number}: "
                    f"{s.window_name or s.session_id} ({s.state}) age={age_str}"
                )

    elif args.command == "find":
        if not args.name:
            print("Error: name required for find command", file=sys.stderr)
            return 1
        session = discovery.find_session_by_name(args.name)
        if not session:
            print(f"No session matching '{args.name}'", file=sys.stderr)
            return 1
        if args.json:
            print(
                json.dumps(
                    {
                        "session_id": session.session_id,
                        "window_number": session.window_number,
                        "window_name": session.window_name,
                        "state": session.state,
                        "context_pct": session.context_pct,
                        "is_responsive": session.is_responsive,
                    },
                    indent=2,
                )
            )
        else:
            print(
                f"Win {session.window_number}: "
                f"{session.window_name or session.session_id} ({session.state})"
            )

    elif args.command == "responsive":
        sessions = discovery.get_responsive_sessions()
        if args.json:
            print(json.dumps([s.session_id for s in sessions], indent=2))
        else:
            for s in sessions:
                print(f"  Win {s.window_number}: {s.window_name or s.session_id}")

    elif args.command == "frozen":
        sessions = discovery.get_frozen_sessions()
        if args.json:
            print(json.dumps([s.session_id for s in sessions], indent=2))
        else:
            for s in sessions:
                age = s.heartbeat_age_seconds()
                print(
                    f"  Win {s.window_number}: "
                    f"{s.window_name or s.session_id} (age={age:.0f}s)"
                )

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
