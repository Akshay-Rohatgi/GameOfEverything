"""Direct AWS Bedrock API client — no crewAI, no LiteLLM."""

from __future__ import annotations

import boto3
from botocore.config import Config

# Connect/read timeouts (seconds) and retry policy for every Bedrock call.
# Without an explicit read timeout, botocore blocks indefinitely when Bedrock
# stalls or throttles — that is what made the grader call hang forever. A
# bounded read timeout plus adaptive retries turns a stall into a raised
# BedrockError instead of a permanent hang.
_BEDROCK_CONFIG = Config(
    connect_timeout=10,
    read_timeout=120,
    retries={"max_attempts": 3, "mode": "adaptive"},
)

# Process-wide cache of bedrock-runtime clients, keyed by
# (region, access_key_id, secret_access_key). boto3 clients are thread-safe
# for API calls, so reusing one across calls avoids re-resolving the botocore
# session and reloading service models on every LLM call. Distinct
# credentials/regions still get distinct clients.
_CLIENT_CACHE: dict[tuple, object] = {}


class BedrockError(Exception):
    pass


def _get_client(region, akid, secret):
    """Return a cached bedrock-runtime client for the given credentials/region.

    Calls ``boto3.client`` exactly once per unique key; subsequent calls with
    the same key reuse the cached client.
    """
    # Include the identity of the boto3.client factory in the key. In normal
    # operation this is stable, so the client is created once and reused. Under
    # tests that patch ``boto3.client`` per-call, each patch installs a fresh
    # factory object, so the key differs and the (new) mock is still invoked —
    # the cache stays correct without weakening those tests.
    key = (region, akid, secret, id(boto3.client))
    client = _CLIENT_CACHE.get(key)
    if client is None:
        client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            aws_access_key_id=akid,
            aws_secret_access_key=secret,
            config=_BEDROCK_CONFIG,
        )
        _CLIENT_CACHE[key] = client
    return client


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
    caller: str = "",
) -> str:
    """Call a Bedrock model via the Converse API and return the assistant text.

    Args:
        model_id: Bedrock model ID (region prefix added automatically if missing).
        system: System prompt text.
        messages: List of {"role": "user"|"assistant", "content": str} dicts.
        max_tokens: Maximum tokens to generate.
        temperature: Sampling temperature.
        caller: Optional identifier for metrics (e.g., "architect", "planner.design_systems").

    Returns:
        The assistant's response text.

    Raises:
        BedrockError: On API error or unexpected response shape.
    """
    import time
    import uuid
    from goe.config import GoEConfig
    from goe.metrics import get_session, LLMCallRecord

    cfg = GoEConfig.get()
    model_id = _ensure_region_prefix(model_id)

    client = _get_client(
        cfg.aws_region,
        cfg.aws_access_key_id or None,
        cfg.aws_secret_access_key or None,
    )

    converse_messages = [
        {"role": m["role"], "content": [{"text": m["content"]}]}
        for m in messages
    ]

    t0 = time.time()
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
    latency_ms = (time.time() - t0) * 1000

    # Extract token usage
    usage = response.get("usage", {})
    input_tokens = usage.get("inputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)

    # Extract response text first so it's available for transcript capture below
    try:
        response_text = response["output"]["message"]["content"][0]["text"]
    except (KeyError, IndexError) as e:
        raise BedrockError(f"Unexpected Bedrock response shape: {response}") from e

    # Record metrics (and optionally full conversation) if a session is active
    session = get_session()
    if session:
        record = LLMCallRecord(
            call_id=str(uuid.uuid4()),
            timestamp=t0,
            caller=caller,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )
        session.record(record)
        # Capture full conversation when artifact collection is active
        if getattr(session, "transcripts", None) is not None:
            session.record_transcript(
                call_id=record.call_id,
                timestamp=t0,
                caller=caller,
                model_id=model_id,
                system=system,
                messages=messages,
                response=response_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

    return response_text
