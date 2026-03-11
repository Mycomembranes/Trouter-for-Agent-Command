#!/usr/bin/env python3
"""
Claude Session REST API Client
==============================

Client library for programmatic control of Claude sessions via the Session REST API.
Enables trouter, scripts, and other tools to interact with Claude sessions from any context.

Reference: doc/PROPOSED_SOLUTIONS.md (Issue 3: Programmatic Claude Session Access)

API Server:
    Start with: python3 CLI/mcp/session_api.py --port 8765
    Default base URL: http://localhost:8765

Usage:
    from CLI.lib.session_client import ClaudeSessionClient

    client = ClaudeSessionClient()
    sessions = client.list_sessions()
    client.send_command(2, "/compact")
    stats = client.get_stats(2)
    output = client.get_output(2, lines=50)

Requirements:
    pip install requests
"""

import json
import time
from typing import Any, Dict, List, Optional

try:
    import requests
    from requests.exceptions import (
        ConnectionError,
        Timeout,
        RequestException,
        HTTPError,
    )
except ImportError:
    requests = None  # type: ignore


class SessionAPIError(Exception):
    """Base exception for Session API client errors."""

    pass


class SessionAPIConnectionError(SessionAPIError):
    """Raised when connection to the API server fails."""

    pass


class SessionAPITimeoutError(SessionAPIError):
    """Raised when a request exceeds the configured timeout."""

    pass


