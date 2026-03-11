"""Tests for trouter.core.config — TrouterConfig and find_config_path."""

import json
from pathlib import Path


from trouter.core.config import TrouterConfig, find_config_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_ALLOWED_MODELS = [
    "composer-1.5",
    "gpt-5.3-codex",
    "gpt-5.3-codex-low",
    "gpt-5.3-codex-low-fast",
    "gpt-5.3-codex-fast",
    "gpt-5.3-codex-high",
    "gpt-5.3-codex-high-fast",
    "gpt-5.3-codex-xhigh",
    "gpt-5.3-codex-xhigh-fast",
]

FULL_CONFIG = {
    "dispatch_mode": "local",
    "enabled": False,
    "model_override": "gpt-5.3-codex-high",
    "default_model": "gpt-5.3-codex",
    "allowed_models": ["gpt-5.3-codex", "composer-1.5"],
    "composer_only": True,
    "composer_augmented": False,
    "credit_target_monthly": 250,
    "locked": False,
}


# ---------------------------------------------------------------------------
# test_from_file_valid
# ---------------------------------------------------------------------------

class TestFromFileValid:
    """Load a fully-populated JSON file and verify every field is mapped."""

    def test_from_file_valid(self, tmp_path: Path):
        cfg_file = tmp_path / "cursor_config.json"
        cfg_file.write_text(json.dumps(FULL_CONFIG))

        cfg = TrouterConfig.from_file(cfg_file)

        assert cfg.dispatch_mode == "local"
        assert cfg.enabled is False
        assert cfg.model_override == "gpt-5.3-codex-high"
        assert cfg.default_model == "gpt-5.3-codex"
        assert cfg.allowed_models == ["gpt-5.3-codex", "composer-1.5"]
        assert cfg.composer_only is True
        assert cfg.composer_augmented is False
        assert cfg.credit_target_monthly == 250
        assert cfg.locked is False


# ---------------------------------------------------------------------------
# test_from_file_missing
# ---------------------------------------------------------------------------

