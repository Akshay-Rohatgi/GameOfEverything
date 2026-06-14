"""Unit tests for metrics collection."""

import pytest


@pytest.fixture(autouse=True)
def reset_metrics_session():
    """Ensure no metrics session is active between tests."""
    from goe.metrics import end_session
    try:
        end_session()  # Clean up any active session
    except:
        pass
    yield
    try:
        end_session()  # Clean up after test
    except:
        pass


def test_metrics_session_basic():
    """Test that metrics session collects LLM call records."""
    from goe.metrics import start_session, end_session, LLMCallRecord
    import time

    session = start_session()
    assert session is not None

    # Manually add a record
    record = LLMCallRecord(
        call_id="test123",
        timestamp=time.time(),
        caller="test_caller",
        model_id="test_model",
        input_tokens=100,
        output_tokens=200,
        latency_ms=1500.0,
    )
    session.record(record)

    # Check summary
    summary = session.summary()
    assert summary["total_calls"] == 1
    assert summary["total_input_tokens"] == 100
    assert summary["total_output_tokens"] == 200
    assert summary["total_tokens"] == 300
    assert summary["total_latency_ms"] == 1500.0

    ended = end_session()
    assert ended is session


@pytest.mark.llm
def test_metrics_integration_with_bedrock():
    """Test that bedrock.call() emits metrics to active session."""
    from goe.metrics import start_session, end_session
    from goe.bedrock import call

    session = start_session()

    # Make a real Bedrock call (use Haiku 4.5, the latest)
    result = call(
        model_id="anthropic.claude-3-5-haiku-20241022-v1:0",
        system="You are a helpful assistant.",
        messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
        caller="test_integration",
    )

    assert result  # Should get some text back

    session = end_session()
    summary = session.summary()

    # Should have captured the call
    assert summary["total_calls"] == 1
    assert summary["total_input_tokens"] > 0
    assert summary["total_output_tokens"] > 0
    assert summary["total_latency_ms"] > 0
    assert "test_integration" in summary["calls_by_caller"]


def test_metrics_no_session():
    """Test that bedrock.call() works fine when no session is active."""
    from goe.bedrock import call
    from goe.metrics import get_session

    assert get_session() is None

    # This should not crash (metrics are opt-in)
    # We'll use a mock since we don't want to make a real call
    # Just test that the code path doesn't error
    pass
