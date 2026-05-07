"""LLM provider abstraction — T2.6.

Minimal interface for v0.0.2: ``complete(system, messages) → str``.

Provider selection:

* **AnthropicProvider** — production default. Requires ``anthropic``
  SDK and ``$ANTHROPIC_API_KEY``.
* **StubProvider** — returns a canned response. For tests and for the
  graceful-degradation path when no API key is configured.

Model configuration:

* ``$ITSME_LLM_MODEL`` — single model for all LLM calls (default
  Sonnet 4.6). v0.0.2 uses one model for everything; role-specific
  model selection is deferred to v0.0.3+ if cost warrants it.

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

#: Single default model for all LLM calls in v0.0.2.
DEFAULT_MODEL: str = "claude-sonnet-4-6-20260514"

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


# ----------------------------------------------------------- Anthropic provider


class AnthropicProvider:
    """Production LLM backend via the Anthropic Messages API.

    Args:
        model: Model id override. Defaults to ``$ITSME_LLM_MODEL``
            or :data:`DEFAULT_MODEL`.
        api_key: API key override. Defaults to ``$ANTHROPIC_API_KEY``.
        max_tokens: Default max output tokens (can be overridden per call).
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._model = model or os.environ.get("ITSME_LLM_MODEL", DEFAULT_MODEL)
        self._max_tokens = max_tokens

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or ""
        if not resolved_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is required for AnthropicProvider. "
                "Set it in the environment or pass api_key= explicitly."
            )

        try:
            import anthropic  # type: ignore[import-untyped]
        except ImportError as exc:
            raise LLMError(
                "The 'anthropic' package is required for AnthropicProvider. "
                "Install it with: pip install anthropic"
            ) from exc

        self._client = anthropic.Anthropic(api_key=resolved_key)

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
    ) -> str:
        """Call the Anthropic Messages API.

        Raises:
            LLMError: Authentication or request-shape errors.
            LLMUnavailableError: Network / rate-limit / server errors.
        """
        import anthropic  # type: ignore[import-untyped]

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens or self._max_tokens,
                system=system,
                messages=messages,
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError(f"Anthropic auth failed: {exc}") from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Anthropic bad request: {exc}") from exc
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            raise LLMUnavailableError(f"Anthropic unavailable: {exc}") from exc

        # Messages API returns content blocks; take the first text block.
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AnthropicProvider model={self._model!r}>"


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
        An :class:`AnthropicProvider` if ``$ANTHROPIC_API_KEY`` is set
        and the ``anthropic`` SDK is installed; ``None`` otherwise.
        The caller decides whether to degrade or raise.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        _logger.info("itsme: no ANTHROPIC_API_KEY — LLM provider unavailable, will degrade")
        return None

    model = os.environ.get("ITSME_LLM_MODEL", DEFAULT_MODEL)

    try:
        return AnthropicProvider(model=model, api_key=api_key)
    except LLMError as exc:
        _logger.warning("itsme: LLM provider creation failed, degrading: %s", exc)
        return None
