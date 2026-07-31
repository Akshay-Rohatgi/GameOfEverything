"""Unit tests for goe/bedrock.py — all mocked, no real API calls."""

from unittest.mock import MagicMock, patch
import pytest


def _make_response(text: str) -> dict:
    return {"output": {"message": {"content": [{"text": text}]}}}


class TestRegionPrefix:
    def test_no_prefix_gets_us(self):
        from goe.bedrock import _ensure_region_prefix
        assert _ensure_region_prefix("anthropic.claude-sonnet-4-6") == "us.anthropic.claude-sonnet-4-6"

    def test_us_prefix_unchanged(self):
        from goe.bedrock import _ensure_region_prefix
        assert _ensure_region_prefix("us.anthropic.claude-sonnet-4-6") == "us.anthropic.claude-sonnet-4-6"

    def test_eu_prefix_unchanged(self):
        from goe.bedrock import _ensure_region_prefix
        assert _ensure_region_prefix("eu.anthropic.claude-opus-4") == "eu.anthropic.claude-opus-4"


class TestCall:
    def test_returns_text(self):
        from goe.bedrock import call
        mock_client = MagicMock()
        mock_client.converse.return_value = _make_response("hello world")

        with patch("boto3.client", return_value=mock_client):
            result = call(
                model_id="anthropic.claude-sonnet-4-6",
                system="You are helpful.",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result == "hello world"

    def test_message_format(self):
        """Messages are converted to Bedrock converse format."""
        from goe.bedrock import call
        mock_client = MagicMock()
        mock_client.converse.return_value = _make_response("ok")

        with patch("boto3.client", return_value=mock_client):
            call(
                model_id="anthropic.claude-sonnet-4-6",
                system="sys",
                messages=[
                    {"role": "user", "content": "question"},
                    {"role": "assistant", "content": "answer"},
                ],
            )

        _, kwargs = mock_client.converse.call_args
        assert kwargs["messages"] == [
            {"role": "user", "content": [{"text": "question"}]},
            {"role": "assistant", "content": [{"text": "answer"}]},
        ]
        assert kwargs["system"] == [{"text": "sys"}]

    def test_region_prefix_applied(self):
        from goe.bedrock import call
        mock_client = MagicMock()
        mock_client.converse.return_value = _make_response("ok")

        with patch("boto3.client", return_value=mock_client):
            call(
                model_id="anthropic.claude-sonnet-4-6",
                system="s",
                messages=[{"role": "user", "content": "x"}],
            )

        _, kwargs = mock_client.converse.call_args
        assert kwargs["modelId"].startswith("us.")

    def test_inference_config(self):
        from goe.bedrock import call
        mock_client = MagicMock()
        mock_client.converse.return_value = _make_response("ok")

        with patch("boto3.client", return_value=mock_client):
            call(
                model_id="anthropic.claude-sonnet-4-6",
                system="s",
                messages=[{"role": "user", "content": "x"}],
                max_tokens=1024,
                temperature=0.5,
            )

        _, kwargs = mock_client.converse.call_args
        assert kwargs["inferenceConfig"]["maxTokens"] == 1024
        assert kwargs["inferenceConfig"]["temperature"] == 0.5

    def test_api_error_raises_bedrock_error(self):
        from goe.bedrock import call, BedrockError
        mock_client = MagicMock()
        mock_client.converse.side_effect = RuntimeError("connection refused")

        with patch("boto3.client", return_value=mock_client):
            with pytest.raises(BedrockError, match="connection refused"):
                call("model", "sys", [{"role": "user", "content": "x"}])

    def test_bad_response_shape_raises_bedrock_error(self):
        from goe.bedrock import call, BedrockError
        mock_client = MagicMock()
        mock_client.converse.return_value = {"unexpected": "shape"}

        with patch("boto3.client", return_value=mock_client):
            with pytest.raises(BedrockError, match="Unexpected Bedrock response shape"):
                call("model", "sys", [{"role": "user", "content": "x"}])
