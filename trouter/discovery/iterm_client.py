#!/usr/bin/env python3
"""
iTerm Control Client
====================

Direct Python client for controlling iTerm terminals without MCP.
Use this for scripting and automation.

Usage:
    from CLI.mcp.iterm.client import ItermController

    ctrl = ItermController()
    ctrl.list_windows()
    ctrl.compact(2)
    ctrl.force_compact(2)
    ctrl.compact_all_low_context(threshold=25)
"""

import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionStats:
    """Claude session statistics."""
    window_number: int
    window_name: str
    tokens: Optional[int] = None
    context_left: Optional[int] = None
    background_tasks: int = 0
    mode: str = "normal"


@dataclass
class TerminalWindow:
    """iTerm terminal window info."""
    window_number: int
    window_name: str
    session_name: str
    tty: str


class ItermController:
    """Controller for iTerm terminals running Claude Code."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def _log(self, msg: str):
        """Log message if verbose."""
        if self.verbose:
            print(f"[iTerm] {msg}")

    def _run_applescript(self, script: str, timeout: int = 30) -> str:
        """Execute AppleScript and return output."""
        try:
            result = subprocess.run(
                ["osascript"],
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "Error: AppleScript timeout"
        except Exception as e:
            return f"Error: {str(e)}"

    def list_windows(self) -> list[TerminalWindow]:
        """List all iTerm windows."""
        script = '''
        tell application "iTerm"
            set windowList to {}
            set windowCount to count of windows
            repeat with i from 1 to windowCount
                set w to window i
                set winName to name of w
                tell w
                    set currentSess to current session of current tab
                    set sessName to name of currentSess
                    set sessTTY to tty of currentSess
                end tell
                set end of windowList to (i as text) & "|" & winName & "|" & sessName & "|" & sessTTY
            end repeat
            return windowList
        end tell
        '''
        output = self._run_applescript(script)
        self._log(f"Raw output: {output}")

        windows = []
        if output and not output.startswith("Error"):
            for item in output.split(", "):
                parts = item.split("|")
                if len(parts) >= 4:
                    windows.append(TerminalWindow(
                        window_number=int(parts[0]),
                        window_name=parts[1],
                        session_name=parts[2],
                        tty=parts[3]
                    ))
        return windows

    def get_output(self, window_num: int, lines: int = 20) -> str:
        """Get recent output from a terminal window."""
        script = f'''
        tell application "iTerm"
            tell window {window_num}
                tell current session of current tab
                    set screenContent to contents
                    return screenContent
                end tell
            end tell
        end tell
        '''
        output = self._run_applescript(script)
        if output:
            output_lines = output.split('\n')
            return '\n'.join(output_lines[-lines:])
        return output

    def send(self, window_num: int, text: str, press_enter: bool = True) -> bool:
        """Send text/command to a terminal window.

        Args:
            window_num: iTerm window number (1-indexed)
            text: Command or text to send
            press_enter: If True (default), sends Enter key after text
        """
        escaped_text = text.replace('"', '\\"')
        if press_enter:
            # Type text, bring target window to front, then explicitly press Enter
            # (write text's newline can be unreliable; keystroke goes to frontmost window)
            script = f'''
            tell application "iTerm" to activate
            delay 0.15
            tell application "iTerm"
                set index of window {window_num} to 1
                tell window {window_num}
                    tell current session of current tab
                        write text "{escaped_text}" without newline
                    end tell
                end tell
            end tell
            delay 0.1
            tell application "System Events"
                keystroke return
            end tell
            '''
        else:
            script = f'''
            tell application "iTerm"
                tell window {window_num}
                    tell current session of current tab
                        write text "{escaped_text}" without newline
                    end tell
                end tell
            end tell
            '''
        result = self._run_applescript(script)
        self._log(f"Sent '{text}' to window {window_num} (enter={press_enter})")
        return not result.startswith("Error")

    def send_ctrl_c(self, window_num: int) -> bool:
        """Send Ctrl+C to interrupt a terminal."""
        script = f'''
        tell application "iTerm"
            tell window {window_num}
                tell current session of current tab
                    write text (ASCII character 3)
                end tell
            end tell
        end tell
        '''
        result = self._run_applescript(script)
        self._log(f"Sent Ctrl+C to window {window_num}")
        return not result.startswith("Error")

    def compact(self, window_num: int) -> bool:
        """Send /compact to a terminal."""
        return self.send(window_num, "/compact")

    def force_compact(self, window_num: int, interrupt_count: int = 2, delay: float = 1.0) -> bool:
        """Interrupt tasks then send /compact."""
        self._log(f"Force compacting window {window_num}")

        # Send Ctrl+C interrupts
        for i in range(interrupt_count):
            self.send_ctrl_c(window_num)
            self._log(f"Ctrl+C {i+1}/{interrupt_count}")
            time.sleep(delay)

        # Wait a moment more
        time.sleep(delay)

        # Send compact
        return self.compact(window_num)

    def parse_stats(self, output: str) -> dict:
        """Parse Claude session stats from terminal output."""
        stats = {
            "tokens": None,
            "context_left": None,
            "background_tasks": 0,
            "mode": "normal"
        }

        # Parse token count
        token_match = re.search(r'(\d+)\s*tokens', output)
        if token_match:
            stats["tokens"] = int(token_match.group(1))

        # Parse context left
        context_match = re.search(r'Context left[^:]*:\s*(\d+)%', output)
        if context_match:
            stats["context_left"] = int(context_match.group(1))

        # Parse background tasks
        tasks_match = re.search(r'(\d+)\s*background\s*tasks?', output)
        if tasks_match:
            stats["background_tasks"] = int(tasks_match.group(1))

        # Parse mode
        if "plan mode" in output.lower():
            stats["mode"] = "plan"
        elif "accept edits" in output.lower():
            stats["mode"] = "edit_review"

        return stats

    def get_session_stats(self, window_num: int) -> SessionStats:
        """Get Claude session statistics for a window."""
        windows = self.list_windows()
        window_name = ""
        for w in windows:
            if w.window_number == window_num:
                window_name = w.window_name
                break

        output = self.get_output(window_num, 30)
        stats = self.parse_stats(output)

        return SessionStats(
            window_number=window_num,
            window_name=window_name,
            tokens=stats["tokens"],
            context_left=stats["context_left"],
            background_tasks=stats["background_tasks"],
            mode=stats["mode"]
        )

    def get_all_session_stats(self) -> list[SessionStats]:
        """Get stats for all Claude sessions."""
        windows = self.list_windows()
        stats_list = []

        for window in windows:
            # Check if it's likely a Claude session
            if "claude" in window.session_name.lower() or "claude" in window.window_name.lower():
                stats = self.get_session_stats(window.window_number)
                stats_list.append(stats)

        return stats_list

    def compact_all_low_context(
        self,
        threshold: int = 25,
        force: bool = False
    ) -> list[str]:
        """Compact all sessions below context threshold."""
        results = []

        for stats in self.get_all_session_stats():
            if stats.context_left is not None and stats.context_left < threshold:
                if stats.background_tasks > 0 and not force:
                    msg = f"Window {stats.window_number}: {stats.context_left}% - skipped (has {stats.background_tasks} background tasks)"
                    results.append(msg)
                    self._log(msg)
                else:
                    if force and stats.background_tasks > 0:
                        self.force_compact(stats.window_number)
                    else:
                        self.compact(stats.window_number)
                    msg = f"Window {stats.window_number}: {stats.context_left}% - compact sent"
                    results.append(msg)
                    self._log(msg)

        if not results:
            results.append(f"No Claude sessions found below {threshold}% context")

        return results


def main():
    """CLI interface."""
    import argparse

    parser = argparse.ArgumentParser(description="iTerm Control CLI")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # list
    subparsers.add_parser("list", help="List all iTerm windows")

    # stats
    stats_p = subparsers.add_parser("stats", help="Get session stats")
    stats_p.add_argument("window", type=int, nargs="?", help="Window number (omit for all)")

    # compact
    compact_p = subparsers.add_parser("compact", help="Send /compact")
    compact_p.add_argument("window", type=int, help="Window number")

    # force-compact
    fc_p = subparsers.add_parser("force-compact", help="Force compact")
    fc_p.add_argument("window", type=int, help="Window number")

    # compact-all
    ca_p = subparsers.add_parser("compact-all", help="Compact all low-context sessions")
    ca_p.add_argument("-t", "--threshold", type=int, default=25, help="Context threshold %%")
    ca_p.add_argument("-f", "--force", action="store_true", help="Force even with background tasks")

    # discover (SessionDiscovery)
    disc_p = subparsers.add_parser("discover", help="Discover all Claude sessions with state")
    disc_p.add_argument("--no-frozen", action="store_true", help="Exclude sessions with stale heartbeats")
    disc_p.add_argument("--json", action="store_true", help="Output as JSON")

    # send-by-name
    sbn_p = subparsers.add_parser("send-by-name", help="Send command to session by window name")
    sbn_p.add_argument("name", help="Window name pattern (substring match)")
    sbn_p.add_argument("command", help="Command to send")
    sbn_p.add_argument("--wait", action="store_true", help="Wait for session ready before sending")

    # wait-ready
    wr_p = subparsers.add_parser("wait-ready", help="Wait until session is ready (idle) or timeout")
    wr_p.add_argument("name", help="Window name pattern")
    wr_p.add_argument("-t", "--timeout", type=int, default=60, help="Timeout seconds (default: 60)")

    args = parser.parse_args()

    ctrl = ItermController(verbose=args.verbose)

    if args.command == "list":
        for w in ctrl.list_windows():
            print(f"{w.window_number}: {w.window_name} ({w.session_name}) - {w.tty}")

    elif args.command == "stats":
        if args.window:
            stats = ctrl.get_session_stats(args.window)
            print(f"Window {stats.window_number}: {stats.window_name}")
            print(f"  Tokens: {stats.tokens}")
            print(f"  Context left: {stats.context_left}%")
            print(f"  Background tasks: {stats.background_tasks}")
            print(f"  Mode: {stats.mode}")
        else:
            for stats in ctrl.get_all_session_stats():
                print(f"Window {stats.window_number}: {stats.window_name}")
                print(f"  Tokens: {stats.tokens}, Context: {stats.context_left}%, Tasks: {stats.background_tasks}")

    elif args.command == "compact":
        if ctrl.compact(args.window):
            print(f"Sent /compact to window {args.window}")
        else:
            print("Failed to send compact")

    elif args.command == "force-compact":
        if ctrl.force_compact(args.window):
            print(f"Force compact sent to window {args.window}")
        else:
            print("Failed to force compact")

    elif args.command == "compact-all":
        for result in ctrl.compact_all_low_context(args.threshold, args.force):
            print(result)

    elif args.command == "discover":
        try:
            from CLI.lib.session_discovery import SessionDiscovery
            d = SessionDiscovery(ctrl)
            sessions = d.discover_sessions(include_frozen=not args.no_frozen)
            if args.json:
                import json
                data = [
                    {
                        "window_number": s.window_number,
                        "window_name": s.window_name,
                        "state": s.state,
                        "context_pct": s.context_pct,
                        "is_responsive": s.is_responsive,
                        "ready": d.is_session_ready(s),
                    }
                    for s in sessions
                ]
                print(json.dumps(data, indent=2))
            else:
                for s in sessions:
                    r = " [ready]" if d.is_session_ready(s) else ""
                    ctx = f", ctx={s.context_pct}%" if s.context_pct is not None else ""
                    print(f"{s.window_number}: {s.window_name} state={s.state}{ctx}{r}")
        except ImportError as e:
            print(f"Error: SessionDiscovery not available: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "send-by-name":
        try:
            from CLI.lib.session_discovery import SessionDiscovery
            d = SessionDiscovery(ctrl)
            if args.wait:
                ok, msg = d.wait_for_session_ready_sync(args.name, timeout=60)
                if not ok:
                    print(f"Error: {msg}", file=sys.stderr)
                    sys.exit(1)
            session = d.find_session_by_name(args.name)
            if not session:
                print(f"Session '{args.name}' not found", file=sys.stderr)
                sys.exit(1)
            if ctrl.send(session.window_number, args.command):
                print(f"Sent to window {session.window_number}: {args.command}")
            else:
                print("Failed to send command", file=sys.stderr)
                sys.exit(1)
        except ImportError as e:
            print(f"Error: SessionDiscovery not available: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "wait-ready":
        try:
            from CLI.lib.session_discovery import SessionDiscovery
            d = SessionDiscovery(ctrl)
            ok, msg = d.wait_for_session_ready_sync(args.name, timeout=args.timeout)
            if ok:
                print(msg)
            else:
                print(f"Error: {msg}", file=sys.stderr)
                sys.exit(1)
        except ImportError as e:
            print(f"Error: SessionDiscovery not available: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
