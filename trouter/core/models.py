"""
Shared constants and enumerations for the trouter agent system.

Extracted from terminal_router.py: SWARM_TIERS, COMMANDS, ANSI helpers.
"""

from enum import Enum


# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


class AgentState(Enum):
    """Agent slot lifecycle state."""

    STANDBY = "STANDBY"
    BUSY = "BUSY"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"


# ---------------------------------------------------------------------------
# Swarm tier configuration — keyword-based model routing
# ---------------------------------------------------------------------------

SWARM_TIERS = {
    "low-fast": {
        "model": "composer-1.5",
        "keywords": ["prototype", "experiment", "throwaway", "one-liner", "trivial", "scratch"],
    },
    "low": {
        "model": "composer-1.5",
        "keywords": ["fix", "typo", "rename", "simple", "boilerplate", "template"],
    },
    "fast": {
        "model": "composer-1.5",
        "keywords": ["quick", "tweak", "parameter", "adjust", "iterate", "inline"],
    },
    "standard": {
        "model": "composer-1.5",
        "keywords": ["implement", "generate", "write", "create", "add"],
    },
    "high-fast": {
        "model": "gpt-5.3-codex-high-fast",
        "keywords": ["urgent complex", "fast quality", "time-sensitive", "deadline", "hotfix"],
    },
    "high": {
        "model": "gpt-5.3-codex-high",
        "keywords": [
            "refactor", "multi-file", "complex", "architecture",
            "design", "algorithm", "optimize",
        ],
    },
    "xhigh-fast": {
        "model": "gpt-5.3-codex-xhigh-fast",
        "keywords": ["emergency", "urgent critical", "security hotfix", "production emergency"],
    },
    "xhigh": {
        "model": "gpt-5.3-codex-xhigh",
        "keywords": ["critical", "security", "audit", "production", "safety"],
    },
}

# ---------------------------------------------------------------------------
# Available REPL commands
# ---------------------------------------------------------------------------

COMMANDS = [
    "status", "send", "send-to", "discover", "compact", "compact-all",
    "dispatch", "composer", "triad", "swarm", "broadcast", "kill", "restart",
    "dispatch-mode",
    "health", "watch", "output", "help", "quit",
    "standby", "pool", "assign", "recall", "agents", "dashboard",
    "credits", "cursor-model", "cursor-disable", "cursor-enable",
    "cursor-verify", "cursor-enforce", "cursor-lock", "cursor-unlock", "cursor-flexible",
    "mcp-status", "mcp-start", "mcp-stop", "mcp-restart", "mcp-check", "mcp-logs",
]


def select_swarm_tier(task: str) -> tuple[str, str]:
    """Select the best swarm tier for a task based on keyword matching.

    Returns:
        (tier_name, model_id) tuple.
    """
    task_lower = task.lower()

    # Check from highest to lowest priority
    for tier_name in ("xhigh-fast", "xhigh", "high-fast", "high", "fast", "low-fast", "low"):
        tier = SWARM_TIERS[tier_name]
        for keyword in tier["keywords"]:
            if keyword in task_lower:
                return tier_name, tier["model"]

    # Default to standard
    return "standard", SWARM_TIERS["standard"]["model"]
