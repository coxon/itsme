"""MemPalace adapter — Protocol shape + InMemory reference impl."""

from __future__ import annotations

import pytest

from itsme.core.adapters import (
    InMemoryMemPalaceAdapter,
    MemPalaceAdapter,
    MemPalaceHit,
    MemPalaceWriteResult,
)


def test_in_memory_satisfies_protocol() -> None:
    """The reference impl must be runtime-checkable as the Protocol."""
    a = InMemoryMemPalaceAdapter()
    assert isinstance(a, MemPalaceAdapter)


def test_write_returns_populated_result() -> None:
    """write returns a populated, frozen MemPalaceWriteResult."""
    from pydantic import ValidationError

    a = InMemoryMemPalaceAdapter()
    res = a.write(content="hello world", wing="wing_x", room="room_y")
    assert isinstance(res, MemPalaceWriteResult)
    assert len(res.drawer_id) == 26  # ULID
    assert res.wing == "wing_x"
    assert res.room == "room_y"
    with pytest.raises(ValidationError):  # frozen
        res.drawer_id = "TAMPERED"


def test_write_rejects_empty_content() -> None:
    """Empty / whitespace content is not a memory."""
    a = InMemoryMemPalaceAdapter()
    with pytest.raises(ValueError):
        a.write(content="   ", wing="w", room="r")


def test_write_rejects_missing_wing_or_room() -> None:
    """wing/room are required by spec."""
    a = InMemoryMemPalaceAdapter()
    with pytest.raises(ValueError):
        a.write(content="ok", wing="", room="r")
    with pytest.raises(ValueError):
        a.write(content="ok", wing="w", room="")


def test_write_rejects_whitespace_only_wing_or_room() -> None:
    """Whitespace-only wing/room must NOT slip through (regression).

    The earlier check used a truthiness test that accepted ``"   "`` as
    a non-empty wing, which would persist drawers under garbage names.
    """
    a = InMemoryMemPalaceAdapter()
    with pytest.raises(ValueError):
        a.write(content="ok", wing="   ", room="r")
    with pytest.raises(ValueError):
        a.write(content="ok", wing="w", room="\t\n")


def test_write_strips_wing_and_room_whitespace() -> None:
    """Surrounding whitespace is trimmed before persistence."""
    a = InMemoryMemPalaceAdapter()
    res = a.write(content="ok", wing="  wing_x  ", room="\troom_y\n")
    assert res.wing == "wing_x"
    assert res.room == "room_y"


def test_search_returns_hits_ranked_by_score() -> None:
    """Higher overlap → higher score, top result first."""
    a = InMemoryMemPalaceAdapter()
    a.write(content="cats love yarn", wing="w", room="r")
    a.write(content="dogs hate yarn", wing="w", room="r")
    a.write(content="lemurs eat fruit", wing="w", room="r")

    hits = a.search("cats yarn")
    assert hits, "expected at least one hit"
    assert isinstance(hits[0], MemPalaceHit)
    # cats+yarn (2 overlaps) should outrank dogs+yarn (1 overlap)
    assert hits[0].content == "cats love yarn"
    assert all(0.0 <= h.score <= 1.0 for h in hits)


def test_search_honors_limit() -> None:
    """``limit`` caps the hit count."""
    a = InMemoryMemPalaceAdapter()
    for i in range(10):
        a.write(content=f"thing number {i}", wing="w", room="r")
    assert len(a.search("thing", limit=3)) == 3


def test_search_zero_or_negative_limit_returns_empty() -> None:
    """Defensive: callers passing 0/-1 don't blow up."""
    a = InMemoryMemPalaceAdapter()
    a.write(content="anything", wing="w", room="r")
    assert a.search("anything", limit=0) == []
    assert a.search("anything", limit=-5) == []


def test_search_filters_by_wing() -> None:
    """``wing=`` argument scopes results."""
    a = InMemoryMemPalaceAdapter()
    a.write(content="apple banana", wing="wing_a", room="r")
    a.write(content="apple cherry", wing="wing_b", room="r")
    hits = a.search("apple", wing="wing_a")
    assert {h.wing for h in hits} == {"wing_a"}


def test_search_filters_by_room() -> None:
    """``room=`` argument scopes results."""
    a = InMemoryMemPalaceAdapter()
    a.write(content="alpha beta", wing="w", room="room_one")
    a.write(content="alpha gamma", wing="w", room="room_two")
    hits = a.search("alpha", room="room_one")
    assert {h.room for h in hits} == {"room_one"}


def test_search_empty_query_returns_empty() -> None:
    """Query with no tokens yields no hits (not an error)."""
    a = InMemoryMemPalaceAdapter()
    a.write(content="something", wing="w", room="r")
    assert a.search("") == []
    assert a.search("...") == []


def test_search_no_match_returns_empty() -> None:
    """No overlap → empty list, not an error."""
    a = InMemoryMemPalaceAdapter()
    a.write(content="apples", wing="w", room="r")
    assert a.search("zeppelin") == []


# ---------------------------------------------------- CJK tokenization

# Regression: prior to the per-char CJK tokenizer, ``\w+`` swallowed an
# entire Chinese sentence as one token, so a query of N characters
# never overlapped a longer drawer run. This was the root cause behind
# the v0.0.1 dogfood bug where ``ask("紫色独角兽")`` returned 0 hits
# against a drawer storing ``"魔法口令：紫色独角兽在月光下吃蓝莓松饼"``.


def test_search_cjk_substring_query_matches_longer_drawer() -> None:
    """A 4-char CJK query must hit a drawer whose run is longer."""
    a = InMemoryMemPalaceAdapter()
    a.write(
        content="魔法口令：紫色独角兽在月光下吃蓝莓松饼 2026-05-06",
        wing="w",
        room="r",
    )
    hits = a.search("紫色独角兽")
    assert hits, "CJK substring query must hit longer CJK drawer"
    assert "紫色独角兽" in hits[0].content


def test_search_cjk_negative_case_still_misses() -> None:
    """Non-overlapping CJK chars must not produce false positives."""
    a = InMemoryMemPalaceAdapter()
    a.write(content="紫色独角兽在月光下吃蓝莓松饼", wing="w", room="r")
    # No shared characters — should miss.
    assert a.search("苏门答腊老虎扑克玩法") == []


def test_search_cjk_japanese_hiragana_katakana() -> None:
    """Per-codepoint tokenization covers JP kana too."""
    a = InMemoryMemPalaceAdapter()
    a.write(content="ねこはかわいい", wing="w", room="r")  # hiragana
    a.write(content="ロボットがすき", wing="w", room="r")  # mixed kana
    assert a.search("ねこ"), "hiragana substring must hit"
    assert a.search("ロボット"), "katakana substring must hit"


def test_search_mixed_cjk_and_latin_query() -> None:
    """A query mixing English + Chinese must still hit a mixed drawer."""
    a = InMemoryMemPalaceAdapter()
    a.write(content="itsme v0.0.1 在我机器上首次跑通", wing="w", room="r")
    # Query using Latin token + Chinese run — both signals should
    # contribute to the Jaccard score.
    hits = a.search("itsme 跑通")
    assert hits, "mixed-script query must overlap with mixed drawer"


def test_close_is_idempotent() -> None:
    """close on the in-memory backend is a no-op and re-callable."""
    a = InMemoryMemPalaceAdapter()
    a.close()
    a.close()
