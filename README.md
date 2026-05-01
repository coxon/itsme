# itsme

> Long-term memory plugin for agent IDEs (Claude Code · Codex coming).
>
> **Status**: `v0.0.1a` — alpha. The capture/recall path works
> end-to-end on Claude Code; everything labelled "v0.0.2+" below is
> still a stub.

itsme gives your agent a memory that survives `/clear`, session
endings, and context compaction. Internally it's two engines —
**MemPalace** (verbatim raw memory + KG) and **Aleph** (LLM-curated
wiki, Obsidian vault) — but the agent only sees three MCP verbs.

---

## What works in v0.0.1

| Surface | State |
|---|---|
| `remember(content, kind?)` | ✅ syncs to MemPalace, returns event + drawer ids |
| `ask(question, mode="verbatim")` | ✅ keyword search across stored memories |
| `ask` with `mode="auto"`/`"wiki"`/`"now"` | ⏳ raises `NotImplementedError` (lands in v0.0.2 / v0.0.3) |
| `status(scope?, format?)` | ✅ reads recent events; `json` + `feed` formats |
| **CC SessionEnd** salvage hook | ✅ snapshots transcript tail on session exit |
| **CC PreCompact** salvage hook | ✅ same, fires before context compaction |
| **Context-pressure** proactive hook | ✅ fires near 70% pressure with Schmitt-trigger debounce |
| MemPalace adapter | ✅ in-memory reference impl; persistent stdio backend in v0.0.2 |
| Aleph wiki / promoter | ❌ v0.0.2 — for now everything stays in raw memory |
| Codex hooks | ❌ v0.0.1 task T1.18 |

---

## Quickstart (Claude Code)

### Prerequisites

- Python 3.12+ on `$PATH`
- `pip install itsme` *(when published)* — for now: clone + editable install

```bash
git clone https://github.com/coxon/itsme
cd itsme
uv sync                    # or: pip install -e .
```

### Wire into Claude Code

Symlink the repo into your CC plugins dir:

```bash
mkdir -p ~/.claude/plugins
ln -s "$(pwd)" ~/.claude/plugins/itsme
```

CC will read `.claude-plugin/plugin.json` on next launch and start the
MCP server (`python -m itsme.mcp.server`) over stdio. Three tools
appear in the model's tool list, and the four CC hooks
(`SessionEnd`, `PreCompact`, `UserPromptSubmit`, `PostToolUse`) get
registered automatically.

### Verify

In a fresh CC session:

```
> use the remember tool to save: "itsme is wired up"
> use the status tool to show recent events
```

You should see one `memory.stored` event with the text you saved.
The same DB at `~/.itsme/events.db` will accumulate events across
sessions.

---

## The three verbs

```python
remember(content, kind=None)
# kind ∈ {decision, fact, feeling, todo, event} — optional router hint.
# Synchronous: routes + stores + emits memory.stored before returning.

ask(question, mode="verbatim", limit=5)
# v0.0.1: only mode="verbatim" works. Other modes raise NotImplementedError.
# Returns up to `limit` matching MemPalace drawers.

status(scope="recent", format="json", limit=20)
# scope ∈ {recent, today, session}; format ∈ {json, feed}.
# Reads from the events ring, not memory; useful for "what did the
# hooks just capture?".
```

For the agent-facing version of this guide (when to call what),
see [`skills/itsme/SKILL.md`](skills/itsme/SKILL.md).

---

## Hooks: silent salvage

Even if you never call `remember`, itsme captures the transcript on:

- **SessionEnd** — snapshot tail before CC tears the session down
- **PreCompact** — snapshot before context auto-compaction
- **Context-pressure** — proactive snapshot near 70% pressure, with a
  10% Schmitt-trigger so the hook doesn't fire repeatedly while
  pressure oscillates

All three append to the same events ring as `raw.captured` with a
`hook:before-exit` / `hook:before-compact` / `hook:context-pressure`
source label. v0.0.2's Aleph promoter will consume those.

To disable hooks for a session: `export ITSME_HOOKS_DISABLED=1`.

---

## Configuration

All config is environment-variable based in v0.0.1 (a config file
lands in v0.0.4). Defaults live in `~/.itsme/`.

| Env | Default | Effect |
|---|---|---|
| `ITSME_DB_PATH` | `~/.itsme/events.db` | SQLite ring buffer location |
| `ITSME_PROJECT` | `default` | Wing prefix for namespacing |
| `ITSME_HOOKS_DISABLED` | _(unset)_ | Set to `1`/`true`/`yes` to silence hooks |
| `ITSME_CTX_THRESHOLD` | `0.70` | Pressure fraction that fires the proactive hook |
| `ITSME_CTX_MAX` | `200000` | Assumed context window (override per model) |
| `ITSME_STATE_DIR` | `~/.itsme/state` | Per-session debounce state files |

---

## Roadmap

| Version | Scope | ETA |
|---|---|---|
| **v0.0.1** | Capture path + 3 MCP verbs + CC hooks (this) | in progress |
| **v0.0.2** | Aleph MVP — extract / write / wiki search; promoter consumes hook captures | ~3-4 weeks |
| **v0.0.3** | `ask(promote=true)` reverse-promotion + embedding search | — |
| **v0.0.4** | Curator (dedup, KG invalidation), full skill polish | — |
| **v0.0.5+** | Misc IDE adapters, performance, multi-user | — |

Detailed task list: [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — dual-engine design, EventBus, Aleph pipeline
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — milestone breakdown
- [`docs/INSTALL.md`](docs/INSTALL.md) — per-IDE install matrix
- [`docs/ICONS.md`](docs/ICONS.md) — icon assets per phase
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branching, commits, review flow
- [`skills/itsme/SKILL.md`](skills/itsme/SKILL.md) — the script the agent reads

---

## Repo

<https://github.com/coxon/itsme>

## License

MIT — see `pyproject.toml` (a top-level `LICENSE` file lands with
v0.0.5).
