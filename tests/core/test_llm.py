"""Tests for core.llm — T2.6 LLM provider abstraction."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from itsme.core.llm import (
    DeepSeekProvider,
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


# ---------------------------------------------------------- DeepSeekProvider


class TestDeepSeekProvider:
    def test_missing_api_key_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DEEPSEEK_API_KEY", None)
            with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
                DeepSeekProvider(api_key="")

    def test_complete_calls_api(self) -> None:
        """Verify the provider sends correct request to the API."""
        import httpx

        # Build a fake response
        fake_response = httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "extracted: Postgres entity"}}
                ]
            },
        )

        provider = DeepSeekProvider(api_key="sk-test", model="test-model")
        with patch("httpx.post", return_value=fake_response) as mock_post:
            result = provider.complete(
                system="Extract entities.",
                messages=[{"role": "user", "content": "I chose Postgres."}],
            )

        assert result == "extracted: Postgres entity"
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["model"] == "test-model"
        # System message is prepended to messages
        assert body["messages"][0] == {"role": "system", "content": "Extract entities."}
        assert body["messages"][1] == {"role": "user", "content": "I chose Postgres."}

    def test_auth_error_raises_llm_error(self) -> None:
        import httpx

        fake_response = httpx.Response(401, text="Unauthorized")
        provider = DeepSeekProvider(api_key="sk-bad", model="m")
        with patch("httpx.post", return_value=fake_response), \
             pytest.raises(LLMError, match="auth"):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

    def test_rate_limit_raises_unavailable(self) -> None:
        import httpx

        fake_response = httpx.Response(429, text="Too Many Requests")
        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with patch("httpx.post", return_value=fake_response), \
             pytest.raises(LLMUnavailableError, match="rate limited"):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

    def test_server_error_raises_unavailable(self) -> None:
        import httpx

        fake_response = httpx.Response(500, text="Internal Server Error")
        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with patch("httpx.post", return_value=fake_response), \
             pytest.raises(LLMUnavailableError, match="server error"):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

    def test_connection_error_raises_unavailable(self) -> None:
        import httpx

        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")), \
             pytest.raises(LLMUnavailableError, match="connection"):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

    def test_timeout_raises_unavailable(self) -> None:
        import httpx

        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with patch("httpx.post", side_effect=httpx.ReadTimeout("timed out")), \
             pytest.raises(LLMUnavailableError, match="timed out"):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

    def test_empty_choices_returns_empty(self) -> None:
        import httpx

        fake_response = httpx.Response(200, json={"choices": []})
        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with patch("httpx.post", return_value=fake_response):
            result = provider.complete(system="", messages=[{"role": "user", "content": "hi"}])
            assert result == ""

    def test_custom_base_url(self) -> None:
        provider = DeepSeekProvider(
            api_key="sk-test", model="m", base_url="https://custom.api.com/v1"
        )
        assert provider._base_url == "https://custom.api.com/v1"  # noqa: SLF001


# ------------------------------------------------------------- build factory


class TestBuildLLMProvider:
    def test_no_api_key_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DEEPSEEK_API_KEY", None)
            provider = build_llm_provider()
            assert provider is None

    def test_with_api_key_returns_deepseek(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
            provider = build_llm_provider()
            assert isinstance(provider, DeepSeekProvider)

    def test_custom_model_env(self) -> None:
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "sk-test", "ITSME_LLM_MODEL": "deepseek-reasoner"},
        ):
            provider = build_llm_provider()
            assert isinstance(provider, DeepSeekProvider)
            assert provider._model == "deepseek-reasoner"  # noqa: SLF001 — test internals
