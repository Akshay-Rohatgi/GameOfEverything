"""Centralized LLM construction with per-agent model resolution.

Resolution order for make_llm("testing_agent"):
  1. GOE_MODEL_TESTING_AGENT env var
  2. agents.testing_agent.model_id in config/models.yaml
  3. GOE_DEFAULT_MODEL env var
  4. default.model_id in config/models.yaml
  5. Hardcoded fallback: anthropic.claude-sonnet-4-6
"""

import os
import yaml
from pathlib import Path
from functools import lru_cache
from typing import Optional

from crewai import LLM

_CONFIG_DIR = Path(__file__).parent / "config"


@lru_cache(maxsize=1)
def _load_models_config() -> dict:
    config_path = _CONFIG_DIR / "models.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def make_llm(agent_name: Optional[str] = None) -> LLM:
    """Create a crewAI LLM instance, optionally tailored to a specific agent.

    Args:
        agent_name: Key from agents.yaml (e.g. "testing_agent"). If None, uses
                    the default model configuration.
    """
    config = _load_models_config()
    default_cfg = config.get("default", {})
    agents_cfg = config.get("agents", {}) or {}

    agent_cfg = agents_cfg.get(agent_name, {}) if agent_name else {}

    # Check env var override for this specific agent
    env_agent_model = None
    if agent_name:
        env_key = f"GOE_MODEL_{agent_name.upper()}"
        env_agent_model = os.getenv(env_key)

    model_id = (
        env_agent_model
        or agent_cfg.get("model_id")
        or os.getenv("GOE_DEFAULT_MODEL")
        or default_cfg.get("model_id", "anthropic.claude-sonnet-4-6")
    )

    provider = agent_cfg.get("provider") or default_cfg.get("provider", "bedrock")

    if provider == "bedrock":
        if not model_id.startswith("us.") and not model_id.startswith("eu."):
            model_id = f"us.{model_id}"
        return LLM(
            model=f"bedrock/{model_id}",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            region=os.getenv(
                "AWS_REGION",
                agent_cfg.get("region") or default_cfg.get("region", "us-east-1"),
            ),
        )

    raise ValueError(
        f"Unsupported LLM provider: '{provider}'. Currently only 'bedrock' is supported."
    )
