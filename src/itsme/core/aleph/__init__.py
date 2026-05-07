"""Aleph — knowledge layer (Obsidian vault).

Long-term wiki pages at ``~/Documents/Aleph/``, synced via iCloud.
Source of truth for consolidated knowledge. Follows Karpathy's
llm-wiki pattern.

Pipeline: Intake → AlephRound → create/update vault pages.

Modules:
- ``vault.py`` — Read/write adapter for the Obsidian vault.
- ``round.py`` — LLM-powered wiki consolidation (decides create vs
  update, generates frontmatter + body).

Search (``ask(mode=auto)``):
  vault wiki > MemPalace raw

The SQLite FTS5 per-turn extraction index was removed in T3.0 —
vault pages + MemPalace raw provide sufficient coverage without
the intermediate layer.
"""
