"""Shared utilities for planner agents."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any


def strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text, count=1)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


@lru_cache(maxsize=1)
def _rendered_context() -> dict[str, str]:
    from goe.planner._context import atom_list, edge_type_list, runtime_list
    return {
        "ATOMS": atom_list(),
        "RUNTIMES": runtime_list(),
        "EDGE_TYPES": edge_type_list(),
    }


def render_system_prompt(template: str) -> str:
    """Fill {ATOMS}, {RUNTIMES}, {EDGE_TYPES} placeholders from live sources."""
    ctx = _rendered_context()
    for key, value in ctx.items():
        template = template.replace("{" + key + "}", value)
    return template


def call_json(model: str, system: str, user_msg: str, caller: str = "") -> Any:
    """Call Bedrock and return parsed JSON, retrying once on parse failure."""
    from goe.bedrock import call

    raw = call(model_id=model, system=system, messages=[{"role": "user", "content": user_msg}], caller=caller)
    raw = strip_fences(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        retry = (
            f"{user_msg}\n\nYour previous response could not be parsed as JSON: {e}\n"
            "Output ONLY valid JSON with no surrounding text."
        )
        raw2 = call(model_id=model, system=system, messages=[{"role": "user", "content": retry}], caller=f"{caller}.retry")
        raw2 = strip_fences(raw2)
        return json.loads(raw2)
