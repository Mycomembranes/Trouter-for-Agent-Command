"""
Shared dispatch utilities for Cursor/Claude agent routing.

Canonical implementations of binary resolution, config reading,
model mapping, environment construction, and prompt rendering.
"""

import json
import os
import shutil
import subprocess
import time as _time
from pathlib import Path


# ---------------------------------------------------------------------------
# Health status constants
# ---------------------------------------------------------------------------


class HealthStatus:
    """Canonical health-status labels used by watchdog, heartbeat, and agent-list."""

    HEALTHY = "healthy"
    WARNING = "warning"
    FROZEN = "frozen"
    COMPLETED = "completed"
    ALERT = "alert"
    EXITED = "exited"
    DEGRADED = "degraded"
    STALE = "stale"
    RUNNING = "running"
    RECENT_EXIT = "recent_exit"
    STOPPED = "stopped"


# ---------------------------------------------------------------------------
# Dispatch mode constants
# ---------------------------------------------------------------------------

DISPATCH_NATIVE = "native"
DISPATCH_LOCAL = "local"
DISPATCH_API = "api"

DISPATCH_FALLBACK_ORDER: list[str] = [DISPATCH_NATIVE, DISPATCH_API, DISPATCH_LOCAL]

DISPATCH_BACKEND_LABELS: dict[str, str] = {
    DISPATCH_NATIVE: "native_cursor_agent",
    DISPATCH_API: "cursor_wrapper_api",
    DISPATCH_LOCAL: "claude_local_fallback",
}


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


_native_agent_cache: dict = {"path": None, "expires": 0.0}
_NATIVE_AGENT_TTL: float = 60.0  # seconds


def resolve_native_agent() -> str:
    """Scan ~/.local/share/cursor-agent/versions/ for the latest binary.

    Results are cached for 60 seconds.

    Returns:
        Path string to the binary, or empty string if not found.
    """
    now = _time.monotonic()
    if _native_agent_cache["path"] is not None and now < _native_agent_cache["expires"]:
        return _native_agent_cache["path"]

    result = _resolve_native_agent_uncached()
    _native_agent_cache["path"] = result
    _native_agent_cache["expires"] = now + _NATIVE_AGENT_TTL
    return result


def invalidate_native_agent_cache() -> None:
    """Reset the TTL-based native-agent cache."""
    _native_agent_cache["path"] = None
    _native_agent_cache["expires"] = 0.0


def _resolve_native_agent_uncached() -> str:
    """Perform the actual directory scan + stat for the native agent binary."""
    agent_dir = Path.home() / ".local/share/cursor-agent/versions"
    if agent_dir.exists():
        versions = sorted(
            (d for d in agent_dir.iterdir() if d.is_dir()),
            key=lambda d: d.name,
            reverse=True,
        )
        for v in versions:
            binary = v / "cursor-agent"
            if binary.exists() and binary.stat().st_size > 0:
                return str(binary)
    # Fallback: symlink at ~/.local/bin/cursor-agent
    symlink = Path.home() / ".local/bin/cursor-agent"
    if symlink.exists():
        try:
            resolved = symlink.resolve()
            if "cursor-agent/versions" in str(resolved):
                return str(resolved)
        except OSError:
            pass
    return ""


def resolve_claude_bin() -> str:
    """Find the Claude Code CLI binary.

    Returns:
        Path string to the binary, or empty string if not found.
    """
    p = Path.home() / ".local" / "bin" / "claude"
    if p.exists():
        return str(p)
    return shutil.which("claude") or ""


# ---------------------------------------------------------------------------
# Config reading (mtime-based caching)
# ---------------------------------------------------------------------------

_dispatch_mode_cache: dict = {"value": None, "mtime": 0.0}


def read_dispatch_mode(config_path: str = "") -> str:
    """Read dispatch_mode from cursor_config.json with mtime-based caching.

    Returns 'native', 'local', or 'api'.
    """
    if not config_path:
        return DISPATCH_NATIVE
    cfg = Path(config_path)
    try:
        current_mtime = cfg.stat().st_mtime
    except OSError:
        return DISPATCH_NATIVE
    if current_mtime == _dispatch_mode_cache["mtime"] and _dispatch_mode_cache["value"] is not None:
        return _dispatch_mode_cache["value"]
    try:
        with open(cfg) as f:
            value = json.load(f).get("dispatch_mode", DISPATCH_NATIVE)
    except Exception:
        value = DISPATCH_NATIVE
    _dispatch_mode_cache["value"] = value
    _dispatch_mode_cache["mtime"] = current_mtime
    return value


