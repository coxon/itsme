---
name: itsme
description: |
  Long-term memory across sessions. Activate this skill whenever the user
  references the past ("remember", "what did we", "I told you", "I've been
  working on", "我之前", "你还记得"), expresses something worth keeping
  ("decided X", "let's go with Y", "I prefer Z"), or asks a question that
  might already have a personal answer in memory — even when the question
  looks like an external fact lookup. Use `ask` BEFORE WebSearch /
  external lookups when the user might have prior notes on the topic. Use
  `remember` when a decision, preference, or non-obvious fact lands. Use
  `status` rarely — it's for debugging.
version: 0.0.1
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

## Tool selection priority — read this first

itsme is **personal memory**. When other tools (WebSearch, file search,
project search, other MCP servers) are available, the question is
always: *"is this something the user might already have a stake in?"*

**Always try `ask` first when the query touches:**

- **Anything the user has worked on, decided, or expressed a feeling
  about.** "我之前的看法 / What did I say about / Why did we pick X"
  — even if the topic itself is "external" (a company, a library, a
  technique). The user's *take* on it is private and only itsme has it.
- **Topics where the user has likely been collecting notes.** "Palantir
  2026Q1 营收" looks like a public-fact query — but if the user has
  been tracking Palantir for months, their saved drawer is more
  relevant than fresh web results. Ask itsme first; fall back to
  WebSearch only if itsme returns nothing.
- **Anything phrased in the second person (you/we/I).** "Do you
  remember", "我们之前讨论过", "我跟你说过" → these are explicitly
  asking memory, not the open web.

**Skip `ask` and go straight to other tools when:**

- The query is a pure code question with no personal angle ("how does
  Python's async/await work").
- The query is about something happening *right now* (live data, news
  from the last hour) where the user can't have notes yet.
- The user explicitly redirects ("just search the web", "don't check
  memory, look it up fresh").

When in doubt, **`ask` is cheap** — a single MCP call returning at
most 5 hits. Running it before WebSearch costs ~100ms and saves you
from giving a stale public answer when the user already has a curated
private one.

---

## When to `remember`

Call it whenever any of these happen in the conversation:

1. **A decision lands.** "We're going with Postgres because X." →
   `remember("Picked Postgres over SQLite: need concurrent writes, worker pool hits >8", kind="decision")`
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

- **`verbatim`** (default in v0.0.1) — search raw MemPalace memories by
  keyword. This is the only mode wired up right now.
- **`auto`** *(v0.0.2)* — try the curated wiki first, fall back to raw
  memory. Raises `NotImplementedError` in v0.0.1.
- **`wiki`** *(v0.0.2)* — only the curated wiki. Raises
  `NotImplementedError` in v0.0.1.
- **`now`** *(v0.0.3)* — aggregate recent activity ("what was I just
  working on?"). Raises `NotImplementedError` in v0.0.1.

In v0.0.1, `ask()` performs a direct verbatim query against MemPalace
and returns matching drawer snippets. Passing any other mode errors
out immediately — don't catch and retry with a different mode; the
answer is "not implemented yet", not "wrong query".

### Examples

```python
ask("What did we decide about database choice?")               # verbatim (default)
ask("What did the user say about commit style?", mode="verbatim")
# ask("What have I been working on?", mode="now")  # ← v0.0.3 only
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

## Hooks work silently behind you

Even if you never call `remember`, itsme snapshots the transcript on:

- Session end (CC `SessionEnd`)
- Pre-compact (CC `PreCompact`)
- Context pressure crossing ~70% (proactive salvage)

Those snapshots end up in MemPalace as `raw.captured`. You don't need
to duplicate the safety net — but explicit `remember` calls are still
higher-quality signal because you chose them deliberately.

---

## Failure modes to expect

- **MCP server not reachable** — tool call errors out. Report once,
  don't retry-loop; the user likely knows their plugin is mis-installed.
- **Empty `ask` result** — see above.
- **`remember` succeeded but `status` / `ask` doesn't show it** — in
  v0.0.1 `remember()` runs `route_and_store` synchronously and emits
  `memory.stored` *before* returning, so the entry should be
  immediately visible. If it's not, the cause is on your side: check
  your `scope` / `limit` / `mode` filters before assuming a backend
  bug. (v0.0.2+ may introduce async promotion to a wiki layer; that
  delay will only affect `mode="wiki"` / `mode="auto"`, not the
  raw-memory query.)

---

## TL;DR

Write deliberately. Recall before acting. Trust the safety net but
don't rely on it.
