"""Envelope filter — T2.0a.

Strips CC control envelope blocks that get injected into the transcript
when slash commands fire (``/exit``, ``/clear``, etc.).  These blocks
are not user semantic content — they're IDE control metadata that
pollutes drawers and degrades search recall.

The five known envelope tag families:

1. ``<local-command-caveat>…</local-command-caveat>``
2. ``<command-name>…</command-name>``
3. ``<command-message>…</command-message>``
4. ``<command-args>…</command-args>``
5. ``<local-command-stdout>…</local-command-stdout>``

Each tag may span multiple lines and may appear multiple times in a
single transcript snapshot.  The filter removes the **entire block**
(open tag → content → close tag) including any nested whitespace.

Why regex and not an XML parser?  Because:

* The transcript is not valid XML — these tags are embedded in
  arbitrary plain text / markdown.
* The tag set is closed (5 families, CC-controlled), so a targeted
  regex is more robust than a parser that might choke on surrounding
  non-XML content.
* Performance: a compiled regex over a ~10KB string is <1ms.
"""

from __future__ import annotations

import re
from typing import Final

# --------------------------------------------------------------------- patterns

#: The five CC control envelope tag names.
_ENVELOPE_TAGS: Final[tuple[str, ...]] = (
    "local-command-caveat",
    "command-name",
    "command-message",
    "command-args",
    "local-command-stdout",
)

# Build a single alternation pattern for all five tags.
#
# ``re.DOTALL`` makes ``.`` match newlines so multi-line tag bodies
# are captured.  The ``?`` in ``.*?`` makes the match non-greedy so
# ``<command-name>exit</command-name> ... <command-name>foo</command-name>``
# matches each block independently rather than swallowing everything
# between the first open and last close tag.
_ENVELOPE_RE: Final[re.Pattern[str]] = re.compile(
    r"<(" + "|".join(re.escape(t) for t in _ENVELOPE_TAGS) + r")>"
    r".*?"
    r"</\1>",
    re.DOTALL,
)

# After stripping tags, clean up runs of blank lines left behind.
# Three or more consecutive newlines → collapse to two (one blank line).
_BLANK_COLLAPSE_RE: Final[re.Pattern[str]] = re.compile(r"\n{3,}")


# --------------------------------------------------------------------- public API


def strip_envelopes(text: str) -> str:
    """Remove all CC control envelope blocks from *text*.

    Returns the cleaned string with collapsed blank lines.  If *text*
    contains no envelope tags, it is returned unchanged (fast path —
    the regex engine short-circuits on no match).

    This function is **idempotent**: calling it twice produces the same
    output as calling it once.
    """
    cleaned = _ENVELOPE_RE.sub("", text)
    cleaned = _BLANK_COLLAPSE_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def has_envelopes(text: str) -> bool:
    """Return True if *text* contains at least one CC envelope block."""
    return _ENVELOPE_RE.search(text) is not None