def invalidate_dispatch_mode_cache() -> None:
    """Reset the mtime-based dispatch mode cache."""
    _dispatch_mode_cache["value"] = None
    _dispatch_mode_cache["mtime"] = 0.0


# ---------------------------------------------------------------------------
# Model mapping
# ---------------------------------------------------------------------------


def map_cursor_model_to_claude(cursor_model: str) -> list[str]:
    """Map a Cursor model tier name to ``claude --model`` flag arguments.

    Returns:
        A list like ``["--model", "sonnet"]``, or an empty list for default.
    """
    if cursor_model == "composer-1.5":
        return ["--model", "sonnet"]
    if cursor_model in ("gpt-5.3-codex-low", "gpt-5.3-codex-low-fast"):
        return ["--model", "haiku"]
    elif cursor_model in (
        "gpt-5.3-codex", "gpt-5.3-codex-fast",
        "gpt-5.3-codex-high", "gpt-5.3-codex-high-fast",
    ):
        return ["--model", "sonnet"]
    elif cursor_model in ("gpt-5.3-codex-xhigh", "gpt-5.3-codex-xhigh-fast"):
        return ["--model", "opus"]
    return []


# ---------------------------------------------------------------------------
# Environment construction
# ---------------------------------------------------------------------------


def make_clean_env() -> dict[str, str]:
    """Create a clean subprocess environment.

    Copies ``os.environ``, strips ``CLAUDECODE`` and ``CURSOR_API_KEY``,
    and normalises ``PATH``.
    """
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CURSOR_API_KEY", None)
    env["PATH"] = (
        f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:/usr/local/bin:"
        f"{env.get('PATH', '')}"
    )
    return env


# ---------------------------------------------------------------------------
# Prompt context building
# ---------------------------------------------------------------------------


def build_prompt_context(cli_root: str, work_dir: Path) -> str:
    """Run ``build_prompt_context.sh`` and return the context string."""
    if os.environ.get("CURSOR_SKIP_CONTEXT") == "1":
        return ""
    script = Path(cli_root) / "lib" / "build_prompt_context.sh"
    if not script.exists():
        return ""
    try:
        result = subprocess.run(
            ["bash", str(script), str(work_dir)],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout or ""
    except Exception:
        return ""


def normalize_prompt_backend(dispatch_mode: str) -> str:
    """Map dispatch mode to shared prompt adapter backend IDs."""
    if dispatch_mode == DISPATCH_API:
        return "cursor-api"
    if dispatch_mode == DISPATCH_LOCAL:
        return "claude-local"
    return "cursor-native"


def render_prompt_for_backend(
    cli_root: str,
    prompt: str,
    dispatch_mode: str,
    work_dir: Path,
    context_files: str = "",
    role_hint: str = "",
) -> str:
    """Render a final prompt through the shared shell adapter contract."""
    backend = normalize_prompt_backend(dispatch_mode)
    script = Path(cli_root) / "lib" / "build_prompt_context.sh"
    if script.exists():
        try:
            result = subprocess.run(
                ["bash", str(script), "--render", backend, str(work_dir), context_files, role_hint],
                cwd=str(work_dir),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except Exception:
            pass

    # Fallback: manual assembly
    base_prompt = f"{role_hint}: {prompt}" if role_hint else prompt
    ctx = build_prompt_context(cli_root, work_dir)
    if not ctx:
        return base_prompt
    final_prompt = ctx.rstrip() + "\n\n" + base_prompt
    if context_files and backend != "claude-local":
        final_prompt = f"@{context_files} {final_prompt}"
    return final_prompt


def backend_label(dispatch_mode: str) -> str:
    """Canonical backend label for status/output contracts."""
    return DISPATCH_BACKEND_LABELS.get(dispatch_mode, DISPATCH_BACKEND_LABELS[DISPATCH_NATIVE])
