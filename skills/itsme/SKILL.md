---
name: itsme
description: Long-term memory across sessions. Use remember() to persist important facts/decisions/feelings, ask() to recall, status() to see what was captured recently.
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

- **`auto`** (default) — tries the wiki first, falls back to raw
  memory. Use this 95% of the time.
- **`verbatim`** — you specifically need the original phrasing (e.g.
  to quote the user back to themselves).
- **`wiki`** — skip raw memory; only show curated entries.
- **`now`** — aggregate recent activity. Useful for "what was I just
  working on?"

### Examples

```
ask("What did we decide about database choice?")            # auto
ask("What did the user say about commit style?", mode="verbatim")
ask("What have I been working on?", mode="now")
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
- **`remember` returns an id but `status` doesn't show it yet** —
  normal; there's a short async delay from write → routed → visible.

---

## TL;DR

Write deliberately. Recall before acting. Trust the safety net but
don't rely on it.
