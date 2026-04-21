"""Async OpenRouter HTTP client."""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from auto_lorebook.config import Config, ModelParams

_BASE_URL = "https://openrouter.ai/api/v1"


class LLMError(Exception):
    """LLM request failure."""


def model_slot(config: Config) -> tuple[str, ModelParams]:
    """Extract (model, params) for pipeline stages.

    :param config: loaded Config
    :return: (model string, ModelParams)
    """
    return config.model, config.model_params


def params_sha256(params: ModelParams) -> str:
    """Deterministic SHA-256 of model params for staleness tracking.

    :param params: sampling parameters
    :return: hex SHA-256 digest
    """
    canon = f"temperature={params.temperature};max_tokens={params.max_tokens}"
    return hashlib.sha256(canon.encode()).hexdigest()


def _or_model(model: str) -> str:
    """Strip 'openrouter/' prefix for direct API calls."""
    return model.removeprefix("openrouter/")


async def complete(
    prompt: str,
    *,
    model: str,
    params: ModelParams,
    api_key: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Post prompt to OpenRouter /chat/completions, return response text.

    :param prompt: user prompt text
    :param model: model string (with or without 'openrouter/' prefix)
    :param params: sampling parameters
    :param api_key: override OPENROUTER_API_KEY env var
    :param _transport: injectable transport for testing
    :raises LLMError: on missing key, HTTP error, or unexpected response shape
    """
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        msg = "OPENROUTER_API_KEY environment variable not set"
        raise LLMError(msg)

    payload: dict[str, object] = {
        "model": _or_model(model),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": params.temperature,
        "max_tokens": params.max_tokens,
    }

    async with httpx.AsyncClient(transport=_transport) as client:
        try:
            resp = await client.post(
                f"{_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120.0,
            )
        except httpx.RequestError as exc:
            msg = f"OpenRouter connection error: {exc}"
            raise LLMError(msg) from exc

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            msg = f"OpenRouter HTTP {exc.response.status_code}"
            raise LLMError(msg) from exc

        data: dict[str, object] = resp.json()
        try:
            choices = data["choices"]
            assert isinstance(choices, list)  # noqa: S101
            first = choices[0]
            assert isinstance(first, dict)  # noqa: S101
            message = first["message"]
            assert isinstance(message, dict)  # noqa: S101
            content = message["content"]
            assert isinstance(content, str)  # noqa: S101
        except (KeyError, IndexError, AssertionError) as exc:
            msg = f"Unexpected OpenRouter response: {data!r}"
            raise LLMError(msg) from exc
        else:
            return content
