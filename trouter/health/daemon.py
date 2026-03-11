"""
Watchdog Daemon for Terminal Health Monitoring.

Background daemon that monitors heartbeats and triggers remediation
actions when sessions appear frozen.

The daemon runs in a dedicated tmux session (watchdog_daemon) and
polls heartbeat files at a configurable interval.

Usage:
    python -m CLI.lib.watchdog.daemon [--interval 10] [--timeout 60]
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from trouter.health.heartbeat import HeartbeatManager, HeartbeatData, DEFAULT_HEALTH_DIR
from trouter.health.remediation import (
    RemediationHandler,
    RemediationConfig,
    EscalationLevel,
    verify_freeze,
)

logger = logging.getLogger(__name__)


@dataclass
class WatchdogConfig:
    """Configuration for watchdog daemon."""
    check_interval: float = 10.0       # seconds between checks
    heartbeat_timeout: float = 60.0    # seconds before considered frozen
    compact_threshold: float = 90.0    # seconds before /compact
    kill_threshold: float = 120.0      # seconds before kill
    auto_compact: bool = True
    auto_recovery: bool = True
    verify_before_action: bool = True  # verify freeze before remediation
    cleanup_interval: float = 300.0    # seconds between stale cleanup

    @classmethod
    def from_env(cls) -> 'WatchdogConfig':
        """Load configuration from environment variables."""
        return cls(
            check_interval=float(os.environ.get('WATCHDOG_CHECK_INTERVAL', '10')),
            heartbeat_timeout=float(os.environ.get('WATCHDOG_HEARTBEAT_TIMEOUT', '60')),
            compact_threshold=float(os.environ.get('WATCHDOG_COMPACT_THRESHOLD', '90')),
            kill_threshold=float(os.environ.get('WATCHDOG_KILL_THRESHOLD', '120')),
            auto_compact=os.environ.get('WATCHDOG_AUTO_COMPACT', 'true').lower() == 'true',
            auto_recovery=os.environ.get('WATCHDOG_AUTO_RECOVERY', 'true').lower() == 'true',
        )


@dataclass
class DaemonStatus:
    """Current daemon status."""
    running: bool
    pid: int
    started_at: str
    uptime_seconds: float
    checks_performed: int
    actions_taken: int
    sessions_monitored: int
    last_check: Optional[str] = None


class WatchdogDaemon:
    """
    Watchdog daemon for terminal health monitoring.

    Polls heartbeat files and triggers remediation actions
    when sessions appear frozen.

    Example:
        >>> daemon = WatchdogDaemon()
        >>> daemon.start()  # Blocks until stopped
    """

    def __init__(
        self,
        config: Optional[WatchdogConfig] = None,
        health_dir: Optional[Path] = None,
        on_alert: Optional[Callable[[HeartbeatData], None]] = None,
    ):
        """
        Initialize watchdog daemon.

        Args:
            config: Daemon configuration
            health_dir: Health data directory
            on_alert: Callback for alert-level events
        """
        self.config = config or WatchdogConfig.from_env()
        self.health_dir = health_dir or DEFAULT_HEALTH_DIR

        # Initialize components
        self.heartbeat_manager = HeartbeatManager(health_dir=self.health_dir)

        remediation_config = RemediationConfig(
            warning_threshold=self.config.heartbeat_timeout / 2,
            alert_threshold=self.config.heartbeat_timeout,
            compact_threshold=self.config.compact_threshold,
            kill_threshold=self.config.kill_threshold,
            auto_compact=self.config.auto_compact,
            auto_recovery=self.config.auto_recovery,
        )
        self.remediation_handler = RemediationHandler(
            config=remediation_config,
            health_dir=self.health_dir,
        )

        self.on_alert = on_alert

        # State
        self._running = False
        self._started_at: Optional[float] = None
        self._checks_performed = 0
        self._actions_taken = 0
        self._last_cleanup: float = 0

        # Status file
        self.status_dir = self.health_dir / "status"
        self.status_dir.mkdir(parents=True, exist_ok=True)
        self.status_file = self.status_dir / "watchdog.status"
        self.pid_file = self.status_dir / "watchdog.pid"

    def start(self) -> None:
        """
        Start the watchdog daemon.

        This method blocks until stop() is called or a signal is received.
        """
        if self._running:
            logger.warning("Daemon already running")
            return

        self._running = True
        self._started_at = time.time()
        self._setup_signal_handlers()
        self._write_pid_file()

        logger.info(
            f"Watchdog daemon started: interval={self.config.check_interval}s, "
            f"timeout={self.config.heartbeat_timeout}s"
        )

        try:
            self._run_loop()
        finally:
            self._cleanup()

    def stop(self) -> None:
        """Stop the daemon."""
        logger.info("Stopping watchdog daemon...")
        self._running = False

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame) -> None:
        """Handle shutdown signal."""
        logger.info(f"Received signal {signum}, stopping...")
        self.stop()

    def _write_pid_file(self) -> None:
        """Write PID file."""
        self.pid_file.write_text(str(os.getpid()))

    def _cleanup(self) -> None:
        """Cleanup on shutdown."""
        self._running = False
        try:
            self.pid_file.unlink(missing_ok=True)
            self.status_file.unlink(missing_ok=True)
        except Exception:
            pass
        logger.info("Watchdog daemon stopped")

    def _run_loop(self) -> None:
        """Main daemon loop."""
        while self._running:
            try:
                self._check_cycle()
            except Exception as e:
                logger.error(f"Error in check cycle: {e}")

            # Update status
            self._update_status()

            # Periodic cleanup
            if time.time() - self._last_cleanup > self.config.cleanup_interval:
                self._cleanup_stale()
                self._last_cleanup = time.time()

            # Sleep until next check
            time.sleep(self.config.check_interval)

    def _check_cycle(self) -> None:
        """Perform one check cycle."""
        self._checks_performed += 1
        heartbeats = self.heartbeat_manager.get_all_heartbeats()

        logger.debug(f"Check cycle {self._checks_performed}: {len(heartbeats)} sessions")

        for heartbeat in heartbeats:
            self._check_heartbeat(heartbeat)

    def _check_heartbeat(self, heartbeat: HeartbeatData) -> None:
        """
        Check a single heartbeat and take action if needed.

        Args:
            heartbeat: Heartbeat to check
        """
        level = self.remediation_handler.get_escalation_level(heartbeat)

        if level == EscalationLevel.NONE:
            return

        # Verify freeze before taking action (if enabled and level > WARNING)
        if self.config.verify_before_action and level.value >= EscalationLevel.ALERT.value:
            if not verify_freeze(heartbeat.session_id, heartbeat.pid):
                logger.debug(
                    f"Freeze verification failed for {heartbeat.session_id}, skipping"
                )
                return

        # Handle the heartbeat
        action = self.remediation_handler.handle_heartbeat(
            heartbeat,
            on_alert=self.on_alert,
        )

        if action and action.success:
            self._actions_taken += 1
            logger.info(f"Action taken: {action.level} - {action.action} for {action.session_id}")

    def _update_status(self) -> None:
        """Update daemon status file."""
        heartbeats = self.heartbeat_manager.get_all_heartbeats()

        status = DaemonStatus(
            running=self._running,
            pid=os.getpid(),
            started_at=datetime.fromtimestamp(self._started_at).isoformat() if self._started_at else "",
            uptime_seconds=time.time() - self._started_at if self._started_at else 0,
            checks_performed=self._checks_performed,
            actions_taken=self._actions_taken,
            sessions_monitored=len(heartbeats),
            last_check=datetime.now().isoformat(),
        )

        try:
            self.status_file.write_text(json.dumps(asdict(status), indent=2))
        except Exception as e:
            logger.debug(f"Failed to write status: {e}")

    def _cleanup_stale(self) -> None:
        """Cleanup stale heartbeat files."""
        # Remove heartbeats older than 5 minutes (300 seconds)
        count = self.heartbeat_manager.cleanup_stale(300)
        if count > 0:
            logger.info(f"Cleaned up {count} stale heartbeat files")

    def get_status(self) -> Optional[DaemonStatus]:
        """
        Get current daemon status.

        Returns:
            DaemonStatus or None if not running
        """
        if not self.status_file.exists():
            return None

        try:
            data = json.loads(self.status_file.read_text())
            # Filter to only known DaemonStatus fields to handle version
            # mismatches gracefully (e.g., status file written by a newer
            # daemon version with extra fields).
            import dataclasses
            known_fields = {f.name for f in dataclasses.fields(DaemonStatus)}
            filtered = {k: v for k, v in data.items() if k in known_fields}
            return DaemonStatus(**filtered)
        except Exception:
            return None

    @classmethod
    def is_running(cls, health_dir: Optional[Path] = None) -> bool:
        """
        Check if daemon is running.

        Args:
            health_dir: Health directory to check

        Returns:
            True if daemon is running
        """
        health_dir = health_dir or DEFAULT_HEALTH_DIR
        pid_file = health_dir / "status" / "watchdog.pid"

        if not pid_file.exists():
            return False

        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process exists
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            return False


def spawn_iterm_alert(heartbeat: HeartbeatData) -> None:
    """
    Spawn an alert window in iTerm2.

    Args:
        heartbeat: Heartbeat that triggered the alert
    """
    import subprocess
    from trouter.health.remediation import _escape_applescript

    safe_sid = _escape_applescript(heartbeat.session_id)
    safe_age = f"{heartbeat.age_seconds():.0f}"

    script = f'''
tell application "iTerm2"
    create window with default profile
    tell current session of current window
        set name to "FREEZE ALERT: {safe_sid}"
        write text "echo '========================================'"
        write text "echo 'TERMINAL FREEZE DETECTED'"
        write text "echo '========================================'"
        write text "echo 'Session: {safe_sid}'"
        write text "echo 'Frozen for: {safe_age} seconds'"
        write text "echo ''"
        write text "echo 'Options:'"
        write text "echo '  1. watchdog_compact {safe_sid}'"
        write text "echo '  2. watchdog_kill {safe_sid}'"
        write text "echo '  3. Close this window to dismiss'"
    end tell
    activate
end tell
'''
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        logger.info(f"Spawned iTerm alert for {heartbeat.session_id}")
    except Exception as e:
        logger.error(f"Failed to spawn iTerm alert: {e}")


def main():
    """Main entry point for daemon."""
    parser = argparse.ArgumentParser(description='Terminal watchdog daemon')
    parser.add_argument('--interval', '-i', type=float, default=10.0,
                       help='Check interval in seconds (default: 10)')
    parser.add_argument('--timeout', '-t', type=float, default=60.0,
                       help='Heartbeat timeout in seconds (default: 60)')
    parser.add_argument('--compact-threshold', type=float, default=90.0,
                       help='Seconds before /compact (default: 90)')
    parser.add_argument('--kill-threshold', type=float, default=120.0,
                       help='Seconds before kill (default: 120)')
    parser.add_argument('--no-auto-compact', action='store_true',
                       help='Disable automatic /compact')
    parser.add_argument('--no-auto-recovery', action='store_true',
                       help='Disable automatic session restart')
    parser.add_argument('--no-alerts', action='store_true',
                       help='Disable iTerm alert windows')
    parser.add_argument('--debug', '-d', action='store_true',
                       help='Enable debug logging')
    parser.add_argument('--status', action='store_true',
                       help='Show daemon status and exit')

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Status check
    if args.status:
        daemon = WatchdogDaemon()
        status = daemon.get_status()
        if status:
            print(json.dumps(asdict(status), indent=2))
            sys.exit(0)
        else:
            print("Daemon is not running")
            sys.exit(1)

    # Check if already running
    if WatchdogDaemon.is_running():
        logger.error("Daemon is already running")
        sys.exit(1)

    # Create config
    config = WatchdogConfig(
        check_interval=args.interval,
        heartbeat_timeout=args.timeout,
        compact_threshold=args.compact_threshold,
        kill_threshold=args.kill_threshold,
        auto_compact=not args.no_auto_compact,
        auto_recovery=not args.no_auto_recovery,
    )

    # Alert callback
    on_alert = None if args.no_alerts else spawn_iterm_alert

    # Create and start daemon
    daemon = WatchdogDaemon(config=config, on_alert=on_alert)
    daemon.start()


if __name__ == '__main__':
    main()
