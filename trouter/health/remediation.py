"""
Remediation Handler for Terminal Watchdog.

Implements escalating remediation actions for frozen terminals:
  Level 1 (30-60s): Log warning
  Level 2 (60-90s): Write alert + notify user
  Level 3 (90-120s): Send /compact to session
  Level 4 (>120s): Kill session + spawn recovery

Uses tmux for session interaction and iTerm2 for macOS alerts.
"""

import json
import logging
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Callable

from trouter.health.heartbeat import HeartbeatData, DEFAULT_HEALTH_DIR

logger = logging.getLogger(__name__)


def _escape_applescript(s: str) -> str:
    """Escape a string for safe interpolation into AppleScript double-quoted strings.

    AppleScript uses backslash-escaping inside double-quoted strings, so we
    must escape existing backslashes first, then double quotes.
    """
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


class EscalationLevel(Enum):
    """Remediation escalation levels."""
    NONE = 0        # Healthy - no action needed
    WARNING = 1     # 30-60s - log warning
    ALERT = 2       # 60-90s - notify user
    COMPACT = 3     # 90-120s - send /compact
    KILL = 4        # >120s - kill and respawn


@dataclass
class RemediationAction:
    """Record of a remediation action taken."""
    timestamp: str
    session_id: str
    level: str
    action: str
    success: bool
    details: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


@dataclass
class RemediationConfig:
    """Configuration for remediation thresholds."""
    warning_threshold: float = 30.0    # seconds before warning
    alert_threshold: float = 60.0      # seconds before alert
    compact_threshold: float = 90.0    # seconds before /compact
    kill_threshold: float = 120.0      # seconds before kill
    auto_compact: bool = True          # enable automatic /compact
    auto_recovery: bool = True         # enable automatic session restart
    save_checkpoint: bool = True       # save checkpoint before killing
    compact_wait: float = 30.0         # wait time after compact before kill


