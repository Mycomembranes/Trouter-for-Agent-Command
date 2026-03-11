"""
Heartbeat Manager for Terminal Health Monitoring.

Manages heartbeat files that track session activity. Each Claude Code
session writes periodic heartbeats; stale heartbeats indicate frozen sessions.

Heartbeat files are stored in ~/.claude/terminal_health/heartbeats/
"""

import json
import logging
import os
import time
from dataclasses import dataclass, asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Default health directory
DEFAULT_HEALTH_DIR = Path.home() / ".claude" / "terminal_health"


@dataclass
class HeartbeatData:
    """Data stored in a heartbeat file.

    Enhanced for session discovery (PROPOSED_SOLUTIONS.md):
    - session_id: Unique identifier
    - window_number/window_name: iTerm window info for session targeting
    - state: idle|busy|plan_mode|compact_mode|frozen
    - context_pct: Context usage % (from Claude output when available)
    """
    session_id: str
    timestamp: str
    unix_time: float
    pid: int
    status: str  # active, idle, completing (legacy)
    working_dir: str
    last_tool: Optional[str] = None
    context_tokens: Optional[int] = None
    # Session discovery fields
    window_number: Optional[int] = None
    window_name: Optional[str] = None
    state: Optional[str] = None  # idle, busy, plan_mode, compact_mode, frozen
    context_pct: Optional[int] = None

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> 'HeartbeatData':
        """Deserialize from JSON, ignoring unknown fields."""
        data = json.loads(json_str)
        valid_fields = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_file(cls, path: Path) -> Optional['HeartbeatData']:
        """Load from heartbeat file."""
        try:
            if not path.exists():
                return None
            return cls.from_json(path.read_text())
        except Exception as e:
            logger.warning(f"Failed to read heartbeat {path}: {e}")
            return None

    def age_seconds(self) -> float:
        """Get age of heartbeat in seconds."""
        return time.time() - self.unix_time


class HeartbeatManager:
    """
    Manage heartbeat files for terminal health monitoring.

    Provides methods to write, read, and query heartbeat status
    for Claude Code sessions.

    Example:
        >>> mgr = HeartbeatManager()
        >>> mgr.write_heartbeat("session_123", status="active", pid=12345)
        >>> heartbeat = mgr.get_heartbeat("session_123")
        >>> print(f"Session age: {heartbeat.age_seconds()}s")
    """

    def __init__(self, health_dir: Optional[Path] = None):
        """
        Initialize heartbeat manager.

        Args:
            health_dir: Base directory for health data (default: ~/.claude/terminal_health)
        """
        self.health_dir = health_dir or DEFAULT_HEALTH_DIR
        self.heartbeats_dir = self.health_dir / "heartbeats"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Ensure required directories exist."""
        self.heartbeats_dir.mkdir(parents=True, exist_ok=True)

    def _heartbeat_path(self, session_id: str) -> Path:
        """Get path for a session's heartbeat file."""
        # Sanitize session_id for filename
        safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in session_id)
        # Fall back to "unknown" when sanitization produces an empty string
        # (e.g. the caller passed an empty or all-special-character session_id).
        if not safe_id:
            safe_id = "unknown"
        return self.heartbeats_dir / f"{safe_id}.heartbeat"

    def write_heartbeat(
        self,
        session_id: str,
        status: str = "active",
        pid: Optional[int] = None,
        working_dir: Optional[str] = None,
        last_tool: Optional[str] = None,
        context_tokens: Optional[int] = None,
        window_number: Optional[int] = None,
        window_name: Optional[str] = None,
        state: Optional[str] = None,
        context_pct: Optional[int] = None,
    ) -> HeartbeatData:
        """
        Write a heartbeat for a session.

        Args:
            session_id: Unique session identifier
            status: Session status (active, idle, completing)
            pid: Process ID (default: current process)
            working_dir: Working directory (default: cwd)
            last_tool: Last tool executed
            context_tokens: Current context token count
            window_number: iTerm window number (for session discovery)
            window_name: iTerm window name (for session discovery)
            state: Session state - idle, busy, plan_mode, compact_mode, frozen
            context_pct: Context usage percentage (0-100)

        Returns:
            HeartbeatData that was written
        """
        heartbeat = HeartbeatData(
            session_id=session_id,
            timestamp=datetime.now().isoformat(),
            unix_time=time.time(),
            pid=pid or os.getpid(),
            status=status,
            working_dir=working_dir or os.getcwd(),
            last_tool=last_tool,
            context_tokens=context_tokens,
            window_number=window_number,
            window_name=window_name,
            state=state,
            context_pct=context_pct,
        )

        path = self._heartbeat_path(session_id)
        tmp_path = path.with_suffix('.tmp')
        tmp_path.write_text(heartbeat.to_json())
        tmp_path.rename(path)

        logger.debug(f"Wrote heartbeat for {session_id}: {status}")
        return heartbeat

    def get_heartbeat(self, session_id: str) -> Optional[HeartbeatData]:
        """
        Get heartbeat for a session.

        Args:
            session_id: Session identifier

        Returns:
            HeartbeatData or None if not found
        """
        path = self._heartbeat_path(session_id)
        return HeartbeatData.from_file(path)

    def get_all_heartbeats(self) -> List[HeartbeatData]:
        """
        Get all heartbeats.

        Returns:
            List of HeartbeatData for all sessions
        """
        heartbeats = []

        for path in self.heartbeats_dir.glob("*.heartbeat"):
            heartbeat = HeartbeatData.from_file(path)
            if heartbeat:
                heartbeats.append(heartbeat)

        # Sort by timestamp, newest first
        heartbeats.sort(key=lambda h: h.unix_time, reverse=True)
        return heartbeats

    def get_stale_heartbeats(self, threshold_seconds: float = 60.0) -> List[HeartbeatData]:
        """
        Get heartbeats older than threshold.

        Args:
            threshold_seconds: Age threshold in seconds

        Returns:
            List of stale HeartbeatData
        """
        stale = []

        for heartbeat in self.get_all_heartbeats():
            if heartbeat.age_seconds() > threshold_seconds:
                stale.append(heartbeat)

        return stale

    def remove_heartbeat(self, session_id: str) -> bool:
        """
        Remove a heartbeat file.

        Args:
            session_id: Session identifier

        Returns:
            True if removed, False if not found
        """
        path = self._heartbeat_path(session_id)
        if path.exists():
            path.unlink()
            logger.debug(f"Removed heartbeat for {session_id}")
            return True
        return False

    def cleanup_stale(self, threshold_seconds: float = 300.0) -> int:
        """
        Remove heartbeats older than threshold.

        Args:
            threshold_seconds: Age threshold in seconds

        Returns:
            Number of heartbeats removed
        """
        count = 0

        for heartbeat in self.get_stale_heartbeats(threshold_seconds):
            if self.remove_heartbeat(heartbeat.session_id):
                count += 1
                logger.info(f"Cleaned up stale heartbeat: {heartbeat.session_id}")

        return count

    def get_health_summary(self) -> Dict:
        """
        Get summary of all session health.

        Returns:
            Dictionary with health summary
        """
        heartbeats = self.get_all_heartbeats()

        healthy = []
        warning = []
        frozen = []

        for hb in heartbeats:
            age = hb.age_seconds()
            if age < 30:
                healthy.append(hb)
            elif age < 60:
                warning.append(hb)
            else:
                frozen.append(hb)

        sessions = []
        for hb in heartbeats:
            age = hb.age_seconds()
            sessions.append({
                'session_id': hb.session_id,
                'age_seconds': round(age, 1),
                'status': hb.status,
                'pid': hb.pid,
                'health': 'healthy' if age < 30 else
                         'warning' if age < 60 else 'frozen',
            })

        return {
            'total_sessions': len(heartbeats),
            'healthy': len(healthy),
            'warning': len(warning),
            'frozen': len(frozen),
            'sessions': sessions,
        }


