"""Tests for OpenRouter async client and model slot config."""

from __future__ import annotations

import json

import httpx
import pytest

from auto_lorebook.config import Config, ModelParams
from auto_lorebook.llm import LLMError, complete, model_slot, params_sha256


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, status: int, body: dict[str, object]) -> None:
        self._status = status
        self._body = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:  # noqa: ARG002
        return httpx.Response(self._status, json=self._body)


def _ok(content: str) -> _MockTransport:
    return _MockTransport(200, {"choices": [{"message": {"content": content}}]})


def _err(status: int) -> _MockTransport:
    return _MockTransport(status, {"error": "bad"})


@pytest.mark.trio
async def test_complete_returns_response_text() -> None:
    result = await complete(
        "hello",
        model="openrouter/anthropic/claude-test",
        params=ModelParams(),
        api_key="sk-test",
        _transport=_ok("hi there"),
    )
    assert result == "hi there"


@pytest.mark.trio
async def test_complete_strips_openrouter_prefix() -> None:
    captured: list[httpx.Request] = []

    class _Cap(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]}
            )

    await complete(
        "p",
        model="openrouter/anthropic/t",
        params=ModelParams(),
        api_key="k",
        _transport=_Cap(),
    )
    body = json.loads(captured[0].content)
    assert body["model"] == "anthropic/t"


@pytest.mark.trio
async def test_complete_raises_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        await complete("p", model="m", params=ModelParams())


@pytest.mark.trio
async def test_complete_raises_on_http_error() -> None:
    with pytest.raises(LLMError, match="HTTP 401"):
        await complete(
            "p", model="m", params=ModelParams(), api_key="bad", _transport=_err(401)
        )


@pytest.mark.trio
async def test_complete_raises_on_bad_shape() -> None:
    transport = _MockTransport(200, {"result": "oops"})
    with pytest.raises(LLMError, match="Unexpected"):
        await complete(
            "p", model="m", params=ModelParams(), api_key="k", _transport=transport
        )


def test_model_slot_returns_model_and_params() -> None:
    cfg = Config(model="openrouter/x/y", model_params=ModelParams(temperature=0.5))
    m, p = model_slot(cfg)
    assert m == "openrouter/x/y"
    assert p.temperature == pytest.approx(0.5)


def test_params_sha256_deterministic() -> None:
    p = ModelParams(temperature=1.0, max_tokens=4096)
    assert params_sha256(p) == params_sha256(p)


def test_params_sha256_differs_on_change() -> None:
    a = ModelParams(temperature=1.0, max_tokens=4096)
    b = ModelParams(temperature=0.5, max_tokens=4096)
    assert params_sha256(a) != params_sha256(b)
