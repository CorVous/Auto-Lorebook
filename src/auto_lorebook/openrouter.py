"""OpenRouter HTTP client.

Thin wrapper over OpenRouter's OpenAI-compatible `/chat/completions`
endpoint. Uses stdlib `urllib` to avoid a runtime HTTP dep.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 120.0


class OpenRouterError(RuntimeError):
    """Base for OpenRouter client errors."""


class OpenRouterAuthError(OpenRouterError):
    """401: bad or missing API key."""


class OpenRouterRateLimitError(OpenRouterError):
    """429: upstream throttling."""


class OpenRouterAPIError(OpenRouterError):
    """Non-2xx response or malformed body."""


class OpenRouterTransportError(OpenRouterError):
    """DNS / connection / TLS failure before a response was received."""


@dataclass(frozen=True)
class OpenRouterResponse:
    """Parsed completion response."""

    text: str
    model: str
    tokens_in: int | None
    tokens_out: int | None


class OpenRouterClient:
    """Synchronous OpenRouter chat-completions client."""

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        referer: str | None = None,
        app_title: str | None = None,
    ) -> None:
        if not api_key:
            msg = "OpenRouterClient: api_key is required (set it in the config or env)"
            raise OpenRouterError(msg)
        self._api_key = api_key
        self._default_model = default_model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._referer = referer
        self._app_title = app_title

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> OpenRouterResponse:
        """Call `/chat/completions` and return the first choice's text."""
        body: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if response_format is not None:
            body["response_format"] = response_format
        if extra:
            body.update(extra)

        raw = self._post_json("/chat/completions", body)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            msg = f"OpenRouter returned non-JSON body: {raw[:200]!r}"
            raise OpenRouterAPIError(msg) from e

        choices = parsed.get("choices") or []
        if not choices:
            msg = f"OpenRouter returned no choices: {parsed!r}"
            raise OpenRouterAPIError(msg)
        first = choices[0]
        message = first.get("message") or {}
        text = message.get("content") or ""
        usage = parsed.get("usage") or {}
        return OpenRouterResponse(
            text=text,
            model=parsed.get("model") or body["model"],
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
        )

    def _post_json(self, path: str, body: dict[str, Any]) -> bytes:
        url = self._base_url + path
        data = json.dumps(body).encode("utf-8")
        req = Request(url, data=data, method="POST")  # noqa: S310
        req.add_header("Authorization", f"Bearer {self._api_key}")
        req.add_header("Content-Type", "application/json")
        if self._referer:
            req.add_header("HTTP-Referer", self._referer)
        if self._app_title:
            req.add_header("X-Title", self._app_title)
        try:
            with urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                return resp.read()
        except HTTPError as e:
            self._raise_from_http_error(e)
        except URLError as e:
            msg = f"OpenRouter transport error: {e.reason}"
            raise OpenRouterTransportError(msg) from e
        # unreachable: _raise_from_http_error always raises
        msg = "unreachable"
        raise RuntimeError(msg)

    @staticmethod
    def _raise_from_http_error(e: HTTPError) -> None:
        try:
            body_bytes = e.read() or b""
        except (OSError, AttributeError):
            body_bytes = b""
        detail = ""
        if body_bytes:
            try:
                parsed = json.loads(body_bytes.decode("utf-8", errors="replace"))
                detail = (
                    parsed.get("error", {}).get("message")
                    if isinstance(parsed, dict)
                    else ""
                ) or body_bytes.decode("utf-8", errors="replace")[:200]
            except json.JSONDecodeError:
                detail = body_bytes.decode("utf-8", errors="replace")[:200]
        msg = f"OpenRouter {e.code}: {detail or e.reason}"
        if e.code == 401:
            raise OpenRouterAuthError(msg) from e
        if e.code == 429:
            raise OpenRouterRateLimitError(msg) from e
        raise OpenRouterAPIError(msg) from e
