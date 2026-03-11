"""
Configuration management for trouter.

Reads cursor_config.json and provides typed access to settings.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TrouterConfig:
    """Unified trouter configuration."""

    dispatch_mode: str = "native"
    enabled: bool = True
    model_override: str = ""
    default_model: str = "composer-1.5"
    allowed_models: list[str] = field(default_factory=lambda: [
        "composer-1.5",
        "gpt-5.3-codex", "gpt-5.3-codex-low", "gpt-5.3-codex-low-fast",
        "gpt-5.3-codex-fast", "gpt-5.3-codex-high", "gpt-5.3-codex-high-fast",
        "gpt-5.3-codex-xhigh", "gpt-5.3-codex-xhigh-fast",
    ])
    composer_only: bool = False
    composer_augmented: bool = True
    credit_target_monthly: int = 500
    locked: bool = True

    @classmethod
    def from_file(cls, path: str | Path) -> "TrouterConfig":
        """Load from cursor_config.json."""
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            with open(path) as f:
                data = json.load(f)
            return cls(
                dispatch_mode=data.get("dispatch_mode", "native"),
                enabled=data.get("enabled", True),
                model_override=data.get("model_override", ""),
                default_model=data.get("default_model", "composer-1.5"),
                allowed_models=data.get("allowed_models", [
                    "composer-1.5",
                    "gpt-5.3-codex", "gpt-5.3-codex-low", "gpt-5.3-codex-low-fast",
                    "gpt-5.3-codex-fast", "gpt-5.3-codex-high", "gpt-5.3-codex-high-fast",
                    "gpt-5.3-codex-xhigh", "gpt-5.3-codex-xhigh-fast",
                ]),
                composer_only=data.get("composer_only", False),
                composer_augmented=data.get("composer_augmented", True),
                credit_target_monthly=data.get("credit_target_monthly", 500),
                locked=data.get("locked", True),
            )
        except json.JSONDecodeError as exc:
            logger.warning("cursor_config.json contains invalid JSON (%s): %s", path, exc)
            return cls()
        except OSError as exc:
            logger.warning("Failed to read cursor_config.json (%s): %s", path, exc)
            return cls()

    def to_file(self, path: str | Path) -> None:
        """Write to cursor_config.json.

        Raises:
            PermissionError: If the process lacks write permission for *path*
                or its parent directory.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "dispatch_mode": self.dispatch_mode,
            "enabled": self.enabled,
            "model_override": self.model_override,
            "default_model": self.default_model,
            "allowed_models": self.allowed_models,
            "composer_only": self.composer_only,
            "composer_augmented": self.composer_augmented,
            "credit_target_monthly": self.credit_target_monthly,
            "locked": self.locked,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


def find_config_path() -> Path:
    """Locate cursor_config.json in standard locations."""
    # Check TROUTER_CONFIG env var
    env_path = os.environ.get("TROUTER_CONFIG")
    if env_path:
        return Path(env_path)

    # Check relative to package
    pkg_etc = Path(__file__).parent.parent.parent / "etc" / "cursor_config.json"
    if pkg_etc.exists():
        return pkg_etc

    # Check CLI location
    cli_etc = Path.home() / "claude_rotifer" / "CLI" / "etc" / "cursor_config.json"
    if cli_etc.exists():
        return cli_etc

    return pkg_etc  # Default even if doesn't exist
