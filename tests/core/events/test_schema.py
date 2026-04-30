"""Schema invariants — envelope must be strict, frozen, and small-typed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from itsme.core.events.schema import EventEnvelope, EventType


def _ulid() -> str:
    """Ad-hoc 26-char ULID-shaped string (good enough for schema tests)."""
    return "01HXYZ" + "0" * 20


def test_event_type_count_is_six() -> None:
    """Locked decision — the bus has exactly 6 event types."""
    assert len(list(EventType)) == 6


def test_event_type_values_match_architecture_doc() -> None:
    """Type strings must match ARCHITECTURE §5 verbatim."""
    expected = {
        "raw.captured",
        "memory.stored",
        "memory.routed",
        "wiki.promoted",
        "memory.curated",
        "memory.queried",
    }
    assert {t.value for t in EventType} == expected


def test_envelope_happy_path() -> None:
    """A fully populated envelope round-trips through pydantic."""
    env = EventEnvelope(
        id=_ulid(),
        ts=datetime.now(tz=UTC),
        type=EventType.RAW_CAPTURED,
        source="test",
        payload={"k": "v"},
    )
    assert env.id == _ulid()
    assert env.type is EventType.RAW_CAPTURED
    assert env.payload == {"k": "v"}
    assert env.schema_version == 1


def test_envelope_is_frozen() -> None:
    """Once emitted, envelopes must not mutate."""
    env = EventEnvelope(
        id=_ulid(),
        ts=datetime.now(tz=UTC),
        type=EventType.RAW_CAPTURED,
        source="test",
    )
    with pytest.raises(ValidationError):
        env.source = "tampered"


def test_envelope_rejects_unknown_fields() -> None:
    """Strict envelope — extra fields are a programming error."""
    with pytest.raises(ValidationError):
        EventEnvelope(
            id=_ulid(),
            ts=datetime.now(tz=UTC),
            type=EventType.RAW_CAPTURED,
            source="test",
            secret_field="surprise",  # type: ignore[call-arg]
        )


def test_envelope_rejects_unknown_event_type() -> None:
    """Type field must be one of the 6 enum members."""
    with pytest.raises(ValidationError):
        EventEnvelope(
            id=_ulid(),
            ts=datetime.now(tz=UTC),
            type="memory.exfiltrated",  # type: ignore[arg-type]
            source="test",
        )


def test_envelope_rejects_short_id() -> None:
    """ULIDs are exactly 26 chars; reject shorter ids."""
    with pytest.raises(ValidationError):
        EventEnvelope(
            id="short",
            ts=datetime.now(tz=UTC),
            type=EventType.RAW_CAPTURED,
            source="test",
        )


def test_envelope_rejects_non_crockford_id() -> None:
    """Crockford base32 excludes I, L, O, U — ids using them must fail."""
    bad_ids = [
        "01HXYZ0000000000000000000I",  # contains I
        "01HXYZ0000000000000000000L",  # contains L
        "01HXYZ0000000000000000000O",  # contains O
        "01HXYZ0000000000000000000U",  # contains U
        "abcdefghijklmnopqrstuvwxyz",  # lowercase
        "!@#$%^&*()_+0123456789ABCD",  # symbols + only 26 chars
    ]
    for bad in bad_ids:
        assert len(bad) == 26, f"test data wrong: {bad!r}"
        with pytest.raises(ValidationError):
            EventEnvelope(
                id=bad,
                ts=datetime.now(tz=UTC),
                type=EventType.RAW_CAPTURED,
                source="test",
            )


def test_envelope_accepts_real_ulid() -> None:
    """Real python-ulid output passes the Crockford check."""
    from ulid import ULID

    real_id = str(ULID())
    env = EventEnvelope(
        id=real_id,
        ts=datetime.now(tz=UTC),
        type=EventType.RAW_CAPTURED,
        source="test",
    )
    assert env.id == real_id


def test_envelope_rejects_empty_source() -> None:
    """Source must identify *some* producer."""
    with pytest.raises(ValidationError):
        EventEnvelope(
            id=_ulid(),
            ts=datetime.now(tz=UTC),
            type=EventType.RAW_CAPTURED,
            source="",
        )


def test_envelope_default_payload_is_empty_dict() -> None:
    """Omitted payload defaults to ``{}`` (not ``None``)."""
    env = EventEnvelope(
        id=_ulid(),
        ts=datetime.now(tz=UTC),
        type=EventType.MEMORY_QUERIED,
        source="test",
    )
    assert env.payload == {}
