"""
Orchestration Configuration
============================

Configuration dataclasses for agent orchestration, batch processing,
and memory optimization.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class OrchestrationConfig:
    """Configuration for agent orchestration system."""

    # Worker settings
    max_parallel: int = 4
    stagger_seconds: float = 0.0
    terminal_mode: bool = False
    mcp_mode: bool = False

    # Directory settings
    cli_root: str = ""
    memory_dir: str = ""
    output_dir: str = ""
    work_dir: str = ""

    # Agent families for vocabulary sharing
    agent_families: Dict[str, List[str]] = field(default_factory=lambda: {
        'analysis': ['clustering-advisor', 'network-guide', 'sequence-specialist'],
        'workflow': ['compression-manager', 'multi-agent-orchestrator', 'documentation-keeper'],
        'data': ['ncbi-fetcher', 'domain-vocab', 'pipeline-validator'],
    })

    # Domain-specific settings
    domain_keywords: Dict[str, List[str]] = field(default_factory=lambda: {
        'clustering': ['cluster', 'architecture', 'domain', 'embed', 'encode'],
        'network': ['network', 'community', 'louvain', 'leiden', 'layout', 'graph'],
        'sequence': ['sequence', 'fetch', 'fasta', 'align', 'msa'],
        'ncbi': ['ncbi', 'taxonomy', 'taxid', 'protein', 'pid2tax', 'ipg'],
    })

    # Rate limiting for I/O operations
    rate_limits: Dict[str, float] = field(default_factory=lambda: {
        'ncbi': 2.0,  # 2 second stagger between NCBI requests
        'sequence': 1.0,  # 1 second for sequence operations
        'network': 0.0,  # No stagger for CPU-bound
        'clustering': 0.0,  # No stagger for CPU-bound
    })

    def __post_init__(self):
        """Initialize paths from environment if not set."""
        if not self.cli_root:
            self.cli_root = os.environ.get(
                'CLI_ROOT',
                str(Path(__file__).parent.parent.parent)
            )

        if not self.memory_dir:
            self.memory_dir = os.environ.get(
                'AGENT_MEMORY_DIR',
                os.path.expanduser('~/.claude/agent_memory')
            )

        if not self.output_dir:
            self.output_dir = os.environ.get(
                'AGENT_BACKGROUND_DIR',
                os.path.expanduser('~/.claude/agent-output')
            )

        if not self.work_dir:
            self.work_dir = os.path.expanduser('~/.claude/batch_work')

        # Read from environment
        self.max_parallel = int(os.environ.get('AGENT_MAX_PARALLEL', self.max_parallel))

    @classmethod
    def from_env(cls) -> 'OrchestrationConfig':
        """Create configuration from environment variables."""
        return cls(
            max_parallel=int(os.environ.get('AGENT_MAX_PARALLEL', 4)),
            terminal_mode=os.environ.get('AGENT_TERMINAL_MODE', 'false').lower() == 'true',
            mcp_mode=os.environ.get('AGENT_MCP_MODE', 'false').lower() == 'true',
        )

    def get_rate_limit(self, domain: str) -> float:
        """Get rate limit (stagger) for a domain."""
        return self.rate_limits.get(domain, 0.0)

    def detect_domain(self, task: str) -> str:
        """Detect domain from task description."""
        task_lower = task.lower()

        for domain, keywords in self.domain_keywords.items():
            for keyword in keywords:
                if keyword in task_lower:
                    return domain

        return 'general'

    def get_agent_family(self, agent_name: str) -> Optional[str]:
        """Get the family for an agent name."""
        for family, agents in self.agent_families.items():
            if agent_name in agents:
                return family
        return None


@dataclass
class BatchConfig:
    """Configuration for batch processing."""

    # Batch settings
    batch_size: Optional[int] = None  # Auto-calculate if None
    workers: int = 4
    merge_results: bool = True

    # Rate limiting
    stagger_seconds: float = 0.0

    # Execution mode
    terminal_mode: bool = False
    mcp_mode: bool = False

    # Output settings
    output_file: Optional[str] = None
    output_dir: Optional[str] = None

    # Operation settings
    operation: str = ""
    input_file: str = ""

    def calculate_batch_size(self, total_lines: int) -> int:
        """Calculate optimal batch size."""
        if self.batch_size:
            return self.batch_size
        return (total_lines + self.workers - 1) // self.workers


@dataclass
class MemoryConfig:
    """Configuration for memory optimization."""

    # Memory directory
    memory_dir: str = ""

    # Pruning settings
    prune_age_days: int = 7
    max_sessions_per_agent: int = 100
    min_similarity_threshold: float = 0.3

    # Cache settings
    cache_dir: str = ""
    max_cache_age_hours: int = 24

    def __post_init__(self):
        """Initialize paths."""
        if not self.memory_dir:
            self.memory_dir = os.path.expanduser('~/.claude/agent_memory')
        if not self.cache_dir:
            self.cache_dir = os.path.expanduser('~/.claude/compression_cache')
