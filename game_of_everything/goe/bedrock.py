"""Direct AWS Bedrock API client — no crewAI, no LiteLLM."""

from __future__ import annotations


class BedrockError(Exception):
    pass


def _ensure_region_prefix(model_id: str) -> str:
    """Prepend 'us.' inference profile prefix if the model ID lacks one."""
    if model_id.startswith(("us.", "eu.", "ap.")):
        return model_id
    return f"us.{model_id}"


def call(
    model_id: str,
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 1.0,
) -> str:
    """Call a Bedrock model via the Converse API and return the assistant text.

    Args:
        model_id: Bedrock model ID (region prefix added automatically if missing).
        system: System prompt text.
        messages: List of {"role": "user"|"assistant", "content": str} dicts.
        max_tokens: Maximum tokens to generate.
        temperature: Sampling temperature.

    Returns:
        The assistant's response text.

    Raises:
        BedrockError: On API error or unexpected response shape.
    """
    import boto3
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model_id = _ensure_region_prefix(model_id)

    client = boto3.client(
        "bedrock-runtime",
        region_name=cfg.aws_region,
        aws_access_key_id=cfg.aws_access_key_id or None,
        aws_secret_access_key=cfg.aws_secret_access_key or None,
    )

    converse_messages = [
        {"role": m["role"], "content": [{"text": m["content"]}]}
        for m in messages
    ]

    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=converse_messages,
            inferenceConfig={
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        )
    except Exception as e:
        raise BedrockError(f"Bedrock converse call failed: {e}") from e

    try:
        return response["output"]["message"]["content"][0]["text"]
    except (KeyError, IndexError) as e:
        raise BedrockError(f"Unexpected Bedrock response shape: {response}") from e
