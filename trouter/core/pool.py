"""
Standby Agent Pool — pre-warmed agent slots for dispatch.

Extracted from terminal_router.py L206-484.
"""

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from trouter.core.dispatch import (
    DISPATCH_NATIVE,
    DISPATCH_API,
    make_clean_env,
    map_cursor_model_to_claude,
    read_dispatch_mode,
    resolve_claude_bin,
    resolve_native_agent,
)
from trouter.core.config import find_config_path
from trouter.core.models import AgentState


@dataclass
class StandbyAgent:
    """A single agent slot in the standby pool."""

    id: str
    agent_type: str  # "codex", "claude", "composer"
    state: AgentState = AgentState.STANDBY
    pid: int | None = None
    proc: subprocess.Popen | None = None
    current_task: str | None = None
    current_model: str | None = None
    tasks_completed: int = 0
    last_output: str | None = None
    error_time: float | None = None


@dataclass
class StandbyConfig:
    """Configuration for the standby pool."""

    codex_slots: int = 0          # Minimize usage: composer-only
    claude_slots: int = 0         # Minimize usage: composer-only
    composer_slots: int = 2       # Primary agent pool
    check_interval: int = 10
    auto_compact: bool = True
    compact_threshold: int = 20
    task_timeout: int = 600


class StandbyPool:
    """Manages a pool of agent slots for standby mode dispatch."""

    def __init__(
        self,
        config: StandbyConfig,
        cli_bin: Path,
        config_path: Path | None = None,
    ):
        self._config = config
        self._cli_bin = cli_bin
        self._config_path = Path(config_path) if config_path else find_config_path()
        self._lock = threading.Lock()
        self._slots: dict[str, StandbyAgent] = {}
        self._shutting_down = False
        self._init_slots()

    def _init_slots(self):
        for i in range(1, self._config.codex_slots + 1):
            aid = f"codex-{i}"
            self._slots[aid] = StandbyAgent(id=aid, agent_type="codex")
        for i in range(1, self._config.claude_slots + 1):
            aid = f"claude-{i}"
            self._slots[aid] = StandbyAgent(id=aid, agent_type="claude")
        for i in range(1, self._config.composer_slots + 1):
            aid = f"composer-{i}"
            self._slots[aid] = StandbyAgent(id=aid, agent_type="composer")

    def _read_dispatch_mode(self) -> str:
        """Read dispatch_mode from cursor_config.json."""
        return read_dispatch_mode(str(self._config_path))

    @staticmethod
    def _find_native_agent() -> str:
        """Find the real Cursor agent binary."""
        return resolve_native_agent()

    def _composer_only(self) -> bool:
        """Return True if cursor_config.json has composer_only."""
        cfg = self._config_path
        if not cfg.exists():
            return True
        try:
            with open(cfg) as f:
                return json.load(f).get("composer_only", True)
        except Exception:
            return True

    @staticmethod
    def _composer_env() -> dict[str, str]:
        """Build clean env with CLAUDECODE stripped."""
        return make_clean_env()

    @staticmethod
    def _resolve_claude_bin() -> str:
        """Find the Claude Code CLI binary."""
        return resolve_claude_bin()

    @staticmethod
    def _map_model_to_claude(cursor_model: str) -> list[str]:
        """Map cursor model tier to claude --model flag."""
        return map_cursor_model_to_claude(cursor_model)

    def _build_native_cmd(
        self, model_id: str, task: str
    ) -> tuple[list[str], dict[str, str]]:
        """Build a native Cursor agent command."""
        env = make_clean_env()
        native_bin = self._find_native_agent()
        if not native_bin:
            return [], env
        cmd = [
            native_bin, "--print", "--trust",
            "--workspace", str(Path.cwd()),
            "--model", model_id,
            "--output-format", "text", task,
        ]
        return cmd, env

    def _build_cmd(
        self, agent_type: str, task: str, model_id: str
    ) -> tuple[list[str], dict[str, str]]:
        """Build dispatch command based on config mode."""
        env = make_clean_env()
        dispatch = self._read_dispatch_mode()

        if dispatch == DISPATCH_NATIVE:
            return self._build_native_cmd(model_id, task)

        if dispatch == DISPATCH_API:
            # API mode permanently removed
            return [], self._composer_env()

        # Local fallback (Claude CLI)
        claude_bin = self._resolve_claude_bin()
        if not claude_bin:
            return [], env
        model_args = self._map_model_to_claude(model_id)
        return (
            [claude_bin, "--dangerously-skip-permissions"] + model_args + ["-p", task],
            env,
        )

    def dispatch(self, agent_id: str, task: str, model_id: str = "composer-1.5") -> bool:
        """Dispatch a task to a specific agent slot."""
        with self._lock:
            if self._shutting_down:
                return False
            slot = self._slots.get(agent_id)
            if not slot:
                return False
            if slot.state != AgentState.STANDBY:
                return False
            slot.state = AgentState.BUSY
            slot.current_task = task
            slot.current_model = model_id
            slot.last_output = None
        t = threading.Thread(
            target=self._run_agent_task,
            args=(agent_id, task, model_id),
            daemon=True,
        )
        t.start()
        return True

    def dispatch_auto(
        self,
        task: str,
        prefer_type: str = "auto",
        model_id: str = "composer-1.5",
    ) -> str | None:
        """Auto-select an available agent and dispatch."""
        selected_aid: str | None = None
        with self._lock:
            if self._shutting_down:
                return None
            if prefer_type != "auto":
                for aid, slot in self._slots.items():
                    if slot.agent_type == prefer_type and slot.state == AgentState.STANDBY:
                        slot.state = AgentState.BUSY
                        slot.current_task = task
                        slot.current_model = model_id
                        slot.last_output = None
                        selected_aid = aid
                        break
                if selected_aid is None:
                    return None
            else:
                # auto: prefer composer, then codex, then claude
                for pref in ("composer", "codex", "claude"):
                    for aid, slot in self._slots.items():
                        if slot.agent_type == pref and slot.state == AgentState.STANDBY:
                            slot.state = AgentState.BUSY
                            slot.current_task = task
                            slot.current_model = model_id
                            slot.last_output = None
                            selected_aid = aid
                            break
                    if selected_aid is not None:
                        break
        if selected_aid is not None:
            t = threading.Thread(
                target=self._run_agent_task,
                args=(selected_aid, task, model_id),
                daemon=True,
            )
            t.start()
        return selected_aid

    def _run_agent_task(self, agent_id: str, task: str, model_id: str) -> None:
        with self._lock:
            agent_type = self._slots[agent_id].agent_type
        cmd, env = self._build_cmd(agent_type, task, model_id)
        if not cmd:
            with self._lock:
                self._slots[agent_id].state = AgentState.ERROR
                self._slots[agent_id].error_time = time.time()
                self._slots[agent_id].last_output = "ERROR: agent binary not found"
                self._slots[agent_id].current_task = None
                self._slots[agent_id].current_model = None
            return
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
            )
            with self._lock:
                self._slots[agent_id].pid = proc.pid
                self._slots[agent_id].proc = proc
            stdout, _ = proc.communicate(timeout=self._config.task_timeout)
            with self._lock:
                slot = self._slots[agent_id]
                if slot.state != AgentState.BUSY:
                    slot.pid = None
                    slot.proc = None
                    return
                if proc.returncode != 0:
                    slot.state = AgentState.ERROR
                    slot.error_time = time.time()
                    slot.last_output = stdout or f"ERROR: process exited with code {proc.returncode}"
                    slot.current_task = None
                    slot.current_model = None
                    slot.pid = None
                    slot.proc = None
                    return
                slot.last_output = stdout
                slot.tasks_completed += 1
                slot.state = AgentState.OFFLINE if self._shutting_down else AgentState.STANDBY
                slot.current_task = None
                slot.current_model = None
                slot.pid = None
                slot.proc = None
        except subprocess.TimeoutExpired:
            if proc is not None:
                try:
                    proc.kill()
                    proc.wait()
                except Exception:
                    pass
            with self._lock:
                self._slots[agent_id].state = AgentState.ERROR
                self._slots[agent_id].error_time = time.time()
                self._slots[agent_id].last_output = "ERROR: task timed out"
                self._slots[agent_id].current_task = None
                self._slots[agent_id].current_model = None
                self._slots[agent_id].pid = None
                self._slots[agent_id].proc = None
        except Exception as e:
            if proc is not None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            with self._lock:
                self._slots[agent_id].state = AgentState.ERROR
                self._slots[agent_id].error_time = time.time()
                self._slots[agent_id].last_output = f"ERROR: {e}"
                self._slots[agent_id].current_task = None
                self._slots[agent_id].current_model = None
                self._slots[agent_id].pid = None
                self._slots[agent_id].proc = None

    def recall(self, agent_id: str) -> bool:
        """Kill a running task and return agent to standby."""
        with self._lock:
            slot = self._slots.get(agent_id)
            if not slot or slot.state != AgentState.BUSY:
                return False
            if slot.proc:
                try:
                    slot.proc.kill()
                    slot.proc.wait(timeout=5)
                except Exception:
                    pass
            slot.state = AgentState.STANDBY
            slot.current_task = None
            slot.current_model = None
            slot.pid = None
            slot.proc = None
            return True

    def reset_error(self, agent_id: str) -> bool:
        """Reset an errored agent to standby."""
        with self._lock:
            slot = self._slots.get(agent_id)
            if not slot or slot.state != AgentState.ERROR:
                return False
            slot.state = AgentState.STANDBY
            slot.error_time = None
            slot.current_model = None
            return True

    def get_last_result(self, agent_id: str) -> str | None:
        """Get last output from an agent."""
        with self._lock:
            slot = self._slots.get(agent_id)
            return slot.last_output if slot else None

    def summary(self) -> list[dict]:
        """Get pool status summary."""
        with self._lock:
            return [
                {
                    "id": aid,
                    "type": slot.agent_type,
                    "state": slot.state.value,
                    "pid": slot.pid,
                    "model": slot.current_model,
                    "current_task": slot.current_task,
                    "tasks_completed": slot.tasks_completed,
                }
                for aid, slot in self._slots.items()
            ]

    def reset_stale_errors(self, cooldown: float = 60.0):
        """Auto-reset agents that have been in ERROR state beyond cooldown."""
        now = time.time()
        with self._lock:
            for slot in self._slots.values():
                if slot.state == AgentState.ERROR and slot.error_time:
                    if now - slot.error_time >= cooldown:
                        slot.state = AgentState.STANDBY
                        slot.error_time = None
                        slot.current_model = None

    def shutdown(self):
        """Graceful shutdown — kill all busy agents."""
        with self._lock:
            self._shutting_down = True
            for slot in self._slots.values():
                if slot.proc and slot.state == AgentState.BUSY:
                    try:
                        slot.proc.kill()
                        slot.proc.wait(timeout=5)
                    except Exception:
                        pass
                    slot.state = AgentState.OFFLINE
                    slot.current_model = None
                    slot.pid = None
                    slot.proc = None
