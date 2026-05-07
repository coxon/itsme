"""Content filters for hook captures — T2.0a+.

Each filter is a pure function ``(text) → text`` that strips a class
of noise from raw transcript content.  Filters compose: apply them in
sequence on the raw string before emitting ``raw.captured``.

Filters are only applied to **hook captures**, never to explicit
``remember()`` calls — the agent's deliberate intent is 100% honored.
"""
