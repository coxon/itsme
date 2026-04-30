"""itsme core — engines, workers, adapters. NOT exposed to agents.

Layout:
- events/    sqlite ring buffer + envelope schema
- workers/   router / promoter / curator / reader
- adapters/  MemPalace adapter (Aleph is in-process at core/aleph)
- aleph/     in-process wiki manager (built from scratch, see §7.2)
- llm.py     LLM provider abstraction (Anthropic first)
"""
