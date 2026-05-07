"""Tests for core.llm — T2.6 LLM provider abstraction.

Mock strategy: ``DeepSeekProvider.complete()`` creates an ``httpx.Client``
via a context manager and calls ``.post()`` on the instance.  We patch
``httpx.Client`` to return a ``MagicMock`` whose ``.post()`` returns a
pre-built ``httpx.Response`` (or raises for error paths).

For retry-path tests (ConnectError / TimeoutException) we also patch
``time.sleep`` so the 3-retry loop runs instantly.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from itsme.core.llm import (
    DeepSeekProvider,
    LLMError,
    LLMUnavailableError,
    StubProvider,
    build_llm_provider,
)

# ---------------------------------------------------------------- helpers


def _mock_client(
    *,
    response: Any | None = None,
    side_effect: Any | None = None,
) -> MagicMock:
    """Return a MagicMock that behaves like ``httpx.Client`` as a ctx-mgr.

    ``client.post()`` either returns *response* or raises per *side_effect*.
    """
    client = MagicMock()
    if side_effect is not None:
        client.post.side_effect = side_effect
    elif response is not None:
        client.post.return_value = response
    # context-manager protocol
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


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

        fake_response = httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "extracted: Postgres entity"}}
                ]
            },
        )

        mc = _mock_client(response=fake_response)
        provider = DeepSeekProvider(api_key="sk-test", model="test-model")
        with patch("httpx.Client", return_value=mc):
            result = provider.complete(
                system="Extract entities.",
                messages=[{"role": "user", "content": "I chose Postgres."}],
            )

        assert result == "extracted: Postgres entity"
        call_kwargs = mc.post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["model"] == "test-model"
        # System message is prepended to messages
        assert body["messages"][0] == {"role": "system", "content": "Extract entities."}
        assert body["messages"][1] == {"role": "user", "content": "I chose Postgres."}

    def test_proxy_disabled(self) -> None:
        """httpx.Client is constructed with proxy=None to bypass system proxy."""
        import httpx

        fake_response = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        mc = _mock_client(response=fake_response)
        provider = DeepSeekProvider(api_key="sk-test", model="m")
        with patch("httpx.Client", return_value=mc) as cls_mock:
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

        cls_mock.assert_called_once_with(proxy=None, trust_env=False, timeout=60.0)

    def test_auth_error_raises_llm_error(self) -> None:
        import httpx

        fake_response = httpx.Response(401, text="Unauthorized")
        mc = _mock_client(response=fake_response)
        provider = DeepSeekProvider(api_key="sk-bad", model="m")
        with (
            patch("httpx.Client", return_value=mc),
            pytest.raises(LLMError, match="auth"),
        ):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

    def test_rate_limit_raises_unavailable(self) -> None:
        import httpx

        fake_response = httpx.Response(429, text="Too Many Requests")
        mc = _mock_client(response=fake_response)
        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with (
            patch("httpx.Client", return_value=mc),
            pytest.raises(LLMUnavailableError, match="rate limited"),
        ):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

    def test_server_error_raises_unavailable(self) -> None:
        import httpx

        fake_response = httpx.Response(500, text="Internal Server Error")
        mc = _mock_client(response=fake_response)
        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with (
            patch("httpx.Client", return_value=mc),
            pytest.raises(LLMUnavailableError, match="server error"),
        ):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

    def test_connection_error_raises_unavailable(self) -> None:
        """ConnectError triggers 3 retries then LLMUnavailableError."""
        import httpx

        mc = _mock_client(side_effect=httpx.ConnectError("refused"))
        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with (
            patch("httpx.Client", return_value=mc),
            patch("time.sleep"),
            pytest.raises(LLMUnavailableError, match="connection failed"),
        ):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])
        # Should have attempted 3 times
        assert mc.post.call_count == 3

    def test_timeout_raises_unavailable_no_retry(self) -> None:
        """ReadTimeout raises LLMUnavailableError immediately — no retry."""
        import httpx

        mc = _mock_client(side_effect=httpx.ReadTimeout("timed out"))
        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with (
            patch("httpx.Client", return_value=mc),
            pytest.raises(LLMUnavailableError, match="timed out"),
        ):
            provider.complete(system="", messages=[{"role": "user", "content": "hi"}])
        # Should NOT retry — only 1 attempt
        assert mc.post.call_count == 1

    def test_retry_then_succeed(self) -> None:
        """Transient errors on first two attempts, success on third."""
        import httpx

        ok_resp = httpx.Response(200, json={"choices": [{"message": {"content": "recovered"}}]})
        # Each retry creates a new httpx.Client(), so we need 3 mocks.
        c1 = _mock_client(side_effect=httpx.ConnectError("fail-1"))
        c2 = _mock_client(side_effect=httpx.ConnectError("fail-2"))
        c3 = _mock_client(response=ok_resp)

        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with (
            patch("httpx.Client", side_effect=[c1, c2, c3]),
            patch("time.sleep"),
        ):
            result = provider.complete(system="", messages=[{"role": "user", "content": "hi"}])
        assert result == "recovered"

    def test_truncation_warning_logged(self) -> None:
        """finish_reason=length triggers a truncation warning."""
        import httpx

        fake_response = httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "partial"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"completion_tokens": 2048},
            },
        )

        mc = _mock_client(response=fake_response)
        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        import logging

        with (
            patch("httpx.Client", return_value=mc),
            patch.object(logging.getLogger("itsme.core.llm"), "warning") as mock_warn,
        ):
            result = provider.complete(system="", messages=[{"role": "user", "content": "hi"}])

        assert result == "partial"
        mock_warn.assert_called_once()
        warn_msg = mock_warn.call_args[0][0]
        assert "truncated" in warn_msg
        assert "finish_reason=length" in warn_msg

    def test_empty_choices_returns_empty(self) -> None:
        import httpx

        fake_response = httpx.Response(200, json={"choices": []})
        mc = _mock_client(response=fake_response)
        provider = DeepSeekProvider(api_key="sk-ok", model="m")
        with patch("httpx.Client", return_value=mc):
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