class TestFromFileMissing:
    """A non-existent path should silently return a default TrouterConfig."""

    def test_from_file_missing(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.json"
        cfg = TrouterConfig.from_file(missing)

        # All fields must be their dataclass defaults
        defaults = TrouterConfig()
        assert cfg.dispatch_mode == defaults.dispatch_mode
        assert cfg.enabled == defaults.enabled
        assert cfg.model_override == defaults.model_override
        assert cfg.default_model == defaults.default_model
        assert cfg.allowed_models == defaults.allowed_models
        assert cfg.composer_only == defaults.composer_only
        assert cfg.composer_augmented == defaults.composer_augmented
        assert cfg.credit_target_monthly == defaults.credit_target_monthly
        assert cfg.locked == defaults.locked


# ---------------------------------------------------------------------------
# test_from_file_invalid_json
# ---------------------------------------------------------------------------

class TestFromFileInvalidJson:
    """Malformed JSON should be caught and return defaults — never raise."""

    def test_from_file_invalid_json(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("{this is not valid json!!")

        cfg = TrouterConfig.from_file(bad)

        defaults = TrouterConfig()
        assert cfg.dispatch_mode == defaults.dispatch_mode
        assert cfg.locked == defaults.locked

    def test_from_file_empty_json(self, tmp_path: Path):
        """An empty file also triggers the except path and returns defaults."""
        empty = tmp_path / "empty.json"
        empty.write_text("")

        cfg = TrouterConfig.from_file(empty)
        assert cfg.dispatch_mode == TrouterConfig().dispatch_mode


# ---------------------------------------------------------------------------
# test_to_file
# ---------------------------------------------------------------------------

class TestToFile:
    """Write then read back: roundtrip must be lossless for all fields."""

    def test_to_file(self, tmp_path: Path):
        original = TrouterConfig(
            dispatch_mode="local",
            enabled=True,
            model_override="gpt-5.3-codex-xhigh",
            default_model="gpt-5.3-codex",
            allowed_models=["gpt-5.3-codex", "composer-1.5"],
            composer_only=False,
            composer_augmented=True,
            credit_target_monthly=750,
            locked=True,
        )

        cfg_file = tmp_path / "written_config.json"
        original.to_file(cfg_file)

        assert cfg_file.exists(), "to_file must create the config file"

        restored = TrouterConfig.from_file(cfg_file)

        assert restored.dispatch_mode == original.dispatch_mode
        assert restored.enabled == original.enabled
        assert restored.model_override == original.model_override
        assert restored.default_model == original.default_model
        assert restored.allowed_models == original.allowed_models
        assert restored.composer_only == original.composer_only
        assert restored.composer_augmented == original.composer_augmented
        assert restored.credit_target_monthly == original.credit_target_monthly
        assert restored.locked == original.locked

    def test_to_file_creates_parent_dirs(self, tmp_path: Path):
        """to_file must create intermediate directories if they don't exist."""
        nested = tmp_path / "sub" / "dir" / "cursor_config.json"
        TrouterConfig().to_file(nested)
        assert nested.exists()

    def test_to_file_produces_valid_json(self, tmp_path: Path):
        """File written by to_file must be parseable JSON."""
        cfg_file = tmp_path / "cursor_config.json"
        TrouterConfig().to_file(cfg_file)

        raw = cfg_file.read_text()
        parsed = json.loads(raw)  # raises if not valid JSON
        assert "dispatch_mode" in parsed


# ---------------------------------------------------------------------------
# test_find_config_path_env_override
# ---------------------------------------------------------------------------

class TestFindConfigPathEnvOverride:
    """TROUTER_CONFIG env var must take precedence over filesystem discovery."""

    def test_find_config_path_env_override(self, tmp_path: Path, monkeypatch):
        custom = tmp_path / "custom_config.json"
        custom.write_text(json.dumps({"dispatch_mode": "local"}))

        monkeypatch.setenv("TROUTER_CONFIG", str(custom))

        result = find_config_path()
        assert result == custom

    def test_find_config_path_env_override_nonexistent(self, tmp_path: Path, monkeypatch):
        """Even a nonexistent path in the env var should be returned as-is."""
        fake = tmp_path / "nonexistent.json"
        monkeypatch.setenv("TROUTER_CONFIG", str(fake))

        result = find_config_path()
        assert result == fake

    def test_find_config_path_no_env(self, monkeypatch):
        """Without env var, result must be a Path (not None or a string)."""
        monkeypatch.delenv("TROUTER_CONFIG", raising=False)
        result = find_config_path()
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# test_from_file_missing_key
# ---------------------------------------------------------------------------

class TestFromFileMissingKey:
    """JSON that omits some keys must fill those fields with defaults."""

    def test_from_file_missing_key(self, tmp_path: Path):
        partial = {"dispatch_mode": "local", "enabled": False}
        cfg_file = tmp_path / "partial.json"
        cfg_file.write_text(json.dumps(partial))

        cfg = TrouterConfig.from_file(cfg_file)

        # Keys that were present should be applied
        assert cfg.dispatch_mode == "local"
        assert cfg.enabled is False

        # Keys that were absent should be defaults
        defaults = TrouterConfig()
        assert cfg.model_override == defaults.model_override
        assert cfg.default_model == defaults.default_model
        assert cfg.allowed_models == defaults.allowed_models
        assert cfg.composer_only == defaults.composer_only
        assert cfg.composer_augmented == defaults.composer_augmented
        assert cfg.credit_target_monthly == defaults.credit_target_monthly
        assert cfg.locked == defaults.locked

    def test_from_file_only_allowed_models_key(self, tmp_path: Path):
        """Partial config with only allowed_models applies it, rest are defaults."""
        custom_models = ["composer-1.5"]
        cfg_file = tmp_path / "models_only.json"
        cfg_file.write_text(json.dumps({"allowed_models": custom_models}))

        cfg = TrouterConfig.from_file(cfg_file)

        assert cfg.allowed_models == custom_models
        # Dispatch mode must still be the default
        assert cfg.dispatch_mode == TrouterConfig().dispatch_mode
