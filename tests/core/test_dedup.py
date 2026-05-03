"""T1.19 — content_hash + producer_kind helpers."""

from __future__ import annotations

import hashlib

import pytest

from itsme.core.dedup import content_hash, producer_kind_from_source


# ---------------------------------------------------------------- content_hash
def test_content_hash_is_sha256_hex_of_stripped_content() -> None:
    """The hash is the sha256 hex of ``content.strip()`` UTF-8 bytes."""
    content = "the quick brown fox"
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert content_hash(content) == expected
    assert len(content_hash(content)) == 64


def test_content_hash_strips_whitespace_to_dedup_transcript_artifacts() -> None:
    """``"X"`` and ``"X\\n"`` and ``"  X  "`` all hash identically.

    Transcript readers vary on trailing newlines / leading indent; an
    explicit ``remember("X")`` followed by a hook capturing ``"X\\n"``
    must dedup, not silently double-write.
    """
    base = content_hash("decided to ship")
    assert content_hash("decided to ship\n") == base
    assert content_hash("\n  decided to ship  \n\n") == base
    assert content_hash("\tdecided to ship") == base


def test_content_hash_distinguishes_different_content() -> None:
    """Trivial sanity — collision-free for everyday strings."""
    assert content_hash("alpha") != content_hash("beta")
    assert content_hash("decided X") != content_hash("decided Y")


def test_content_hash_empty_input_returns_empty_sentinel() -> None:
    """Empty / whitespace-only inputs hash to the empty-bytes sha256.

    The orchestrator rejects empty content upstream; this test pins the
    helper's behaviour as a defensive contract — it must not raise.
    """
    empty_sentinel = hashlib.sha256(b"").hexdigest()
    assert content_hash("") == empty_sentinel
    assert content_hash("   ") == empty_sentinel
    assert content_hash("\n\n\t") == empty_sentinel


def test_content_hash_is_unicode_aware() -> None:
    """Encoding is UTF-8 so non-ASCII content hashes deterministically."""
    s = "café résumé naïve 😀"
    expected = hashlib.sha256(s.encode("utf-8")).hexdigest()
    assert content_hash(s) == expected


# -------------------------------------------------------- producer_kind
@pytest.mark.parametrize(
    "source,expected",
    [
        ("explicit", "explicit"),
        ("explicit:cli", "explicit"),
        ("explicit:foo", "explicit"),
        ("hook:before-exit", "hook:lifecycle"),
        ("hook:before-compact", "hook:lifecycle"),
        ("hook:context-pressure", "hook:context-pressure"),
        # Unknown hook source — passthrough so it stays observable
        # rather than silently bucketing as ``unknown``.
        ("hook:future-thing", "hook:future-thing"),
        # Non-hook, non-explicit — passthrough.
        ("worker:router", "worker:router"),
        ("adapter:mempalace", "adapter:mempalace"),
    ],
)
def test_producer_kind_from_source_known_buckets(source: str, expected: str) -> None:
    assert producer_kind_from_source(source) == expected


def test_producer_kind_from_source_empty_is_unknown() -> None:
    """Empty source defaults to ``unknown`` rather than crashing."""
    assert producer_kind_from_source("") == "unknown"
