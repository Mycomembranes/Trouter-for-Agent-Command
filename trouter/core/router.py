"""
TerminalRouter -- Central controller for agent session management.

Extracted from CLI/bin/terminal_router.py L490+.  Manages routing tasks
to Cursor/Claude agents, selecting dispatch backends, and coordinating
the standby pool.  This is the engine; the REPL / TUI layers call into it.

Key responsibilities:
  - Accept tasks and classify them to the right swarm tier / model
  - Build dispatch commands (native cursor-agent, local Claude CLI)
  - Run agents via subprocess and stream or collect output
  - Manage pool lifecycle via StandbyPool
  - Expose status/listing helpers for the UI layer
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from trouter.core.dispatch import (
    DISPATCH_NATIVE,
    DISPATCH_LOCAL,
    DISPATCH_API,
    backend_label,
    make_clean_env,
    map_cursor_model_to_claude,
    read_dispatch_mode,
    render_prompt_for_backend,
    resolve_claude_bin,
    resolve_native_agent,
)
from trouter.core.pool import StandbyPool, StandbyConfig
from trouter.core.models import SWARM_TIERS

# Optional imports -- gracefully degrade when not available
try:
    from trouter.discovery import SessionDiscovery, ClaudeSession
except ImportError:  # pragma: no cover
    SessionDiscovery = None  # type: ignore[assignment,misc]
    ClaudeSession = None  # type: ignore[assignment,misc]

try:
    from trouter.health import HeartbeatManager
except ImportError:  # pragma: no cover
    HeartbeatManager = None  # type: ignore[assignment,misc]

try:
    from trouter.discovery.iterm_client import ItermController
except ImportError:  # pragma: no cover
    ItermController = None  # type: ignore[assignment,misc]


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes for dispatch results
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    """Outcome of a single agent dispatch."""

    success: bool
    exit_code: int = 0
    stdout: str = ""
    pid: int | None = None
    model: str = ""
    backend: str = ""
    tier: str = ""
    duration_secs: float = 0.0
    error: str = ""
    slot_id: str = ""


@dataclass
class TriadResult:
    """Outcome of a 3-leg triad execution."""

    legs: list[DispatchResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(leg.success for leg in self.legs)


@dataclass
class SwarmResult:
    """Outcome of a multi-worker swarm execution."""

    workers: list[DispatchResult] = field(default_factory=list)
    tier: str = ""
    model: str = ""

    @property
    def all_ok(self) -> bool:
        return all(w.success for w in self.workers)


# ---------------------------------------------------------------------------
# TerminalRouter
# ---------------------------------------------------------------------------


class TerminalRouter:
    """Central controller for Composer 1.5 / agent sessions.

    Manages agent dispatch, swarm routing, standby pool lifecycle, and
    session status queries.  The class is backend-agnostic: it builds
    commands and runs them via subprocess, selecting the right binary
    based on ``dispatch_mode`` in ``cursor_config.json``.
    """

    def __init__(
        self,
        cli_root: str | Path | None = None,
        config_path: str | Path | None = None,
    ):
        # Resolve CLI root for finding binaries and config
        if cli_root is not None:
            self._cli_root = Path(cli_root)
        else:
            # Default: assume standard project layout
            self._cli_root = Path(__file__).resolve().parents[3] / "CLI"

        self._cli_bin = self._cli_root / "bin"

        if config_path is not None:
            self._config_path = Path(config_path)
        else:
            self._config_path = self._cli_root / "etc" / "cursor_config.json"

        self._running = True
        self._standby_pool: StandbyPool | None = None
        self._standby_config: StandbyConfig | None = None
        self._standby_monitor_thread: threading.Thread | None = None
        self._dispatch_log: list[dict[str, Any]] = []
        self._dispatch_log_lock = threading.Lock()

        # Optional components -- initialise only when available
        self._iterm: Any = None
        self._heartbeat: Any = None
        self._discovery: Any = None
        self._init_optional_components()

    # -- optional component init --------------------------------------------

    def _init_optional_components(self) -> None:
        """Best-effort initialisation of iTerm, heartbeat, and discovery."""
        if ItermController is not None:
            try:
                self._iterm = ItermController()
            except Exception:
                logger.debug("ItermController unavailable")

        if HeartbeatManager is not None:
            try:
                self._heartbeat = HeartbeatManager()
            except Exception:
                logger.debug("HeartbeatManager unavailable")

        if SessionDiscovery is not None:
            try:
                kwargs: dict[str, Any] = {}
                if self._iterm is not None:
                    kwargs["iterm_controller"] = self._iterm
                self._discovery = SessionDiscovery(**kwargs)
            except Exception:
                logger.debug("SessionDiscovery unavailable")

    # -----------------------------------------------------------------------
    # Configuration helpers
    # -----------------------------------------------------------------------

    def _read_config(self) -> dict:
        """Read cursor_config.json with safe fallback defaults."""
        defaults: dict[str, Any] = {
            "enabled": True,
            "model_override": None,
            "default_model": "composer-1.5",
            "allowed_models": ["composer-1.5"],
            "composer_only": True,
            "credit_target_monthly": 100,
        }
        if not self._config_path.exists():
            return defaults
        try:
            with open(self._config_path) as f:
                data = json.load(f)
            for k, v in defaults.items():
                data.setdefault(k, v)
            return data
        except Exception:
            return defaults

    def _write_config(self, config: dict) -> None:
        """Write cursor_config.json, creating parent dirs if needed."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")

    @property
    def dispatch_mode(self) -> str:
        """Current dispatch mode: 'native', 'local', or 'api'."""
        return read_dispatch_mode(str(self._config_path))

    @property
    def dispatch_backend_label(self) -> str:
        """Human-readable label for the current dispatch backend."""
        return backend_label(self.dispatch_mode)

    # -----------------------------------------------------------------------
    # Command / binary builders
    # -----------------------------------------------------------------------

    @staticmethod
    def _build_cursor_cmd(
        cursor_bin: str, model_id: str, prompt: str, work_dir: Path
    ) -> list[str]:
        """Build cursor-agent command line, handling cursor shim variants."""
        cmd: list[str] = [cursor_bin]
        if Path(cursor_bin).name == "cursor":
            cmd.append("agent")
        cmd += [
            "--trust",
            "--print",
            "--output-format", "text",
            "--workspace", str(work_dir),
            "--model", model_id,
            prompt,
        ]
        return cmd

    def _enrich_prompt(
        self,
        prompt: str,
        work_dir: Path,
        backend: str = "cursor-native",
    ) -> str:
        """Render prompt through the shared adapter contract."""
        _backend_to_dispatch = {
            "cursor-native": DISPATCH_NATIVE,
            "cursor-api": DISPATCH_API,
            "claude-local": DISPATCH_LOCAL,
        }
        dm = _backend_to_dispatch.get(backend, DISPATCH_NATIVE)
        return render_prompt_for_backend(
            str(self._cli_root), prompt, dm, work_dir,
        )

    def _build_native_cmd(
        self, model_id: str, task: str
    ) -> tuple[list[str], dict[str, str]]:
        """Build a native Cursor agent command."""
        env = make_clean_env()
        native_bin = resolve_native_agent()
        if not native_bin:
            return [], env
        work_dir = Path.cwd()
        enriched = self._enrich_prompt(task, work_dir, "cursor-native")
        cmd = self._build_cursor_cmd(native_bin, model_id, enriched, work_dir)
        return cmd, env

    def _build_local_cmd(
        self, model_id: str, task: str
    ) -> tuple[list[str], dict[str, str]]:
        """Build a Claude CLI command for local dispatch mode."""
        env = make_clean_env()
        claude_bin = resolve_claude_bin()
        if not claude_bin:
            return [], env
        model_args = map_cursor_model_to_claude(model_id)
        enriched = self._enrich_prompt(task, Path.cwd(), "claude-local")
        cmd = (
            [claude_bin, "--dangerously-skip-permissions"]
            + model_args
            + ["-p", enriched]
        )
        return cmd, env

    def _build_api_cmd(
        self, model_id: str, task: str
    ) -> tuple[list[str], dict[str, str]]:
        """Build a cursor-wrapper API command (permanently removed)."""
        # API mode removed -- always returns empty command
        return [], make_clean_env()

    def _build_dispatch_cmd(
        self, model_id: str, task: str
    ) -> tuple[list[str], dict[str, str]]:
        """Build command based on current dispatch_mode."""
        dm = self.dispatch_mode
        if dm == DISPATCH_NATIVE:
            return self._build_native_cmd(model_id, task)
        if dm == DISPATCH_API:
            return self._build_api_cmd(model_id, task)
        return self._build_local_cmd(model_id, task)

    # -----------------------------------------------------------------------
    # Core routing: route_task
    # -----------------------------------------------------------------------

    def route_task(
        self,
        task: str,
        model: str | None = None,
        stream: bool = False,
        on_output: Callable[[str], None] | None = None,
    ) -> DispatchResult:
        """Route a task to the best available agent.

        This is the primary entry point.  It selects the swarm tier,
        enforces allowed_models, builds the subprocess command, runs
        it, and returns a ``DispatchResult``.

        Args:
            task: Natural-language task description.
            model: Override model ID.  When ``None`` the swarm tier
                classifier picks one automatically.
            stream: If True, output is streamed line-by-line via
                *on_output*.  Otherwise output is collected and returned
                in ``DispatchResult.stdout``.
            on_output: Callback invoked with each output line when
                *stream* is True.

        Returns:
            DispatchResult with exit code, stdout, timing, etc.
        """
        if not task.strip():
            return DispatchResult(success=False, error="Empty task")

        # Select tier / model
        if model is None:
            tier_name, model_id = self._classify_swarm_tier(task)
            model_id = self._enforce_allowed_models(model_id)
        else:
            tier_name = "custom"
            model_id = model

        # Try pool dispatch first if available.
        # NOTE: success=True here means "task accepted by pool" (async),
        # NOT "task completed".  Callers should check slot_id and poll
        # the pool for completion status.
        if self._standby_pool is not None:
            slot_id = self._standby_pool.dispatch_auto(task, model_id=model_id)
            if slot_id:
                self._log_event(
                    "pool_dispatch", task=task, slot=slot_id, tier=tier_name
                )
                return DispatchResult(
                    success=True,
                    slot_id=slot_id,
                    model=model_id,
                    tier=tier_name,
                    backend=self.dispatch_mode,
                )

        # Direct dispatch via subprocess
        dm = self.dispatch_mode
        cmd, env = self._build_dispatch_cmd(model_id, task)
        if not cmd:
            return DispatchResult(
                success=False,
                error=f"Agent binary not found for dispatch_mode={dm}",
                model=model_id,
                backend=dm,
                tier=tier_name,
            )

        label = backend_label(dm)
        logger.info(
            "Dispatching task via %s, model=%s, tier=%s", label, model_id, tier_name
        )

        t0 = time.monotonic()
        result = self._run_subprocess(
            cmd, env, stream=stream, on_output=on_output
        )
        elapsed = time.monotonic() - t0

        dispatch_result = DispatchResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout or "",
            pid=getattr(result, "pid", None),
            model=model_id,
            backend=dm,
            tier=tier_name,
            duration_secs=round(elapsed, 2),
        )
        self._log_event(
            "dispatch",
            task=task[:120],
            model=model_id,
            tier=tier_name,
            mode=dm,
            exit_code=result.returncode,
            duration=dispatch_result.duration_secs,
        )
        return dispatch_result

    # -----------------------------------------------------------------------
    # dispatch_to_agent -- explicit agent targeting
    # -----------------------------------------------------------------------

    def dispatch_to_agent(
        self,
        agent_id: str,
        task: str,
        model_id: str = "composer-1.5",
    ) -> bool:
        """Dispatch a task to a specific standby-pool agent slot.

        The pool must be initialised first via ``init_pool()``.

        Args:
            agent_id: Slot name, e.g. ``"composer-1"``.
            task: Task description.

        Returns:
            True if the task was accepted by the pool.
        """
        if self._standby_pool is None:
            logger.warning(
                "Standby pool not initialised; call init_pool() first"
            )
            return False
        return self._standby_pool.dispatch(agent_id, task, model_id=model_id)

    def dispatch_auto(
        self,
        task: str,
        prefer_type: str = "auto",
        model_id: str = "composer-1.5",
    ) -> str | None:
        """Auto-select a standby agent and dispatch.

        Returns the agent_id that accepted the task, or None.
        """
        if self._standby_pool is None:
            logger.warning(
                "Standby pool not initialised; call init_pool() first"
            )
            return None
        return self._standby_pool.dispatch_auto(task, prefer_type, model_id=model_id)

    # -----------------------------------------------------------------------
    # Swarm / triad execution
    # -----------------------------------------------------------------------

    def run_swarm(
        self,
        task: str,
        num_workers: int = 3,
        model: str | None = None,
    ) -> SwarmResult:
        """Run a task across multiple parallel workers.

        Each worker receives the same command.  Results are collected
        and returned as a ``SwarmResult``.
        """
        if model is None:
            tier_name, model_id = self._classify_swarm_tier(task)
            model_id = self._enforce_allowed_models(model_id)
        else:
            tier_name = "custom"
            model_id = model

        cfg = self._read_config()
        task_timeout: int = int(cfg.get("task_timeout", 600))

        cmd, env = self._build_dispatch_cmd(model_id, task)
        if not cmd:
            return SwarmResult(
                workers=[
                    DispatchResult(
                        success=False, error="Agent binary not found"
                    )
                ],
                tier=tier_name,
                model=model_id,
            )

        procs: list[tuple[str, subprocess.Popen]] = []
        try:
            for i in range(num_workers):
                p = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )
                procs.append((f"Worker-{i + 1}", p))
        except Exception as exc:
            # Kill all already-started processes on partial spawn failure
            for _, started_proc in procs:
                try:
                    started_proc.kill()
                    started_proc.wait(timeout=5)
                except Exception:
                    pass
            return SwarmResult(
                workers=[
                    DispatchResult(
                        success=False,
                        error=f"Spawn failure: {exc}",
                    )
                ],
                tier=tier_name,
                model=model_id,
            )

        logger.info(
            "Swarm: %d workers launched (tier=%s, model=%s, pids=%s)",
            num_workers,
            tier_name,
            model_id,
            ", ".join(str(p.pid) for _, p in procs),
        )

        def _wait(name: str, proc: subprocess.Popen) -> DispatchResult:
            t0 = time.monotonic()
            try:
                stdout, _ = proc.communicate(timeout=task_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                elapsed = time.monotonic() - t0
                return DispatchResult(
                    success=False,
                    exit_code=124,
                    stdout="",
                    pid=proc.pid,
                    model=model_id,
                    backend=self.dispatch_mode,
                    tier=tier_name,
                    duration_secs=round(elapsed, 2),
                    error=f"Worker {name} timed out after {task_timeout}s",
                )
            elapsed = time.monotonic() - t0
            return DispatchResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=stdout or "",
                pid=proc.pid,
                model=model_id,
                backend=self.dispatch_mode,
                tier=tier_name,
                duration_secs=round(elapsed, 2),
            )

        results: list[DispatchResult] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=num_workers
        ) as pool:
            futures = {
                pool.submit(_wait, name, proc): name
                for name, proc in procs
            }
            for fut in concurrent.futures.as_completed(futures):
                try:
                    results.append(fut.result(timeout=10))
                except TimeoutError:
                    worker_name = futures[fut]
                    results.append(DispatchResult(
                        success=False,
                        exit_code=124,
                        error=f"Future for {worker_name} timed out",
                        model=model_id,
                        backend=self.dispatch_mode,
                        tier=tier_name,
                    ))

        return SwarmResult(workers=results, tier=tier_name, model=model_id)

    def run_triad(
        self,
        task: str,
        models: list[str] | None = None,
    ) -> TriadResult:
        """Execute a 3-leg triad (code gen, strategy, review).

        Args:
            task: Base task description.
            models: List of exactly 3 model IDs.  Defaults to
                ``["composer-1.5", "composer-1.5", "composer-1.5"]``.

        Returns:
            TriadResult with per-leg outcomes.
        """
        if models is None:
            models = ["composer-1.5"] * 3

        if len(models) != 3:
            return TriadResult(legs=[
                DispatchResult(
                    success=False,
                    error="Triad requires exactly 3 models",
                )
            ])

        cfg = self._read_config()
        task_timeout: int = int(cfg.get("task_timeout", 600))

        roles = [
            ("CODE GENERATION", models[0]),
            ("STRATEGY & ARCHITECTURE", models[1]),
            ("REVIEW & OPTIMIZE", models[2]),
        ]

        procs: list[tuple[str, str, subprocess.Popen]] = []
        try:
            for role, model_id in roles:
                suffixed = f"{role}: {task}"
                cmd, env = self._build_dispatch_cmd(model_id, suffixed)
                if not cmd:
                    raise RuntimeError(f"Binary not found for leg: {role}")
                p = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )
                procs.append((role, model_id, p))
        except Exception as exc:
            # Kill all already-started processes on partial spawn failure
            for _, _, started_proc in procs:
                try:
                    started_proc.kill()
                    started_proc.wait(timeout=5)
                except Exception:
                    pass
            return TriadResult(legs=[
                DispatchResult(
                    success=False,
                    error=f"Spawn failure: {exc}",
                )
            ])

        logger.info(
            "Triad: 3 legs launched (pids=%s)",
            ", ".join(str(p.pid) for _, _, p in procs),
        )

        def _wait(
            role: str, model_id: str, proc: subprocess.Popen
        ) -> DispatchResult:
            t0 = time.monotonic()
            try:
                stdout, _ = proc.communicate(timeout=task_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                elapsed = time.monotonic() - t0
                return DispatchResult(
                    success=False,
                    exit_code=124,
                    stdout="",
                    pid=proc.pid,
                    model=model_id,
                    backend=self.dispatch_mode,
                    tier=role,
                    duration_secs=round(elapsed, 2),
                    error=f"Triad leg {role} timed out after {task_timeout}s",
                )
            elapsed = time.monotonic() - t0
            return DispatchResult(
                success=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=stdout or "",
                pid=proc.pid,
                model=model_id,
                backend=self.dispatch_mode,
                tier=role,
                duration_secs=round(elapsed, 2),
            )

        legs: list[DispatchResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(_wait, role, mid, proc): role
                for role, mid, proc in procs
            }
            for fut in concurrent.futures.as_completed(futures):
                try:
                    legs.append(fut.result(timeout=10))
                except TimeoutError:
                    leg_role = futures[fut]
                    legs.append(DispatchResult(
                        success=False,
                        exit_code=124,
                        error=f"Future for triad leg {leg_role} timed out",
                        tier=leg_role,
                    ))

        return TriadResult(legs=legs)

    # -----------------------------------------------------------------------
    # Status / listing
    # -----------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return a status snapshot of the router and its components.

        Includes dispatch mode, pool summary, heartbeat health summary,
        discovered sessions, and recent dispatch history.
        """
        status: dict[str, Any] = {
            "dispatch_mode": self.dispatch_mode,
            "backend_label": self.dispatch_backend_label,
            "config_path": str(self._config_path),
            "running": self._running,
            "pool": None,
            "health_summary": None,
            "sessions": [],
            "recent_dispatches": len(self._dispatch_log),
        }

        if self._standby_pool is not None:
            status["pool"] = self._standby_pool.summary()

        if self._heartbeat is not None:
            try:
                status["health_summary"] = self._heartbeat.get_health_summary()
            except Exception:
                pass

        if self._discovery is not None:
            try:
                sessions = self._discovery.discover_sessions()
                status["sessions"] = [
                    {
                        "session_id": s.session_id,
                        "window_number": s.window_number,
                        "window_name": s.window_name,
                        "state": (
                            s.state
                            if isinstance(s.state, str)
                            else s.state.value
                        ),
                        "context_pct": s.context_pct,
                        "is_responsive": s.is_responsive,
                    }
                    for s in sessions
                ]
            except Exception:
                pass

        return status

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agent slots from the standby pool.

        Returns a list of dicts with id, type, state, pid,
        current_task, and tasks_completed for each slot.
        """
        if self._standby_pool is not None:
            return self._standby_pool.summary()

        # Fallback: try heartbeat manager for live session data
        if self._heartbeat is not None:
            try:
                summary = self._heartbeat.get_health_summary()
                return summary.get("sessions", [])
            except Exception:
                pass

        return []

    def get_agent_result(self, agent_id: str) -> str | None:
        """Retrieve the last output from a pool agent slot."""
        if self._standby_pool is None:
            return None
        return self._standby_pool.get_last_result(agent_id)

    # -----------------------------------------------------------------------
    # Pool lifecycle
    # -----------------------------------------------------------------------

    def init_pool(self, config: StandbyConfig | None = None) -> None:
        """Initialise (or reinitialise) the standby agent pool."""
        if config is None:
            config = StandbyConfig()
        self._standby_config = config
        self._standby_pool = StandbyPool(
            config,
            self._cli_bin,
            config_path=self._config_path,
        )
        logger.info(
            "Standby pool initialised with %d slots",
            len(self.list_agents()),
        )

    def shutdown_pool(self) -> None:
        """Gracefully shut down the standby pool, killing busy agents."""
        if self._standby_pool is not None:
            self._standby_pool.shutdown()
            self._standby_pool = None
            self._standby_config = None

    def recall_agent(self, agent_id: str) -> bool:
        """Kill a running task and return the agent slot to standby."""
        if self._standby_pool is None:
            return False
        return self._standby_pool.recall(agent_id)

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def shutdown(self) -> None:
        """Full shutdown: pool, monitoring threads, flag."""
        self._running = False
        self.shutdown_pool()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _classify_swarm_tier(self, task: str) -> tuple[str, str]:
        """Score task against swarm tiers and return (tier_name, model_id).

        Uses keyword matching.  Time-sensitive tasks are upgraded to
        the ``-fast`` variant when available.
        """
        task_lower = task.lower()
        best_tier = "standard"
        best_score = 0

        time_sensitive = any(
            kw in task_lower
            for kw in ("urgent", "fast", "emergency", "deadline", "hotfix")
        )

        for tier_name, tier_cfg in SWARM_TIERS.items():
            score = sum(1 for kw in tier_cfg["keywords"] if kw in task_lower)
            if score > best_score:
                best_score = score
                best_tier = tier_name

        if best_score == 0:
            best_tier = "fast" if time_sensitive else "standard"
        elif time_sensitive and "-fast" not in best_tier:
            fast_variant = best_tier + "-fast"
            if fast_variant in SWARM_TIERS:
                best_tier = fast_variant

        return best_tier, SWARM_TIERS[best_tier]["model"]

    def _enforce_allowed_models(self, model_id: str) -> str:
        """Clamp model_id to allowed_models from config.

        Returns composer-1.5 if the requested model is disallowed.
        """
        try:
            cfg = self._read_config()
            if cfg.get("composer_only", False):
                return "composer-1.5"
            allowed = cfg.get("allowed_models", [])
            if allowed and model_id not in allowed:
                logger.info(
                    "Model %s not in allowed_models, falling back to composer-1.5",
                    model_id,
                )
                return "composer-1.5"
        except Exception:
            pass
        return model_id

    def _log_event(self, event: str, **kwargs: Any) -> None:
        """Append to the internal dispatch log (ring buffer, max 100).

        Thread-safe: protected by ``_dispatch_log_lock`` so concurrent
        swarm/triad workers can safely log without data races.
        """
        entry = {"event": event, "time": time.time(), **kwargs}
        with self._dispatch_log_lock:
            self._dispatch_log.append(entry)
            if len(self._dispatch_log) > 100:
                self._dispatch_log = self._dispatch_log[-50:]

    @staticmethod
    def _run_subprocess(
        cmd: list[str],
        env: dict[str, str],
        stream: bool = False,
        on_output: Callable[[str], None] | None = None,
        timeout: int = 600,
    ) -> subprocess.CompletedProcess:
        """Run a command, optionally streaming stdout line-by-line.

        When *stream* is True each line is forwarded to *on_output*
        (or printed to stdout).  When False the process runs to
        completion and stdout is returned in the result.
        """
        if stream:
            # NOTE: Stream mode does not enforce the timeout parameter
            # because output is consumed line-by-line and the caller
            # expects real-time delivery.  A threading.Timer is used as
            # a safety net to kill the process if it exceeds the timeout,
            # preventing indefinite hangs.
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            timed_out = False

            def _kill_on_timeout() -> None:
                nonlocal timed_out
                timed_out = True
                try:
                    proc.kill()
                except OSError:
                    pass

            timer = threading.Timer(timeout, _kill_on_timeout)
            timer.start()
            lines: list[str] = []
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    lines.append(line)
                    if on_output is not None:
                        on_output(line)
                    else:
                        print(line, end="")
            except Exception:
                pass
            finally:
                timer.cancel()
            proc.wait()
            rc = 124 if timed_out else proc.returncode
            return subprocess.CompletedProcess(
                cmd, rc, stdout="".join(lines),
                stderr="Stream timed out" if timed_out else "",
            )
        else:
            try:
                return subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return subprocess.CompletedProcess(
                    cmd, returncode=124, stdout="", stderr="Timed out"
                )
