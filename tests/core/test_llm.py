"""Tests for core.llm — T2.6 LLM provider abstraction."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from itsme.core.llm import (
    AnthropicProvider,
    LLMError,
    LLMUnavailableError,
    StubProvider,
    build_llm_provider,
)


# ---------------------------------------------------------------- StubProvider


class TestStubProvider:
    def test_returns_canned_response(self) -> None:
        stub = StubProvider(response='{"result": "ok"}')
        out = stub.complete(system="sys", messages=[{"role": "user", "content": "hi"}])
        assert out == '{"result": "ok"}'

    def test_records_calls(self) -> None:
        stub = StubProvider(response="x")
        stub.complete(system="a", messages=[{"role": "user", "content": "b"}])
        stub.complete(system="c", messages=[{"role": "user", "content": "d"}])
        assert len(stub.calls) == 2
        assert stub.calls[0]["system"] == "a"
        assert stub.calls[1]["system"] == "c"

    def test_default_empty_response(self) -> None:
        stub = StubProvider()
        assert stub.complete(system="", messages=[]) == ""

    def test_satisfies_protocol(self) -> None:
        from itsme.core.llm import LLMProvider

        stub = StubProvider()
        assert isinstance(stub, LLMProvider)


# ---------------------------------------------------------- AnthropicProvider


class TestAnthropicProvider:
    def test_missing_api_key_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            # Ensure ANTHROPIC_API_KEY is absent
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
                AnthropicProvider(api_key="")

    def test_missing_sdk_raises(self) -> None:
        with patch.dict("sys.modules", {"anthropic": None}):
            with pytest.raises(LLMError, match="anthropic"):
                AnthropicProvider(api_key="sk-test-fake")

    def test_complete_calls_messages_api(self) -> None:
        """Verify the provider wires through to anthropic.Anthropic.messages.create."""
        # Build a fake anthropic module
        mock_anthropic = MagicMock()
        fake_text_block = MagicMock()
        fake_text_block.text = "extracted: Postgres entity"
        mock_response = MagicMock()
        mock_response.content = [fake_text_block]
        mock_anthropic.Anthropic.return_value.messages.create.return_value = mock_response
        # Patch the import
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            provider = AnthropicProvider(api_key="sk-test", model="test-model")
            result = provider.complete(
                system="Extract entities.",
                messages=[{"role": "user", "content": "I chose Postgres."}],
            )
        assert result == "extracted: Postgres entity"
        call_kwargs = mock_anthropic.Anthropic.return_value.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "test-model"
        assert call_kwargs.kwargs["system"] == "Extract entities."

    def test_auth_error_raises_llm_error(self) -> None:
        mock_anthropic = MagicMock()
        mock_anthropic.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_anthropic.BadRequestError = type("BadRequestError", (Exception,), {})
        mock_anthropic.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_anthropic.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_anthropic.APIStatusError = type("APIStatusError", (Exception,), {})
        mock_anthropic.Anthropic.return_value.messages.create.side_effect = (
            mock_anthropic.AuthenticationError("bad key")
        )
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            provider = AnthropicProvider(api_key="sk-bad", model="m")
            with pytest.raises(LLMError, match="auth"):
                provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

    def test_rate_limit_raises_unavailable(self) -> None:
        mock_anthropic = MagicMock()
        mock_anthropic.AuthenticationError = type("AuthenticationError", (Exception,), {})
        mock_anthropic.BadRequestError = type("BadRequestError", (Exception,), {})
        mock_anthropic.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_anthropic.APIConnectionError = type("APIConnectionError", (Exception,), {})
        mock_anthropic.APIStatusError = type("APIStatusError", (Exception,), {})
        mock_anthropic.Anthropic.return_value.messages.create.side_effect = (
            mock_anthropic.RateLimitError("too many")
        )
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            provider = AnthropicProvider(api_key="sk-ok", model="m")
            with pytest.raises(LLMUnavailableError, match="unavailable"):
                provider.complete(system="", messages=[{"role": "user", "content": "hi"}])


# ------------------------------------------------------------- build factory


class TestBuildLLMProvider:
    def test_no_api_key_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            provider = build_llm_provider()
            assert provider is None

    def test_with_api_key_returns_anthropic(self) -> None:
        mock_anthropic = MagicMock()
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}),
            patch.dict("sys.modules", {"anthropic": mock_anthropic}),
        ):
            provider = build_llm_provider()
            assert isinstance(provider, AnthropicProvider)

    def test_promoter_role_uses_promoter_model_env(self) -> None:
        mock_anthropic = MagicMock()
        with (
            patch.dict(
                os.environ,
                {"ANTHROPIC_API_KEY": "sk-test", "ITSME_LLM_PROMOTER_MODEL": "my-sonnet"},
            ),
            patch.dict("sys.modules", {"anthropic": mock_anthropic}),
        ):
            provider = build_llm_provider(role="promoter")
            assert isinstance(provider, AnthropicProvider)
            assert provider._model == "my-sonnet"  # noqa: SLF001 — test internals
