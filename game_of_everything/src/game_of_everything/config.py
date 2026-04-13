"""Centralized configuration loaded from goe.toml with env-var overrides.

Resolution order (highest wins):
  1. Environment variable (e.g. AWS_ACCESS_KEY_ID)
  2. goe.toml value
  3. Hardcoded default

Usage:
    from game_of_everything.config import GoEConfig
    cfg = GoEConfig.get()
    cfg.aws_region  # "us-east-1"
"""

import os
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

# Project root: config.py → game_of_everything/ → src/ → game_of_everything/ (project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class GoEConfig:
    """Singleton configuration loaded from goe.toml."""

    _instance: Optional["GoEConfig"] = None

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            config_path = _PROJECT_ROOT / "goe.toml"
        self._data: dict = {}
        if config_path.exists():
            with open(config_path, "rb") as f:
                self._data = tomllib.load(f)

    @classmethod
    def get(cls) -> "GoEConfig":
        """Return the singleton instance, creating it on first call."""
        if cls._instance is None:
            cls._instance = GoEConfig()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Clear the singleton (useful for tests)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # AWS
    # ------------------------------------------------------------------

    @property
    def aws_access_key_id(self) -> str:
        return os.getenv(
            "AWS_ACCESS_KEY_ID",
            self._data.get("aws", {}).get("access_key_id", ""),
        )

    @property
    def aws_secret_access_key(self) -> str:
        return os.getenv(
            "AWS_SECRET_ACCESS_KEY",
            self._data.get("aws", {}).get("secret_access_key", ""),
        )

    @property
    def aws_region(self) -> str:
        return os.getenv(
            "AWS_REGION",
            self._data.get("aws", {}).get("region", "us-east-1"),
        )

    # ------------------------------------------------------------------
    # Models
    # ------------------------------------------------------------------

    @property
    def default_model(self) -> str:
        return os.getenv(
            "GOE_DEFAULT_MODEL",
            self._data.get("models", {}).get("default", "anthropic.claude-sonnet-4-6"),
        )

    def model_override(self, agent_name: str) -> Optional[str]:
        """Return a model override for a specific agent, or None."""
        env_key = f"GOE_MODEL_{agent_name.upper()}"
        env_val = os.getenv(env_key)
        if env_val:
            return env_val
        return self._data.get("models", {}).get("overrides", {}).get(agent_name)

    # ------------------------------------------------------------------
    # EC2 Deploy
    # ------------------------------------------------------------------

    @property
    def deploy_instance_type(self) -> str:
        return self._data.get("deploy", {}).get("instance_type", "t3.medium")

    @property
    def deploy_key_pair_name(self) -> str:
        return self._data.get("deploy", {}).get("key_pair_name", "")

    @property
    def deploy_security_group_id(self) -> str:
        return self._data.get("deploy", {}).get("security_group_id", "")

    @property
    def deploy_subnet_id(self) -> str:
        return self._data.get("deploy", {}).get("subnet_id", "")
