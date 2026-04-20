"""Tests for the OpenRouter async HTTP client."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import httpx
import pytest

from auto_lorebook.llm.client import (
    OpenRouterClient,
    OpenRouterError,
    openrouter_client_from_env,
)

FAKE_API_KEY = "test-key-123"
BASE_URL = "https://openrouter.ai/api/v1"
_USER_HI = [{"role": "user", "content": "Hi"}]


def _make_response(
    content: str, model: str = "test-model", status: int = 200
) -> httpx.Response:
    """Build a mock httpx.Response for the chat completions endpoint."""
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return httpx.Response(status_code=status, json=body)


def _make_error_response(
    status: int, message: str = "Rate limit exceeded"
) -> httpx.Response:
    """Build a mock error httpx.Response."""
    body = {"error": {"message": message, "type": "rate_limit_error"}}
    return httpx.Response(status_code=status, json=body)


def _make_client(handler: httpx.MockTransport) -> OpenRouterClient:
    """Return an OpenRouterClient backed by a mock transport."""
    return OpenRouterClient(FAKE_API_KEY, base_url=BASE_URL, _transport=handler)


@pytest.mark.trio
async def test_chat_success_returns_content() -> None:
    """Successful response returns the assistant message content string."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _make_response("Hello from the LLM!")

    client = _make_client(httpx.MockTransport(handler))
    result = await client.chat("test-model", _USER_HI)
    assert result == "Hello from the LLM!"


@pytest.mark.trio
async def test_chat_non_2xx_raises_error() -> None:
    """Non-2xx responses raise OpenRouterError."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _make_error_response(429)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(OpenRouterError):
        await client.chat("test-model", _USER_HI)


@pytest.mark.trio
async def test_chat_sends_authorization_header() -> None:
    """Request includes the Authorization: Bearer header."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _make_response("ok")

    client = _make_client(httpx.MockTransport(handler))
    await client.chat("test-model", _USER_HI)
    assert captured[0].headers["authorization"] == f"Bearer {FAKE_API_KEY}"


@pytest.mark.trio
async def test_chat_sends_model_in_body() -> None:
    """Request body includes the requested model."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _make_response("ok")

    client = _make_client(httpx.MockTransport(handler))
    await client.chat("my-model", _USER_HI)
    body = json.loads(captured[0].content)
    assert body["model"] == "my-model"


@pytest.mark.trio
async def test_chat_sends_messages_in_body() -> None:
    """Request body includes the messages list."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _make_response("ok")

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Tell me lore."},
    ]
    client = _make_client(httpx.MockTransport(handler))
    await client.chat("test-model", messages)
    body = json.loads(captured[0].content)
    assert body["messages"] == messages


@pytest.mark.trio
async def test_chat_sends_temperature() -> None:
    """Request body includes the temperature parameter."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _make_response("ok")

    client = _make_client(httpx.MockTransport(handler))
    await client.chat("test-model", _USER_HI, temperature=0.5)
    body = json.loads(captured[0].content)
    assert body["temperature"] == pytest.approx(0.5)


@pytest.mark.trio
async def test_chat_sends_max_tokens_when_set() -> None:
    """max_tokens is included in the request body when provided."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _make_response("ok")

    client = _make_client(httpx.MockTransport(handler))
    await client.chat("test-model", _USER_HI, max_tokens=512)
    body = json.loads(captured[0].content)
    assert body["max_tokens"] == 512


@pytest.mark.trio
async def test_chat_omits_max_tokens_when_none() -> None:
    """max_tokens is NOT in the request body when None."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _make_response("ok")

    client = _make_client(httpx.MockTransport(handler))
    await client.chat("test-model", _USER_HI)
    body = json.loads(captured[0].content)
    assert "max_tokens" not in body


@pytest.mark.trio
async def test_chat_sends_response_format_when_provided() -> None:
    """response_format is included in the body when given."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _make_response("{}")

    fmt = {"type": "json_object"}
    client = _make_client(httpx.MockTransport(handler))
    await client.chat("test-model", _USER_HI, response_format=fmt)
    body = json.loads(captured[0].content)
    assert body["response_format"] == fmt


def test_openrouter_client_from_env_reads_key() -> None:
    """openrouter_client_from_env creates a client from the env variable."""
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-key-456"}):
        client = openrouter_client_from_env()
    assert isinstance(client, OpenRouterClient)


def test_openrouter_client_from_env_missing_key() -> None:
    """openrouter_client_from_env raises RuntimeError if key is not set."""
    env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"),
    ):
        openrouter_client_from_env()


@pytest.mark.trio
async def test_chat_network_error_propagates() -> None:
    """Transport-level errors (connection refused, DNS failure) propagate."""

    def handler(_request: httpx.Request) -> httpx.Response:
        msg = "Connection refused"
        raise httpx.ConnectError(msg)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(httpx.ConnectError):
        await client.chat("test-model", _USER_HI)


@pytest.mark.trio
async def test_client_context_manager() -> None:
    """Client works as an async context manager and can be reused across calls."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _make_response("ok")

    async with OpenRouterClient(
        FAKE_API_KEY, base_url=BASE_URL, _transport=httpx.MockTransport(handler)
    ) as client:
        result1 = await client.chat("test-model", _USER_HI)
        result2 = await client.chat("test-model", _USER_HI)
    assert result1 == "ok"
    assert result2 == "ok"


@pytest.mark.trio
async def test_chat_error_text_truncated() -> None:
    """Long error response text is truncated in the exception message."""

    def handler(_request: httpx.Request) -> httpx.Response:
        long_body = "x" * 1000
        return httpx.Response(status_code=500, text=long_body)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(OpenRouterError, match="OpenRouter API error 500") as exc_info:
        await client.chat("test-model", _USER_HI)
    # Error message should be truncated, not contain the full 1000 chars
    assert len(str(exc_info.value)) < 600
