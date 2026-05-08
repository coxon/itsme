---
name: itsme
description: |
  Long-term personal memory across sessions. ALWAYS call `ask` BEFORE
  WebSearch / external lookups whenever the query mentions a person,
  company, project, library, or topic the user might have notes on —
  even when phrased as a fresh-fact lookup ("最新", "latest", "财报",
  "earnings"). The user's stored knowledge is often more
  authoritative than fresh web results for anything they actively
  track. Activation triggers: past references ("remember", "what did
  we", "I told you", "I've been working on", "我之前", "你还记得"),
  decisions/preferences worth keeping ("decided X", "let's go with Y",
  "I prefer Z"), or any second-person framing ("you/we/I"). Use
  `remember` when a decision, preference, or non-obvious fact lands.
  Use `status` rarely — it's for debugging.
version: 0.0.4
---

# itsme — memory for your agent

You have long-term memory via three MCP tools:

- **`remember(content, kind?)`** — persist a verbatim note
- **`ask(question, mode?)`** — recall from memory
- **`status(scope?, format?)`** — see what was captured recently

Everything you remember survives across sessions. Everything you forget
(context compaction, `/clear`, session end) is silently salvaged by
hooks — but that salvage is a safety net, **not a substitute for
deliberate writes**.

---

## The one rule

> **Before WebSearch, before external lookups, before answering from
> training data — if the query touches anything the user might have
> private notes on, call `ask` first.** It's one cheap MCP call. The
> stored knowledge is the canonical answer; the web is the fallback.

---

## Tool selection priority — read this first

itsme is **personal memory**. When other tools (WebSearch, file search,
project search, other MCP servers) are available, the question is
always: *"is this something the user might already have a stake in?"*

**Always try `ask` first when the query touches:**

- **Anything the user has worked on, decided, or expressed a feeling
  about.** "我之前的看法 / What did I say about / Why did we pick X"
  — even if the topic itself is "external" (a company, a library, a
  technique). The user's *take* on it is private and only itsme has it.
- **Any named entity the user might be tracking.** A company, ticker,
  library, paper, person, project — if the user has mentioned it
  before, they likely have a drawer on it. **Default assumption: yes,
  they do.** Verify with `ask` before defaulting to a public source.
- **Anything phrased in the second person (you/we/I).** "Do you
  remember", "我们之前讨论过", "我跟你说过" → these are explicitly
  asking memory, not the open web.

**Skip `ask` and go straight to other tools when:**

- The query is a pure code question with no personal angle ("how does
  Python's async/await work").
- The query is about something happening *right now* (live data, news
  from the last hour) where the user can't have notes yet.
- The user explicitly redirects ("just search the web", "don't check
  memory, look it up fresh", "ignore what I said before").

When in doubt, **`ask` is cheap** — a single MCP call returning at
most 5 hits. Running it before WebSearch costs ~100ms and saves you
from giving a stale public answer when the user already has a curated
private one.

---

## Anti-patterns — don't do these

These are the failure modes we've actually observed. Each one looks
reasonable in isolation but produces a worse answer than `ask`-first.

### ❌ "最新 X" / "latest X" / "current X" → straight to WebSearch

> User: "Palantir 最新的财报怎么样？"
> Bad: → `WebSearch("Palantir Q1 2026 earnings")`
> Good: → `ask("Palantir 财报")` **first**, then optionally
> supplement with WebSearch if the stored notes are stale.

**Why:** "最新 / latest" does **not** override memory priority. The
user's stored notes about Palantir are often *more* recent and *more*
relevant than what's on the web — they've been curating this
specifically.

### ❌ Named entity → answer from training data

> User: "PostgreSQL 16 有什么新特性？"
> Bad: → answer from training-data knowledge of PG16.
> Good: → `ask("Postgres 16")` first. The user may have notes
> on which features they've adopted, which they hit bugs in, etc.

### ❌ "你帮我看看 X" / "tell me about X" → external search

> User: "你帮我看看 Aleph 这个项目"
> Bad: → web search for "Aleph project".
> Good: → `ask("Aleph")` first; this is the user's own project.

### ❌ Empty `ask` → ask same question 3 different ways

If `ask` returns no hits, **that's the answer**. Don't reformulate and
retry — the topic genuinely isn't in memory yet. Move on to the next
tool (WebSearch, file search) or ask the user directly.

---

## When to `remember`

Call it whenever any of these happen in the conversation:

1. **A decision lands.** "We're going with Postgres because X." →
   `remember("Picked Postgres over SQLite: need concurrent writes", kind="decision")`
2. **A non-obvious fact surfaces.** "Actually the API returns 204 not
   200 on empty queues." → `remember(..., kind="fact")`
3. **The user expresses a preference or feeling you'd want to honor
   later.** "I hate abbreviated git commits." →
   `remember(..., kind="feeling")`
4. **A todo gets committed to but not done.** "Refactor the retry
   logic once we ship v1" → `remember(..., kind="todo")`
5. **A project milestone / named event.** "Merged PR #42, v0.2
   released." → `remember(..., kind="event")`

### `kind` guidance

`kind` is **optional** — it's a hint to the router. When you pass it
the write goes fast-path (no LLM classification). When you don't, the
router infers. Prefer passing it when confident, omit when ambiguous.

Valid values: `decision` / `fact` / `feeling` / `todo` / `event`.

### What **NOT** to remember

- **Ephemeral state.** "Currently on line 42 of foo.py." The file
  itself is the source of truth.
- **Things the user can see in the transcript right now.** Remember
  them when they'd be lost on `/clear`, not the moment they happen.
- **Raw tool output.** Summarize the *takeaway*, not the blob.
- **Secrets.** Keys, tokens, credentials — never.
- **Duplicates.** Before writing a near-copy, consider
  `ask(question=..., mode="verbatim")` first.

### Content shape

- One fact per call. Don't concatenate 5 unrelated items.
- Verbatim > paraphrased when quoting the user.
- Include context the future you will need: *why* this mattered, not
  just *what* happened.

---

## When to `ask`

Any time the user references something from a past session, or you'd
benefit from knowing "what did we decide about X?" before acting.

### Modes

| Mode | What it searches | When to use |
|------|-----------------|-------------|
| **`auto`** (recommended) | Wiki pages + wiki embedding + MemPalace raw — three legs merged and deduped | Default for most queries. Consolidated wiki knowledge first, raw memories as fallback. |
| **`wiki`** | Aleph wiki pages only (keyword match on title/alias/summary/body) | When you want curated knowledge without raw noise. |
| **`verbatim`** | MemPalace raw memories only (embedding search) | When you want the user's exact words, or wiki doesn't cover the topic yet. |

**Use `mode="auto"` unless you have a specific reason not to.** It
gives you the best of both worlds: high-precision wiki hits plus
high-recall raw memory as a safety net.

### Examples

```python
ask("What did we decide about database choice?")                    # auto (default)
ask("星图计划的负责人是谁？", mode="auto")                             # auto — wiki will hit
ask("What exactly did the user say about commit style?", mode="verbatim")  # raw words
ask("海龙", mode="wiki")                                              # wiki page lookup
```

### When `ask` returns nothing

**That's signal, not failure.** It means either (a) this topic was
never remembered, or (b) the wiki hasn't been built yet for this
project. Don't keep asking the same question three different ways.
Move on or ask the user directly.

---

## When to `status`

Rarely. It's a debugging / observability tool:

- User asks "what have you been saving?"
- You want to verify a `remember` call landed
- Investigating why memory seems stale

Formats: `json` for further processing, `feed` for showing the user.

---

## How memory works behind the scenes

### Hooks — silent salvage

Even if you never call `remember`, itsme snapshots the transcript on:

- Session end (CC `SessionEnd`)
- Pre-compact (CC `PreCompact`)
- Context pressure crossing ~70% (proactive salvage)

Those snapshots go through an LLM intake pipeline that:
1. Filters out boilerplate (tool output, CC envelopes)
2. Splits into per-turn segments
3. Classifies each turn as keep or skip
4. Writes ALL turns to MemPalace (raw, for full recall)
5. Consolidates KEEP turns into wiki pages (Obsidian Aleph vault)
6. Runs wiki maintenance (dedup paragraphs, insert crosslinks)

Explicit `remember` calls bypass all of this — they're faster and
higher-quality because you chose them deliberately.

### Two-engine architecture

```text
MemPalace (raw)              Aleph (wiki)
─────────────                ──────────────
verbatim · high recall       curated · high precision
"what was said"              "what was learned"
```

`ask(mode="auto")` queries both and merges results.

---

## Failure modes to expect

- **MCP server not reachable** — tool call errors out. Report once,
  don't retry-loop.
- **Empty `ask` result** — see above. Move on.
- **`remember` succeeded but `ask` doesn't show it** — `remember`
  writes to MemPalace synchronously (immediately visible via
  `mode="verbatim"`). Wiki consolidation is async (hooks only),
  so explicit `remember` content won't appear in `mode="wiki"`
  until the next hook capture triggers a wiki round.

---

## TL;DR

**`ask` before WebSearch. `ask` before training-data answers.
`ask` even when the query says "最新 / latest".** The user's
knowledge beats the web for anything they track.

Write deliberately via `remember`. Recall before acting via `ask`.
Trust the hooks' safety net but don't rely on it.
Use `mode="auto"` for the best search coverage.
