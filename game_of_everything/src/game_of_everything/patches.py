"""Monkey-patches for crewAI runtime bugs.

Applied automatically on import — see main.py.
"""

import crewai.utilities.converter as _converter_mod
import crewai.task as _task_mod
from json_repair import repair_json

_original_convert_to_model = _converter_mod.convert_to_model


def _patched_convert_to_model(result, output_pydantic, output_json, agent=None, converter_cls=None):
    """Wrapper that repairs malformed LLM JSON before crewAI's parser sees it.

    LLMs on Bedrock occasionally produce Python-style dict syntax with
    unquoted keys ({package_name: "samba"} instead of {"package_name": "samba"}).
    json-repair fixes these and other common issues (trailing commas,
    single quotes, Python True/False/None) before Pydantic validation.
    On already-valid JSON this is a no-op.
    """
    if isinstance(result, str):
        result = repair_json(result, return_objects=False)
    return _original_convert_to_model(result, output_pydantic, output_json, agent, converter_cls)


# Patch both the canonical definition AND the imported reference in task.py
_converter_mod.convert_to_model = _patched_convert_to_model
_task_mod.convert_to_model = _patched_convert_to_model
