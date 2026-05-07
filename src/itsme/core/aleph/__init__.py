"""Aleph — knowledge wiki layer (Obsidian markdown).

Long-term wiki pages at ``~/Documents/Aleph/``, synced via iCloud.
Source of truth for consolidated knowledge. Follows Karpathy's
llm-wiki pattern.

Pipeline: Intake → AlephRound → create/update wiki pages.

Modules:
- ``wiki.py`` — Read/write adapter (:class:`Aleph`) for the Obsidian
  wiki. Handles page I/O, frontmatter parsing, search, index/log.
- ``round.py`` — LLM-powered wiki consolidation (decides create vs
  update, generates frontmatter + body).

Search (``ask(mode=auto)``):
  wiki pages > MemPalace raw

The SQLite FTS5 per-turn extraction index was removed in T3.0 —
wiki pages + MemPalace raw provide sufficient coverage without
the intermediate layer.
"""

from itsme.core.aleph.wiki import Aleph, AlephVault, IndexEntry, PageHit, PageMeta

__all__ = ["Aleph", "AlephVault", "IndexEntry", "PageHit", "PageMeta"]
