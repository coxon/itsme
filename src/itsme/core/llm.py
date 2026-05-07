"""LLM provider abstraction — T2.6.

Minimal interface for v0.0.2: ``complete(system, messages) → str``.

Provider selection:

* **DeepSeekProvider** — production default. Uses the DeepSeek
  OpenAI-compatible API. Requires ``$DEEPSEEK_API_KEY``.
* **StubProvider** — returns a canned response. For tests and for the
  graceful-degradation path when no API key is configured.

Model configuration:

* ``$ITSME_LLM_MODEL`` — model name (default ``deepseek-chat``).
* ``$DEEPSEEK_API_KEY`` — API key for DeepSeek.

Usage::

    provider = build_llm_provider()       # auto-detect from env
    text = provider.complete(
        system="You are a memory extractor.",
        messages=[{"role": "user", "content": "..."}],
    )
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------- defaults

#: Default model for all LLM calls.
DEFAULT_MODEL: str = "deepseek-chat"

#: Default DeepSeek API base URL.
DEFAULT_BASE_URL: str = "https://api.deepseek.com"

#: Default max output tokens for a single LLM call.
DEFAULT_MAX_TOKENS: int = 2048


# --------------------------------------------------------------------- protocol


@runtime_checkable
class LLMProvider(Protocol):
    """The shape every LLM backend must satisfy.

    Synchronous on purpose — intake runs in the router's background
    thread pool; we don't want to force ``async`` on every caller.
    """

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        """Send *system* + *messages* to the model and return the text response.

        Raises:
            LLMError: Any non-retryable failure (auth, bad request, …).
            LLMUnavailableError: Transient failure (network, rate limit, …).
        """
        ...


# --------------------------------------------------------------------- errors


class LLMError(RuntimeError):
    """Non-retryable LLM failure (bad key, invalid request, …)."""


class LLMUnavailableError(LLMError):
    """Transient failure — caller should degrade gracefully."""


# ----------------------------------------------------------- DeepSeek provider


class DeepSeekProvider:
    """Production LLM backend via the DeepSeek OpenAI-compatible API.

    Args:
        model: Model id override. Defaults to ``$ITSME_LLM_MODEL``
            or :data:`DEFAULT_MODEL`.
        api_key: API key override. Defaults to ``$DEEPSEEK_API_KEY``.
        base_url: API base URL. Defaults to ``$ITSME_LLM_BASE_URL``
            or :data:`DEFAULT_BASE_URL`.
        max_tokens: Default max output tokens (can be overridden per call).
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._model = model or os.environ.get("ITSME_LLM_MODEL", DEFAULT_MODEL)
        self._base_url = (
            base_url
            or os.environ.get("ITSME_LLM_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self._max_tokens = max_tokens

        resolved_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or ""
        if not resolved_key:
            raise LLMError(
                "DEEPSEEK_API_KEY is required for DeepSeekProvider. "
                "Set it in the environment or pass api_key= explicitly."
            )
        self._api_key = resolved_key

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
    ) -> str:
        """Call the DeepSeek chat completions API.

        Raises:
            LLMError: Authentication or request-shape errors.
            LLMUnavailableError: Network / rate-limit / server errors.
        """
        import httpx

        api_messages: list[dict[str, str]] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": api_messages,
                    "max_tokens": max_tokens or self._max_tokens,
                },
                timeout=60.0,
            )
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(f"DeepSeek connection failed: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError(f"DeepSeek request timed out: {exc}") from exc

        if response.status_code == 401:
            raise LLMError(f"DeepSeek auth failed (401): {response.text[:200]}")
        if response.status_code == 400:
            raise LLMError(f"DeepSeek bad request (400): {response.text[:200]}")
        if response.status_code == 429:
            raise LLMUnavailableError(f"DeepSeek rate limited (429): {response.text[:200]}")
        if response.status_code >= 500:
            raise LLMUnavailableError(
                f"DeepSeek server error ({response.status_code}): {response.text[:200]}"
            )
        if response.status_code != 200:
            raise LLMError(
                f"DeepSeek unexpected status ({response.status_code}): {response.text[:200]}"
            )

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeepSeekProvider model={self._model!r} base_url={self._base_url!r}>"


# ----------------------------------------------------------- Stub provider


class StubProvider:
    """Deterministic stub for tests and graceful degradation.

    Returns *response* verbatim for every ``complete()`` call. Useful
    for unit tests that need a predictable LLM output without hitting
    the network.
    """

    def __init__(self, *, response: str = "") -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})
        return self._response

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StubProvider calls={len(self.calls)}>"


# ----------------------------------------------------------- factory


def build_llm_provider() -> LLMProvider | None:
    """Auto-detect and construct the best available LLM provider.

    Returns:
        A :class:`DeepSeekProvider` if ``$DEEPSEEK_API_KEY`` is set;
        ``None`` otherwise. The caller decides whether to degrade or
        raise.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        _logger.info("itsme: no DEEPSEEK_API_KEY — LLM provider unavailable, will degrade")
        return None

    model = os.environ.get("ITSME_LLM_MODEL", DEFAULT_MODEL)

    try:
        return DeepSeekProvider(model=model, api_key=api_key)
    except LLMError as exc:
        _logger.warning("itsme: LLM provider creation failed, degrading: %s", exc)
        return None
