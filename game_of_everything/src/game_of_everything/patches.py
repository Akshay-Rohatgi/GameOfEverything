"""Monkey-patches for crewAI runtime bugs.

Applied automatically on import — see main.py.

Handles:
  - Unquoted JSON keys (Python dict style from Bedrock LLMs)
  - Trailing commas, single quotes, Python True/False/None
  - Multiple concatenated JSON objects (LLM outputs two objects)
  - Markdown code fences around JSON (```json ... ```)
  - Bedrock assistant-message-prefill rejection on Claude 4.x models
"""

import json
import re

import crewai.utilities.converter as _converter_mod
import crewai.task as _task_mod
from json_repair import repair_json

_original_convert_to_model = _converter_mod.convert_to_model

# Strip markdown code fences: ```json ... ``` or ``` ... ```
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def _extract_best_json(raw: str) -> str:
    """Extract a single valid JSON object from potentially messy LLM output.

    Handles: markdown fences, multiple concatenated objects, unquoted keys,
    trailing text before/after JSON, Python-style dicts.
    """
    # Strip markdown fences if present
    fence_match = _FENCE_RE.search(raw)
    if fence_match:
        raw = fence_match.group(1)

    # Use json_repair to parse — return_objects=True gives us a Python object.
    # If the input has multiple concatenated JSON objects, json_repair returns
    # a list of them.
    repaired = repair_json(raw, return_objects=True)

    if isinstance(repaired, list) and repaired:
        # Multiple objects concatenated — pick the best one.
        # Filter to dicts only (skip stray strings/nulls).
        dicts = [obj for obj in repaired if isinstance(obj, dict)]
        if dicts:
            # Pick the largest by serialized length — the real output, not the
            # empty placeholder the LLM sometimes emits first.
            best = max(dicts, key=lambda d: len(json.dumps(d)))
            return json.dumps(best)
        # No dicts — list of primitives or a legitimate JSON array.
        # Fall through to returning the repaired string.

    if isinstance(repaired, dict):
        return json.dumps(repaired)

    # Fallback: repair as string (handles unquoted keys etc.)
    return repair_json(raw, return_objects=False)


def _patched_convert_to_model(result, output_pydantic, output_json, agent=None, converter_cls=None):
    """Wrapper that repairs malformed LLM JSON before crewAI's parser sees it."""
    if isinstance(result, str):
        result = _extract_best_json(result)
    return _original_convert_to_model(result, output_pydantic, output_json, agent, converter_cls)


# Patch both the canonical definition AND the imported reference in task.py
_converter_mod.convert_to_model = _patched_convert_to_model
_task_mod.convert_to_model = _patched_convert_to_model


# ---------------------------------------------------------------------------
# Patch 2: Bedrock "assistant message prefill" rejection
#
# Claude 4.x models on Bedrock reject conversations ending with an assistant
# message (the Converse API treats this as "prefill", which these models don't
# support). crewAI's handle_max_iterations_exceeded() appends an assistant
# message then calls llm.call(messages), triggering the error.
#
# Fix: wrap BedrockCompletion._format_messages_for_converse to append a
# continuation user message whenever the last message is from the assistant,
# regardless of model family.
# ---------------------------------------------------------------------------

try:
    from crewai.llms.providers.bedrock.completion import BedrockCompletion

    _original_format_messages = BedrockCompletion._format_messages_for_converse

    def _patched_format_messages(self, messages):
        converse_messages, system_message = _original_format_messages(self, messages)
        if converse_messages and converse_messages[-1].get("role") == "assistant":
            converse_messages.append(
                {
                    "role": "user",
                    "content": [
                        {"text": "Please continue and provide your final answer."}
                    ],
                }
            )
        return converse_messages, system_message

    BedrockCompletion._format_messages_for_converse = _patched_format_messages
except ImportError:
    pass
