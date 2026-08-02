"""GoE v2 configuration — loaded from goe.toml with env-var overrides.

Resolution order (highest wins):
  1. Environment variable
  2. goe.toml value
  3. Hardcoded default
"""

import os
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

# goe/config.py → goe/ → project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
        if cls._instance is None:
            cls._instance = GoEConfig()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @property
    def aws_access_key_id(self) -> str:
        return os.getenv("AWS_ACCESS_KEY_ID", self._data.get("aws", {}).get("access_key_id", ""))

    @property
    def aws_secret_access_key(self) -> str:
        return os.getenv("AWS_SECRET_ACCESS_KEY", self._data.get("aws", {}).get("secret_access_key", ""))

    @property
    def aws_session_token(self) -> str:
        return os.getenv("AWS_SESSION_TOKEN", self._data.get("aws", {}).get("session_token", ""))

    @property
    def aws_region(self) -> str:
        return os.getenv("AWS_REGION", self._data.get("aws", {}).get("region", "us-east-1"))

    @property
    def aws_profile(self) -> str:
        return os.getenv("AWS_PROFILE", self._data.get("aws", {}).get("profile", ""))

    def _aws_deploy(self, key: str, default=""):
        return self._data.get("deploy", {}).get("aws", {}).get(key, default)

    @property
    def deploy_aws_region(self) -> str:
        return os.getenv("GOE_AWS_REGION", self._aws_deploy("region", self.aws_region))

    @property
    def deploy_aws_profile(self) -> str:
        return os.getenv("GOE_AWS_PROFILE", self._aws_deploy("profile", self.aws_profile))

    @property
    def deploy_aws_instance_type(self) -> str:
        return os.getenv("GOE_AWS_INSTANCE_TYPE", self._aws_deploy("instance_type", "t3.small"))

    @property
    def deploy_aws_attacker_cidr(self) -> str:
        return os.getenv("GOE_ATTACKER_CIDR", self._aws_deploy("attacker_cidr", ""))

    @property
    def default_model(self) -> str:
        return os.getenv(
            "GOE_DEFAULT_MODEL",
            self._data.get("models", {}).get("default", "us.anthropic.claude-sonnet-4-6-20251001-v1:0"),
        )

    def model_for(self, role: str) -> str:
        """Return the configured model for a given construction_crew role."""
        env_key = f"GOE_MODEL_{role.upper()}"
        env_val = os.getenv(env_key)
        if env_val:
            return env_val
        override = self._data.get("models", {}).get("v2_overrides", {}).get(role)
        if override:
            return override
        return self.default_model

    @property
    def save_artifacts(self) -> bool:
        """Whether to persist workflow artifacts (conversations + generated scripts).

        Resolution order: GOE_SAVE_ARTIFACTS env var → [artifacts].enabled in goe.toml → False.
        Accepted truthy values: "1", "true", "yes", "on" (case-insensitive).
        """
        raw = os.getenv("GOE_SAVE_ARTIFACTS") or self._data.get("artifacts", {}).get("enabled", "")
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    @property
    def artifacts_dir(self) -> str:
        """Root directory for artifact output.

        Resolution order: GOE_ARTIFACTS_DIR env var → [artifacts].dir in goe.toml → "artifacts".
        Per-run timestamped subdirectories are created under this root.
        """
        return (
            os.getenv("GOE_ARTIFACTS_DIR")
            or self._data.get("artifacts", {}).get("dir", "artifacts")
        )