def get_session_id() -> str:
    """
    Get the current session ID.

    Tries multiple sources:
    1. CLAUDE_SESSION_ID environment variable
    2. CLAUDE_PROJECT_HASH environment variable
    3. Generate from PID and timestamp

    Returns:
        Session identifier string
    """
    # Try explicit session ID
    session_id = os.environ.get('CLAUDE_SESSION_ID')
    if session_id:
        return session_id

    # Try project hash
    project_hash = os.environ.get('CLAUDE_PROJECT_HASH')
    if project_hash:
        return f"project_{project_hash[:12]}"

    # Fall back to PID-based
    return f"pid_{os.getpid()}"


if __name__ == '__main__':
    # CLI for testing
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description='Heartbeat manager CLI')
    parser.add_argument('command', choices=['write', 'read', 'list', 'cleanup', 'summary'])
    parser.add_argument('--session', '-s', help='Session ID')
    parser.add_argument('--status', default='active', help='Status for write')
    parser.add_argument('--threshold', type=float, default=60, help='Threshold for stale')
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    mgr = HeartbeatManager()

    if args.command == 'write':
        session = args.session or get_session_id()
        hb = mgr.write_heartbeat(session, status=args.status)
        print(f"Wrote heartbeat: {session}")
        if args.json:
            print(hb.to_json())

    elif args.command == 'read':
        if not args.session:
            print("--session required for read")
            exit(1)
        hb = mgr.get_heartbeat(args.session)
        if hb:
            print(hb.to_json() if args.json else f"Session: {hb.session_id}, Age: {hb.age_seconds():.1f}s")
        else:
            print(f"No heartbeat for {args.session}")

    elif args.command == 'list':
        heartbeats = mgr.get_all_heartbeats()
        if args.json:
            print(json.dumps([asdict(hb) for hb in heartbeats], indent=2))
        else:
            for hb in heartbeats:
                status_icon = '✓' if hb.age_seconds() < 30 else '⚠' if hb.age_seconds() < 60 else '🔴'
                print(f"{status_icon} {hb.session_id}: {hb.age_seconds():.1f}s ago ({hb.status})")

    elif args.command == 'cleanup':
        count = mgr.cleanup_stale(args.threshold)
        print(f"Cleaned up {count} stale heartbeats")

    elif args.command == 'summary':
        summary = mgr.get_health_summary()
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"Total: {summary['total_sessions']}, Healthy: {summary['healthy']}, "
                  f"Warning: {summary['warning']}, Frozen: {summary['frozen']}")
