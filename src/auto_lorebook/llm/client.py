"""Async HTTP client for the OpenRouter /chat/completions API."""

from __future__ import annotations

import os

import httpx


class OpenRouterError(RuntimeError):
    """Raised when the OpenRouter API returns an error response."""


class OpenRouterClient:
    """Async HTTP client for the OpenRouter /chat/completions API.

    Uses httpx with an optional injected transport for testing.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Initialise the client.

        :param api_key: OpenRouter API key for the Authorization header.
        :param base_url: Base URL for the OpenRouter API.
        :param _transport: Optional custom transport (for testing only).
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = _transport

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
    ) -> str:
        """Send a chat completion request and return the response text.

        :param model: Model identifier string.
        :param messages: List of role/content message dicts.
        :param temperature: Sampling temperature.
        :param max_tokens: Optional token limit for the response.
        :param response_format: Optional response format dict (e.g. JSON mode).
        :return: Assistant message content string.
        :raises OpenRouterError: If the API returns a non-2xx response.
        """
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        async with httpx.AsyncClient(transport=self._transport) as http:
            response = await http.post(url, json=payload, headers=headers)

        if response.status_code >= 400:
            msg = f"OpenRouter API error {response.status_code}: {response.text}"
            raise OpenRouterError(msg)

        data = response.json()
        return str(data["choices"][0]["message"]["content"])


def openrouter_client_from_env(
    *,
    base_url: str = "https://openrouter.ai/api/v1",
) -> OpenRouterClient:
    """Create an OpenRouterClient using the OPENROUTER_API_KEY env variable.

    :param base_url: Base URL for the OpenRouter API.
    :return: Configured OpenRouterClient.
    :raises RuntimeError: If OPENROUTER_API_KEY is not set in the environment.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key is None:
        msg = "OPENROUTER_API_KEY environment variable is not set"
        raise RuntimeError(msg)
    return OpenRouterClient(api_key, base_url=base_url)
