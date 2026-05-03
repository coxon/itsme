"""Content-identity helpers for cross-producer dedup (T1.19).

Why this lives in `core/` and not in the router or hooks:

* Both producers (`Memory.remember`, `run_lifecycle_hook`,
  `run_context_pressure`) need to stamp the same shape of `content_hash`
  and `producer_kind` into their `raw.captured` payloads, and the
  consumer (`Router`) needs to read it back. Centralising the recipe
  here keeps the wire contract in exactly one place.
* It's pure (no I/O, no bus / adapter handles) so tests don't need
  fixtures.

Wire shape (added to `raw.captured` payloads in v0.0.1):

* ``content_hash`` — sha256 hex of ``content.strip()`` encoded as
  UTF-8. The strip() is intentional: trailing newlines / whitespace
  are common artifacts of transcript readers and shouldn't cause
  spurious misses. Verbatim content is preserved separately in
  ``payload["content"]`` — only the *hash key* is normalised.
* ``producer_kind`` — short stable label derived from the event
  ``source``. ``"explicit"`` collapses ``explicit``, ``explicit:cli``,
  ``explicit:foo``; ``"hook:lifecycle"`` covers
  ``hook:before-exit`` / ``hook:before-compact``;
  ``"hook:context-pressure"`` is its own bucket. v0.0.2 may add more.

The same ``content_hash`` is also written into ``memory.stored``
payloads by the router so dedup scans are O(N) over a single event
type rather than two-step joins.
"""

from __future__ import annotations

import hashlib
from typing import Final

#: Empty-content sentinel used by the helpers — callers should *also*
#: validate non-empty content upstream, but the helper still has to
#: behave deterministically if it leaks through.
_EMPTY_HASH: Final[str] = hashlib.sha256(b"").hexdigest()  # 64 hex chars


def content_hash(content: str) -> str:
    """sha256 hex of ``content.strip()``. Stable across producers.

    Stripping before hashing is on purpose — different transcript
    readers occasionally tack on a trailing newline, and an explicit
    ``remember("X")`` followed by a hook capturing ``"X\\n"`` should
    dedup, not silently double-write.
    """
    if not content:
        return _EMPTY_HASH
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def producer_kind_from_source(source: str) -> str:
    """Map an event ``source`` label to a stable producer-kind bucket.

    Buckets are coarse on purpose: dedup decisions don't care whether
    the lifecycle hook fired on SessionEnd or PreCompact, only that it
    came from the lifecycle producer family. Keeping the bucket count
    small also keeps the surface tests need to cover small.
    """
    if not source:
        return "unknown"
    if source.startswith("explicit"):
        return "explicit"
    if source in {"hook:before-exit", "hook:before-compact"}:
        return "hook:lifecycle"
    if source == "hook:context-pressure":
        return "hook:context-pressure"
    if source.startswith("hook:"):
        # New hook source we forgot to enumerate — keep it observable
        # rather than silently bucketing it as ``unknown``.
        return source
    return source
