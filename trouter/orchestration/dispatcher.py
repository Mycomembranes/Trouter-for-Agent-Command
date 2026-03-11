"""
Computation Dispatcher
======================

Routes computational tasks to specialist agents based on domain detection
and executes them in parallel using terminal, MCP, or background modes.
"""

import os
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

from trouter.orchestration.config import OrchestrationConfig


@dataclass
class DispatchResult:
    """Result of a dispatched computation."""

    success: bool
    domain: str
    tasks_executed: int
    output_dir: Optional[str] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0


class ComputationDispatcher:
    """
    Routes computational tasks to specialist agents for parallel execution.

    Supports three execution modes:
    - Terminal mode: Workers run in visible terminal windows
    - MCP mode: Workers coordinate via rotifer-mcp server
    - Background mode: Workers run in tmux sessions

    Usage:
        dispatcher = ComputationDispatcher()
        result = dispatcher.dispatch(
            task="cluster 500 domain architectures",
            parallel=4,
            terminal_mode=True
        )
    """

    def __init__(self, config: Optional[OrchestrationConfig] = None):
        """Initialize dispatcher with configuration."""
        self.config = config or OrchestrationConfig.from_env()
        self._cli_root = self.config.cli_root

    def dispatch(
        self,
        task: str,
        parallel: int = 4,
        terminal_mode: bool = False,
        mcp_mode: bool = False,
        output_dir: Optional[str] = None,
        domain: str = "auto",
        stagger_seconds: float = 0.0,
    ) -> DispatchResult:
        """
        Dispatch a computational task to specialist agents.

        Args:
            task: The computation task description
            parallel: Number of parallel workers
            terminal_mode: Run workers in visible terminal windows
            mcp_mode: Use MCP server for coordination
            output_dir: Directory for output files
            domain: Force domain (auto, clustering, network, sequence, ncbi)
            stagger_seconds: Delay between worker starts (for I/O-bound tasks)

        Returns:
            DispatchResult with execution status and output location
        """
        import time
        start_time = time.time()

        # Detect domain if auto
        detected_domain = domain if domain != "auto" else self.config.detect_domain(task)

        # Get recommended stagger for domain if not specified
        if stagger_seconds == 0.0:
            stagger_seconds = self.config.get_rate_limit(detected_domain)

        # Create output directory
        if output_dir is None:
            output_dir = os.path.join(self.config.output_dir, f"dispatch_{int(time.time())}")
        os.makedirs(output_dir, exist_ok=True)

        # Generate tasks for the domain
        tasks = self._generate_tasks(task, detected_domain, parallel)

        # Ensure MCP server if needed
        if mcp_mode:
            self._ensure_mcp_server()

        # Build and execute command
        try:
            cmd = self._build_command(
                tasks=tasks,
                parallel=parallel,
                terminal_mode=terminal_mode,
                output_dir=output_dir,
                stagger_seconds=stagger_seconds,
            )

            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
            )

            duration = time.time() - start_time

            if result.returncode == 0:
                return DispatchResult(
                    success=True,
                    domain=detected_domain,
                    tasks_executed=len(tasks),
                    output_dir=output_dir,
                    duration_seconds=duration,
                )
            else:
                return DispatchResult(
                    success=False,
                    domain=detected_domain,
                    tasks_executed=0,
                    error=result.stderr,
                    duration_seconds=duration,
                )

        except subprocess.TimeoutExpired:
            return DispatchResult(
                success=False,
                domain=detected_domain,
                tasks_executed=0,
                error="Execution timeout (1 hour)",
                duration_seconds=3600.0,
            )
        except Exception as e:
            return DispatchResult(
                success=False,
                domain=detected_domain,
                tasks_executed=0,
                error=str(e),
                duration_seconds=time.time() - start_time,
            )

    def _generate_tasks(self, task: str, domain: str, parallel: int) -> List[str]:
        """Generate task list based on domain and parallelization."""
        tasks = []

        if domain == "network":
            # Parallel community detection algorithms
            if "community" in task.lower():
                tasks.append(f"codex: Run Louvain community detection. Task: {task}")
                tasks.append(f"codex: Run Leiden CPM community detection. Task: {task}")
                tasks.append(f"cursor: Run Leiden Modularity community detection. Task: {task}")
            elif "layout" in task.lower():
                tasks.append(f"codex: Calculate spring layout. Task: {task}")
                tasks.append(f"codex: Calculate kamada-kawai layout. Task: {task}")
                tasks.append(f"cursor: Calculate circular layout. Task: {task}")
            else:
                tasks.append(f"codex: Analyze network structure. Task: {task}")
                tasks.append(f"cursor: Validate network analysis. Task: {task}")

        elif domain == "clustering":
            # Batch row processing
            batch_size = max(1, 1000 // parallel)
            for i in range(parallel):
                start = i * batch_size + 1
                end = (i + 1) * batch_size
                tasks.append(f"codex: Process rows {start}-{end}. Task: {task}")

        elif domain == "ncbi":
            # Staggered NCBI fetches
            for i in range(parallel):
                tasks.append(f"codex: Fetch batch {i+1}/{parallel} from NCBI. Task: {task}")

        elif domain == "sequence":
            # Parallel sequence processing
            for i in range(parallel):
                tasks.append(f"codex: Process sequence batch {i+1}/{parallel}. Task: {task}")

        else:
            # General task
            tasks.append(f"auto: {task}")

        return tasks

    def _build_command(
        self,
        tasks: List[str],
        parallel: int,
        terminal_mode: bool,
        output_dir: str,
        stagger_seconds: float,
    ) -> str:
        """Build the parallel-agents command."""
        cmd_parts = [f'"{self._cli_root}/bin/parallel-agents"']

        if terminal_mode:
            cmd_parts.append("--terminal")

        cmd_parts.append(f"--max-parallel {parallel}")
        cmd_parts.append(f'--output "{output_dir}"')

        for task in tasks:
            cmd_parts.append(f'--task "{task}"')

        return " ".join(cmd_parts)

    def _ensure_mcp_server(self) -> bool:
        """Ensure MCP server is running."""
        mcp_script = os.path.join(self._cli_root, "bin", "rotifer-mcp")

        if not os.path.exists(mcp_script):
            return False

        # Check status
        result = subprocess.run(
            [mcp_script, "status"],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            # Start server
            subprocess.run(
                [mcp_script, "start"],
                capture_output=True,
                text=True,
            )
            import time
            time.sleep(2)

        return True

    def get_agent_for_domain(self, domain: str) -> str:
        """Get the specialist agent name for a domain."""
        agent_map = {
            "clustering": "clustering-advisor",
            "network": "network-guide",
            "sequence": "sequence-specialist",
            "ncbi": "ncbi-fetcher",
            "general": "multi-agent-orchestrator",
        }
        return agent_map.get(domain, "multi-agent-orchestrator")

    def list_active_dispatches(self) -> List[Dict]:
        """List currently active dispatch operations."""
        output_dir = self.config.output_dir
        active = []

        if os.path.exists(output_dir):
            for item in os.listdir(output_dir):
                item_path = os.path.join(output_dir, item)
                if os.path.isdir(item_path) and item.startswith("dispatch_"):
                    # Check if still running (has .running marker)
                    running_marker = os.path.join(item_path, ".running")
                    active.append({
                        "id": item,
                        "path": item_path,
                        "running": os.path.exists(running_marker),
                    })

        return active