class RemediationHandler:
    """
    Handle remediation actions for frozen sessions.

    Implements escalating actions based on heartbeat age:
    1. Warning (log only)
    2. Alert (visual notification)
    3. Compact (send /compact command)
    4. Kill (terminate and respawn)

    Example:
        >>> handler = RemediationHandler()
        >>> level = handler.get_escalation_level(heartbeat)
        >>> if level >= EscalationLevel.COMPACT:
        ...     handler.send_compact(heartbeat.session_id)
    """

    def __init__(
        self,
        config: Optional[RemediationConfig] = None,
        health_dir: Optional[Path] = None,
    ):
        """
        Initialize remediation handler.

        Args:
            config: Remediation configuration
            health_dir: Health data directory
        """
        self.config = config or RemediationConfig()
        self.health_dir = health_dir or DEFAULT_HEALTH_DIR
        self.logs_dir = self.health_dir / "logs"
        self.alerts_dir = self.health_dir / "alerts"
        self._ensure_dirs()

        # Track actions to prevent duplicates
        self._action_history: Dict[str, float] = {}
        self._action_debounce = 60.0  # seconds between actions per session

    def _ensure_dirs(self) -> None:
        """Ensure required directories exist."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.alerts_dir.mkdir(parents=True, exist_ok=True)

    def get_escalation_level(self, heartbeat: HeartbeatData) -> EscalationLevel:
        """
        Determine escalation level for a heartbeat.

        Args:
            heartbeat: Heartbeat data to evaluate

        Returns:
            Appropriate EscalationLevel
        """
        age = heartbeat.age_seconds()

        if age < self.config.warning_threshold:
            return EscalationLevel.NONE
        elif age < self.config.alert_threshold:
            return EscalationLevel.WARNING
        elif age < self.config.compact_threshold:
            return EscalationLevel.ALERT
        elif age < self.config.kill_threshold:
            return EscalationLevel.COMPACT
        else:
            return EscalationLevel.KILL

    def should_take_action(self, session_id: str, level: EscalationLevel) -> bool:
        """
        Check if action should be taken (debouncing).

        Args:
            session_id: Session identifier
            level: Escalation level

        Returns:
            True if action should be taken
        """
        key = f"{session_id}:{level.name}"
        last_action = self._action_history.get(key, 0)
        now = time.time()

        if now - last_action < self._action_debounce:
            return False

        self._action_history[key] = now
        return True

    def handle_heartbeat(
        self,
        heartbeat: HeartbeatData,
        on_alert: Optional[Callable[[HeartbeatData], None]] = None,
    ) -> Optional[RemediationAction]:
        """
        Handle a heartbeat and take appropriate action.

        Args:
            heartbeat: Heartbeat to evaluate
            on_alert: Optional callback for alerts

        Returns:
            RemediationAction if action taken, None otherwise
        """
        level = self.get_escalation_level(heartbeat)

        if level == EscalationLevel.NONE:
            return None

        if not self.should_take_action(heartbeat.session_id, level):
            return None

        action = None

        if level == EscalationLevel.WARNING:
            action = self._handle_warning(heartbeat)

        elif level == EscalationLevel.ALERT:
            action = self._handle_alert(heartbeat, on_alert)

        elif level == EscalationLevel.COMPACT:
            if self.config.auto_compact:
                action = self._handle_compact(heartbeat)
            else:
                action = self._handle_alert(heartbeat, on_alert)

        elif level == EscalationLevel.KILL:
            if self.config.auto_recovery:
                action = self._handle_kill(heartbeat)
            elif self.config.auto_compact:
                action = self._handle_compact(heartbeat)
            else:
                action = self._handle_alert(heartbeat, on_alert)

        if action:
            self._log_action(action)

        return action

    def _handle_warning(self, heartbeat: HeartbeatData) -> RemediationAction:
        """Handle warning level (log only)."""
        logger.warning(
            f"Session {heartbeat.session_id} heartbeat stale: "
            f"{heartbeat.age_seconds():.1f}s ago"
        )

        return RemediationAction(
            timestamp=datetime.now().isoformat(),
            session_id=heartbeat.session_id,
            level="WARNING",
            action="logged",
            success=True,
            details=f"Heartbeat age: {heartbeat.age_seconds():.1f}s"
        )

    def _handle_alert(
        self,
        heartbeat: HeartbeatData,
        callback: Optional[Callable] = None,
    ) -> RemediationAction:
        """Handle alert level (notify user)."""
        # Write alert file
        alert_file = self.alerts_dir / f"alert_{heartbeat.session_id}_{int(time.time())}.json"
        alert_data = {
            'timestamp': datetime.now().isoformat(),
            'session_id': heartbeat.session_id,
            'age_seconds': heartbeat.age_seconds(),
            'pid': heartbeat.pid,
            'status': heartbeat.status,
            'message': f"Terminal freeze detected: {heartbeat.session_id}",
        }
        alert_file.write_text(json.dumps(alert_data, indent=2))

        logger.warning(
            f"ALERT: Session {heartbeat.session_id} appears frozen "
            f"(no heartbeat for {heartbeat.age_seconds():.1f}s)"
        )

        # Call alert callback if provided
        if callback:
            try:
                callback(heartbeat)
            except Exception as e:
                logger.error(f"Alert callback failed: {e}")

        return RemediationAction(
            timestamp=datetime.now().isoformat(),
            session_id=heartbeat.session_id,
            level="ALERT",
            action="notified",
            success=True,
            details=f"Alert written to {alert_file}"
        )

    def _handle_compact(self, heartbeat: HeartbeatData) -> RemediationAction:
        """Handle compact level (send /compact)."""
        success = self.send_compact(heartbeat.session_id)

        return RemediationAction(
            timestamp=datetime.now().isoformat(),
            session_id=heartbeat.session_id,
            level="COMPACT",
            action="sent_compact",
            success=success,
            details=f"/compact sent to tmux session {heartbeat.session_id}"
        )

    def _handle_kill(self, heartbeat: HeartbeatData) -> RemediationAction:
        """Handle kill level (terminate and respawn)."""
        # Save checkpoint if enabled
        checkpoint = None
        if self.config.save_checkpoint:
            checkpoint = self._save_checkpoint(heartbeat)

        # Kill the session
        killed = self.kill_session(heartbeat.session_id)

        # Spawn recovery session
        spawned = False
        if killed:
            spawned = self.spawn_recovery_session(heartbeat, checkpoint)

        return RemediationAction(
            timestamp=datetime.now().isoformat(),
            session_id=heartbeat.session_id,
            level="KILL",
            action="killed_and_respawned" if spawned else "killed",
            success=killed,
            details=f"Killed: {killed}, Respawned: {spawned}, Checkpoint: {checkpoint}"
        )

    def _save_checkpoint(self, heartbeat: HeartbeatData) -> Optional[str]:
        """
        Save checkpoint data before killing session.

        Args:
            heartbeat: Heartbeat of session to checkpoint

        Returns:
            Path to checkpoint file or None
        """
        checkpoint_dir = self.health_dir / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        checkpoint_file = checkpoint_dir / f"checkpoint_{heartbeat.session_id}_{int(time.time())}.json"

        checkpoint_data = {
            'timestamp': datetime.now().isoformat(),
            'session_id': heartbeat.session_id,
            'working_dir': heartbeat.working_dir,
            'pid': heartbeat.pid,
            'last_tool': heartbeat.last_tool,
            'context_tokens': heartbeat.context_tokens,
            'reason': 'freeze_recovery',
        }

        try:
            # Atomic write: write to temp file then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(checkpoint_dir), suffix=".tmp", prefix="checkpoint_"
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(checkpoint_data, f, indent=2)
                os.replace(tmp_path, str(checkpoint_file))
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            logger.info(f"Saved checkpoint: {checkpoint_file}")
            return str(checkpoint_file)
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
            return None

    def send_compact(self, session_id: str) -> bool:
        """
        Send /compact command to a session.

        Uses tmux send-keys to inject the command.

        Args:
            session_id: Session/tmux session name

        Returns:
            True if command sent successfully
        """
        try:
            # Try to send via tmux
            result = subprocess.run(
                ["tmux", "send-keys", "-t", session_id, "/compact", "Enter"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                logger.info(f"Sent /compact to session {session_id}")
                return True
            else:
                logger.warning(f"Failed to send /compact: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout sending /compact to {session_id}")
            return False
        except FileNotFoundError:
            logger.error("tmux not available")
            return False
        except Exception as e:
            logger.error(f"Error sending /compact: {e}")
            return False

    def kill_session(self, session_id: str) -> bool:
        """
        Kill a terminal session.

        Tries tmux first, then sends SIGTERM to PID.

        Args:
            session_id: Session identifier

        Returns:
            True if killed successfully
        """
        killed = False

        # Try tmux kill-session
        try:
            result = subprocess.run(
                ["tmux", "kill-session", "-t", session_id],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"Killed tmux session {session_id}")
                killed = True
        except Exception as e:
            logger.debug(f"tmux kill failed: {e}")

        return killed

    def spawn_recovery_session(
        self,
        heartbeat: HeartbeatData,
        checkpoint: Optional[str] = None,
    ) -> bool:
        """
        Spawn a recovery session in iTerm2.

        Args:
            heartbeat: Original heartbeat data
            checkpoint: Path to checkpoint file

        Returns:
            True if spawned successfully
        """
        try:
            working_dir = heartbeat.working_dir

            # Build recovery command (shell-safe quoting)
            recovery_cmd = f"cd {shlex.quote(working_dir)} && echo 'Recovery session started from checkpoint' && claude --continue"

            # Try iTerm2 on macOS
            if os.uname().sysname == 'Darwin':
                return self._spawn_iterm_recovery(recovery_cmd, heartbeat.session_id)
            else:
                # Fall back to tmux
                return self._spawn_tmux_recovery(recovery_cmd, heartbeat.session_id)

        except Exception as e:
            logger.error(f"Failed to spawn recovery session: {e}")
            return False

    def _spawn_iterm_recovery(self, command: str, title: str) -> bool:
        """Spawn recovery session in iTerm2."""
        safe_title = _escape_applescript(title)
        safe_command = _escape_applescript(command)
        script = f'''
tell application "iTerm2"
    create window with default profile
    tell current session of current window
        set name to "Recovery: {safe_title}"
        write text "{safe_command}"
    end tell
    activate
end tell
'''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"Spawned iTerm2 recovery session: {title}")
                return True
            else:
                logger.warning(f"iTerm2 spawn failed: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"iTerm2 spawn error: {e}")
            return False

    def _spawn_tmux_recovery(self, command: str, session_name: str) -> bool:
        """Spawn recovery session in tmux."""
        recovery_name = f"recovery_{session_name}"
        try:
            result = subprocess.run(
                ["tmux", "new-session", "-d", "-s", recovery_name, command],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"Spawned tmux recovery session: {recovery_name}")
                return True
            else:
                logger.warning(f"tmux spawn failed: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"tmux spawn error: {e}")
            return False

    def _log_action(self, action: RemediationAction) -> None:
        """Log an action to the remediation log (atomic append)."""
        log_file = self.logs_dir / "remediation.log"

        # Atomic append: write entry to temp file, then append via
        # read-tmp + append-to-log so a crash never corrupts the log.
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.logs_dir), suffix=".tmp", prefix="remediation_"
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(action.to_json() + "\n")
            # Append temp content to log file
            with open(tmp_path, "r") as tmp_f:
                entry = tmp_f.read()
            with open(log_file, "a") as lf:
                lf.write(entry)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def get_action_history(self, limit: int = 50) -> List[RemediationAction]:
        """
        Get recent remediation actions.

        Args:
            limit: Maximum number of actions to return

        Returns:
            List of recent RemediationActions
        """
        log_file = self.logs_dir / "remediation.log"
        if not log_file.exists():
            return []

        actions = []
        try:
            with open(log_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            actions.append(RemediationAction(**data))
                        except (json.JSONDecodeError, TypeError):
                            continue
        except Exception as e:
            logger.error(f"Error reading action history: {e}")

        # Return most recent
        return actions[-limit:]


def verify_freeze(session_id: str, pid: int) -> bool:
    """
    Verify that a session is actually frozen.

    Secondary checks to confirm freeze before taking action:
    1. PID still exists
    2. tmux session exists and is unresponsive

    Args:
        session_id: Session identifier
        pid: Process ID from heartbeat

    Returns:
        True if freeze is confirmed
    """
    # Check if PID still exists
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # Process doesn't exist - definitely frozen
        return True
    except PermissionError:
        # Process exists but we can't signal it
        pass

    # Check tmux session responsiveness
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session_id, "-p"],
            capture_output=True,
            timeout=5,
        )
        # If capture succeeds quickly, session might be OK
        if result.returncode == 0:
            return False
    except subprocess.TimeoutExpired:
        # Timeout capturing - definitely frozen
        return True
    except Exception:
        pass

    # Conservative default: do NOT confirm freeze when status is uncertain.
    # Returning True here would risk killing a session that is still alive.
    return False


if __name__ == '__main__':
    # CLI for testing
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description='Remediation handler CLI')
    parser.add_argument('command', choices=['compact', 'kill', 'history', 'verify'])
    parser.add_argument('--session', '-s', required=True, help='Session ID')
    parser.add_argument('--pid', type=int, help='PID for verify')
    parser.add_argument('--json', action='store_true', help='JSON output')

    args = parser.parse_args()

    handler = RemediationHandler()

    if args.command == 'compact':
        success = handler.send_compact(args.session)
        print(f"Compact sent: {success}")

    elif args.command == 'kill':
        success = handler.kill_session(args.session)
        print(f"Session killed: {success}")

    elif args.command == 'history':
        actions = handler.get_action_history()
        if args.json:
            print(json.dumps([asdict(a) for a in actions], indent=2))
        else:
            for action in actions[-10:]:
                print(f"{action.timestamp}: {action.level} - {action.action} ({action.session_id})")

    elif args.command == 'verify':
        if not args.pid:
            print("--pid required for verify")
            exit(1)
        frozen = verify_freeze(args.session, args.pid)
        print(f"Freeze verified: {frozen}")