class SessionAPIHTTPError(SessionAPIError):
    """Raised when the API returns an error status code."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class ClaudeSessionClient:
    """
    Client for the Claude Session REST API.

    Provides methods to list sessions, send commands, retrieve output,
    trigger compaction, and fetch session statistics. Includes retry logic
    with exponential backoff for transient failures.

    Attributes:
        base_url (str): Base URL of the Session API (e.g., http://localhost:8765).
        timeout (float): Request timeout in seconds. Default: 30.0.
        max_retries (int): Maximum number of retry attempts. Default: 3.
        retry_delays (tuple): Delays in seconds for each retry (exponential backoff).
    """

    DEFAULT_TIMEOUT = 30.0
    DEFAULT_RETRIES = 3
    DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0)  # Exponential backoff: 1s, 2s, 4s

    def __init__(
        self,
        base_url: str = "http://localhost:8765",
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_delays: Optional[tuple] = None,
    ):
        """
        Initialize the Claude Session API client.

        Args:
            base_url: Base URL of the Session API server. Default: http://localhost:8765.
            timeout: Request timeout in seconds. Default: 30.0.
            max_retries: Maximum retry attempts for transient failures. Default: 3.
            retry_delays: Tuple of delay seconds for each retry (exponential backoff).
                Default: (1.0, 2.0, 4.0).
        """
        if requests is None:
            raise ImportError(
                "The 'requests' library is required for ClaudeSessionClient. "
                "Install with: pip install requests"
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self.max_retries = max_retries if max_retries is not None else self.DEFAULT_RETRIES
        self.retry_delays = retry_delays or self.DEFAULT_RETRY_DELAYS

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Execute an HTTP request with retry logic and exponential backoff.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: URL path (e.g., /sessions).
            json_body: Optional JSON body for POST/PUT requests.
            params: Optional query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            SessionAPIConnectionError: When server is unreachable.
            SessionAPITimeoutError: When request times out.
            SessionAPIHTTPError: When API returns an error status.
        """
        url = f"{self.base_url}{path}"
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                kwargs: Dict[str, Any] = {
                    "timeout": self.timeout,
                    "params": params,
                }
                if json_body is not None:
                    kwargs["json"] = json_body

                response = requests.request(method, url, **kwargs)
                response.raise_for_status()
                if response.text:
                    return response.json()
                return {}

            except ConnectionError as e:
                last_error = SessionAPIConnectionError(
                    f"Cannot connect to Session API at {self.base_url}. "
                    f"Is the server running? (python3 CLI/mcp/session_api.py --port 8765). "
                    f"Original error: {e}"
                )
            except Timeout as e:
                last_error = SessionAPITimeoutError(
                    f"Request to {path} timed out after {self.timeout}s. "
                    f"Original error: {e}"
                )
            except HTTPError as e:
                response = getattr(e, "response", None)
                status_code = response.status_code if response else None
                body = response.text[:500] if response and response.text else None
                last_error = SessionAPIHTTPError(
                    f"API error: {e}",
                    status_code=status_code,
                    response_body=body,
                )
            except RequestException as e:
                last_error = SessionAPIError(f"Request failed: {e}")
            except json.JSONDecodeError as e:
                last_error = SessionAPIError(
                    f"Invalid JSON response from {path}: {e}"
                )

            if attempt < self.max_retries:
                delay = self.retry_delays[min(attempt, len(self.retry_delays) - 1)]
                time.sleep(delay)

        if last_error:
            raise last_error
        raise SessionAPIError("Request failed after retries")

    def list_sessions(self) -> List[Dict[str, Any]]:
        """
        List all active Claude sessions.

        Returns:
            List of session dicts with keys: id, name, tty, state, context_pct, responsive.

        Raises:
            SessionAPIError: On connection, timeout, or API error.

        Example:
            >>> client = ClaudeSessionClient()
            >>> sessions = client.list_sessions()
            >>> for s in sessions:
            ...     print(f"Session {s['id']}: {s['name']} ({s['state']})")
        """
        data = self._request("GET", "/sessions")
        if isinstance(data, list):
            return data
        return data.get("sessions", [data]) if isinstance(data, dict) else []

    def get_session(self, session_id: int) -> Dict[str, Any]:
        """
        Get details for a specific session by ID.

        Args:
            session_id: Session/window ID (integer).

        Returns:
            Session dict with keys: id, name, state, context_pct, responsive.

        Raises:
            SessionAPIError: On connection, timeout, or API error.
            SessionAPIHTTPError: When session is not found (404).

        Example:
            >>> client = ClaudeSessionClient()
            >>> session = client.get_session(2)
            >>> print(f"State: {session['state']}, Context: {session.get('context_pct')}%")
        """
        data = self._request("GET", f"/sessions/{session_id}")
        if isinstance(data, dict) and "error" in data:
            raise SessionAPIHTTPError(
                data.get("error", "Session not found"),
                status_code=404,
                response_body=json.dumps(data),
            )
        return data

    def send_command(self, session_id: int, command: str) -> Dict[str, Any]:
        """
        Send a command to a session (e.g., shell command or slash command).

        Args:
            session_id: Session/window ID (integer).
            command: Command string to send (e.g., "ls -la", "/compact", "/tasks").

        Returns:
            Dict with keys: success (bool), command (str).

        Raises:
            SessionAPIError: On connection, timeout, or API error.
            SessionAPIHTTPError: When no command provided (400) or session not found (404).

        Example:
            >>> client = ClaudeSessionClient()
            >>> result = client.send_command(2, "/compact")
            >>> print(f"Success: {result['success']}")
        """
        data = self._request(
            "POST",
            f"/sessions/{session_id}/command",
            json_body={"command": command},
        )
        if isinstance(data, dict) and "error" in data:
            raise SessionAPIHTTPError(
                data.get("error", "Command send failed"),
                status_code=400,
                response_body=json.dumps(data),
            )
        return data

    def get_output(self, session_id: int, lines: int = 20) -> Dict[str, Any]:
        """
        Get recent terminal output from a session.

        Args:
            session_id: Session/window ID (integer).
            lines: Number of output lines to retrieve. Default: 20.

        Returns:
            Dict with key "output" containing the terminal output string.

        Raises:
            SessionAPIError: On connection, timeout, or API error.
            SessionAPIHTTPError: When session not found (404).

        Example:
            >>> client = ClaudeSessionClient()
            >>> result = client.get_output(2, lines=50)
            >>> print(result["output"])
        """
        data = self._request(
            "GET",
            f"/sessions/{session_id}/output",
            params={"lines": lines},
        )
        if isinstance(data, dict) and "error" in data:
            raise SessionAPIHTTPError(
                data.get("error", "Output not available"),
                status_code=404,
                response_body=json.dumps(data),
            )
        return data

    def compact(self, session_id: int) -> Dict[str, Any]:
        """
        Trigger /compact on a session to reduce context usage.

        Args:
            session_id: Session/window ID (integer).

        Returns:
            Dict with key "success" (bool).

        Raises:
            SessionAPIError: On connection, timeout, or API error.
            SessionAPIHTTPError: When session not found (404).

        Example:
            >>> client = ClaudeSessionClient()
            >>> result = client.compact(2)
            >>> print(f"Compact triggered: {result['success']}")
        """
        data = self._request("POST", f"/sessions/{session_id}/compact")
        if isinstance(data, dict) and "error" in data:
            raise SessionAPIHTTPError(
                data.get("error", "Compact failed"),
                status_code=404,
                response_body=json.dumps(data),
            )
        return data

    def get_stats(self, session_id: int) -> Dict[str, Any]:
        """
        Get context and token statistics for a session.

        Args:
            session_id: Session/window ID (integer).

        Returns:
            Dict with keys such as window_number, window_name, tokens,
            context_left, background_tasks, mode, context_pct.

        Raises:
            SessionAPIError: On connection, timeout, or API error.
            SessionAPIHTTPError: When stats not available (404).

        Example:
            >>> client = ClaudeSessionClient()
            >>> stats = client.get_stats(2)
            >>> print(f"Context: {stats.get('context_pct')}%")
        """
        data = self._request("GET", f"/sessions/{session_id}/stats")
        if isinstance(data, dict) and "error" in data:
            raise SessionAPIHTTPError(
                data.get("error", "Stats not available"),
                status_code=404,
                response_body=json.dumps(data),
            )
        return data


def main() -> None:
    """CLI entry point for quick testing of the session client."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Claude Session REST API client (test/explore)"
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8765",
        help="Base URL of Session API",
    )
    parser.add_argument(
        "action",
        choices=["list", "get", "command", "output", "compact", "stats"],
        help="Action to perform",
    )
    parser.add_argument(
        "session_id",
        type=int,
        nargs="?",
        help="Session ID (required for get/command/output/compact/stats)",
    )
    parser.add_argument(
        "--cmd",
        help="Command string (required for 'command' action)",
    )
    parser.add_argument(
        "--lines",
        type=int,
        default=20,
        help="Lines of output (for 'output' action)",
    )
    args = parser.parse_args()

    client = ClaudeSessionClient(base_url=args.url, timeout=10.0)

    try:
        if args.action == "list":
            sessions = client.list_sessions()
            print(json.dumps(sessions, indent=2))
        elif args.action in ("get", "command", "output", "compact", "stats"):
            if args.session_id is None:
                parser.error(f"session_id required for action '{args.action}'")
            if args.action == "command" and not args.cmd:
                parser.error("--cmd required for 'command' action")
            if args.action == "get":
                print(json.dumps(client.get_session(args.session_id), indent=2))
            elif args.action == "command":
                print(json.dumps(client.send_command(args.session_id, args.cmd), indent=2))
            elif args.action == "output":
                print(json.dumps(client.get_output(args.session_id, lines=args.lines), indent=2))
            elif args.action == "compact":
                print(json.dumps(client.compact(args.session_id), indent=2))
            elif args.action == "stats":
                print(json.dumps(client.get_stats(args.session_id), indent=2))
    except SessionAPIError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
