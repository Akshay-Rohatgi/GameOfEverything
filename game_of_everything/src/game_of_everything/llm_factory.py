"""Centralized LLM construction with per-agent model resolution.

Resolution order for make_llm("testing_agent"):
  1. GOE_MODEL_TESTING_AGENT env var
  2. goe.toml [models.overrides] testing_agent
  3. agents.testing_agent.model_id in config/models.yaml
  4. GOE_DEFAULT_MODEL env var
  5. goe.toml [models] default
  6. default.model_id in config/models.yaml
  7. Hardcoded fallback: anthropic.claude-sonnet-4-6
"""

import yaml
from pathlib import Path
from functools import lru_cache
from typing import Optional

from crewai import LLM

from game_of_everything.config import GoEConfig

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
    cfg = GoEConfig.get()
    yaml_config = _load_models_config()
    default_yaml = yaml_config.get("default", {})
    agents_yaml = yaml_config.get("agents", {}) or {}
    agent_yaml = agents_yaml.get(agent_name, {}) if agent_name else {}

    # Resolve model ID: env var → toml override → yaml override → toml default → yaml default
    toml_override = cfg.model_override(agent_name) if agent_name else None

    model_id = (
        toml_override
        or agent_yaml.get("model_id")
        or cfg.default_model
        or default_yaml.get("model_id", "anthropic.claude-sonnet-4-6")
    )

    provider = agent_yaml.get("provider") or default_yaml.get("provider", "bedrock")

    # Resolve temperature: agent yaml → default yaml → None (LLM class default)
    # Use explicit None sentinel so temperature=0 is not silently skipped.
    temperature = agent_yaml.get("temperature")
    if temperature is None:
        temperature = default_yaml.get("temperature")

    if provider == "bedrock":
        if not model_id.startswith("us.") and not model_id.startswith("eu."):
            model_id = f"us.{model_id}"
        llm_kwargs = dict(
            model=f"bedrock/{model_id}",
            aws_access_key_id=cfg.aws_access_key_id,
            aws_secret_access_key=cfg.aws_secret_access_key,
            region=cfg.aws_region,
        )
        if temperature is not None:
            llm_kwargs["temperature"] = temperature
        return LLM(**llm_kwargs)

    raise ValueError(
        f"Unsupported LLM provider: '{provider}'. Currently only 'bedrock' is supported."
    )
