"""Aleph — knowledge layer with two backends.

Two complementary storage layers:

1. **Obsidian vault** (``vault.py``, ``round.py``) — Long-term wiki pages
   at ``~/Documents/Aleph/``, synced via iCloud. Source of truth for
   consolidated knowledge. Follows Karpathy's llm-wiki pattern.
   Intake → AlephRound → create/update pages.

2. **SQLite FTS5 index** (``store/index.py``, ``search.py``, ``api.py``)
   — Per-turn extraction cache. Lightweight keyword search over LLM
   extractions (summary/entities/claims). Faster than vault text search
   for per-turn resolution. May be deprecated in v0.0.3 if vault search
   proves sufficient.

Search priority (``ask(mode=auto)``):
  vault wiki > extraction index > MemPalace raw
"""
