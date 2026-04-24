"""Tests for openrouter.py HTTP client."""

from __future__ import annotations

import email.message
import json
from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from auto_lorebook.openrouter import (
    OpenRouterAPIError,
    OpenRouterAuthError,
    OpenRouterClient,
    OpenRouterError,
    OpenRouterRateLimitError,
    OpenRouterResponse,
    OpenRouterTransportError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


def _ok_payload(
    text: str = "hello",
    model: str = "anthropic/claude-sonnet-4-5",
    tokens_in: int = 10,
    tokens_out: int = 5,
) -> bytes:
    return json.dumps({
        "id": "gen-xxx",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    }).encode("utf-8")


def _mock_http_response(status: int, body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *_a: None
    return resp


@pytest.fixture
def urlopen_patch() -> Iterator[MagicMock]:
    with patch("auto_lorebook.openrouter.urlopen") as m:
        yield m


class TestComplete:
    def test_success(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.return_value = _mock_http_response(200, _ok_payload("hi"))
        client = OpenRouterClient(api_key="sk-test", default_model="m/one")
        resp = client.complete(
            [{"role": "user", "content": "hi"}],
        )
        assert isinstance(resp, OpenRouterResponse)
        assert resp.text == "hi"
        assert resp.model == "anthropic/claude-sonnet-4-5"
        assert resp.tokens_in == 10
        assert resp.tokens_out == 5

    def test_sends_expected_request(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.return_value = _mock_http_response(200, _ok_payload())
        client = OpenRouterClient(api_key="sk-test", default_model="m/one")
        client.complete(
            [{"role": "user", "content": "hello"}],
            model="m/two",
            temperature=0.3,
        )
        req = urlopen_patch.call_args[0][0]
        assert req.full_url.endswith("/chat/completions")
        assert req.get_header("Authorization") == "Bearer sk-test"
        assert req.get_header("Content-type") == "application/json"
        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "m/two"
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert body["temperature"] == pytest.approx(0.3)

    def test_uses_default_model(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.return_value = _mock_http_response(200, _ok_payload())
        client = OpenRouterClient(api_key="sk-test", default_model="d/flt")
        client.complete([{"role": "user", "content": "x"}])
        body = json.loads(urlopen_patch.call_args[0][0].data.decode("utf-8"))
        assert body["model"] == "d/flt"

    def test_response_format_passthrough(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.return_value = _mock_http_response(200, _ok_payload("{}"))
        client = OpenRouterClient(api_key="sk-test", default_model="m/one")
        client.complete(
            [{"role": "user", "content": "x"}],
            response_format={"type": "json_object"},
        )
        body = json.loads(urlopen_patch.call_args[0][0].data.decode("utf-8"))
        assert body["response_format"] == {"type": "json_object"}

    def test_missing_api_key_raises(self) -> None:
        with pytest.raises(OpenRouterError, match="api_key"):
            OpenRouterClient(api_key="", default_model="m/one")

    def test_401_raises_auth_error(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.side_effect = HTTPError(
            "url",
            401,
            "Unauthorized",
            hdrs=email.message.Message(),
            fp=BytesIO(b'{"error":{"message":"bad key"}}'),
        )
        client = OpenRouterClient(api_key="sk-bad", default_model="m/one")
        with pytest.raises(OpenRouterAuthError):
            client.complete([{"role": "user", "content": "x"}])

    def test_429_raises_rate_limit(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.side_effect = HTTPError(
            "url",
            429,
            "Too Many Requests",
            hdrs=email.message.Message(),
            fp=BytesIO(b'{"error":{"message":"slow down"}}'),
        )
        client = OpenRouterClient(api_key="sk-test", default_model="m/one")
        with pytest.raises(OpenRouterRateLimitError):
            client.complete([{"role": "user", "content": "x"}])

    def test_500_raises_api_error(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.side_effect = HTTPError(
            "url",
            500,
            "Internal Server Error",
            hdrs=email.message.Message(),
            fp=BytesIO(b'{"error":{"message":"boom"}}'),
        )
        client = OpenRouterClient(api_key="sk-test", default_model="m/one")
        with pytest.raises(OpenRouterAPIError, match="boom"):
            client.complete([{"role": "user", "content": "x"}])

    def test_network_error_raises_transport(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.side_effect = URLError("dns fail")
        client = OpenRouterClient(api_key="sk-test", default_model="m/one")
        with pytest.raises(OpenRouterTransportError):
            client.complete([{"role": "user", "content": "x"}])

    def test_malformed_response_raises(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.return_value = _mock_http_response(200, b"not json")
        client = OpenRouterClient(api_key="sk-test", default_model="m/one")
        with pytest.raises(OpenRouterAPIError):
            client.complete([{"role": "user", "content": "x"}])

    def test_no_choices_raises(self, urlopen_patch: MagicMock) -> None:
        urlopen_patch.return_value = _mock_http_response(
            200, json.dumps({"choices": []}).encode("utf-8")
        )
        client = OpenRouterClient(api_key="sk-test", default_model="m/one")
        with pytest.raises(OpenRouterAPIError, match="no choices"):
            client.complete([{"role": "user", "content": "x"}])
