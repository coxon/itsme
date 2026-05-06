# Troubleshooting

Symptom-driven index for v0.0.1. If something looks broken, find the
closest match below before suspecting a code bug — the v0.0.1 dogfood
on macOS uncovered several config gotchas that **look** like bugs but
are environment issues. Every entry follows the same shape: **what
you see → why → how to fix**.

If your symptom isn't listed, file an issue with the output of:

```bash
sqlite3 ~/.itsme/events.db \
  "SELECT ts, type, source FROM events ORDER BY id DESC LIMIT 20;"
```

— that ring is the canonical "did anything happen?" check.

---

## "Hooks never fire — only `explicit` events show up"

**What you see.** After `/exit` and reopening CC, the events ring
contains `raw.captured | explicit` rows from manual `remember` calls
but **zero** `raw.captured | hook:*` rows. Passive capture seems
broken.

**Why.** Almost always one of:

1. **CC was launched with `--bare`.** `claude --bare` documents this
   exactly: "skip hooks, LSP, plugin sync, …". Common in zshrc
   wrapper functions for swapping API keys / models.
2. **The plugin manifest's `hooks/hooks.json` got eaten by CC.** A
   known CC bug ([anthropics/claude-code#45296][45296]) deletes the
   external `hooks/hooks.json` after loading. itsme works around this
   by inlining the entire `hooks` block into `.claude-plugin/plugin.json`
   (PR #16); make sure you're on a recent enough plugin version.

**Fix.**

1. Drop `--bare` from your wrapper. Steady-state cost is negligible;
   the only thing the flag was buying you (skipping `uv sync` cold
   start) is a one-time hit on first install.
2. Reinstall the plugin: `/plugin marketplace update itsme &&
   /plugin install itsme@itsme`.
3. Verify hooks are registered: in CC type `/plugin` and look for
   itsme — its hooks count should be ≥ 4 (SessionEnd, PreCompact,
   UserPromptSubmit, PostToolUse).

[45296]: https://github.com/anthropics/claude-code/issues/45296

---

## "CC keeps prompting `/login` despite `ANTHROPIC_API_KEY` being set"

**What you see.** After exporting `ANTHROPIC_API_KEY` and pointing
`ANTHROPIC_BASE_URL` at a custom gateway (Asia/EU/private LLM proxy,
LiteLLM, OpenRouter, etc.), CC starts up showing `Not logged in ·
Please run /login` and refuses to call the model.

**Why.** When `ANTHROPIC_BASE_URL` is non-default, CC's auth path
expects `ANTHROPIC_AUTH_TOKEN` (passed as the bearer token to your
gateway), **not** `ANTHROPIC_API_KEY` (which CC reserves for
api.anthropic.com). The two variable names are not interchangeable.

**Fix.**

```bash
unset ANTHROPIC_API_KEY                       # remove the wrong one
export ANTHROPIC_AUTH_TOKEN="sk-…"            # ← the right one
export ANTHROPIC_BASE_URL="https://your-gw"
export ANTHROPIC_MODEL="your/model-id"
claude
```

This is a CC-level concern, not itsme — but it kept us locked out for
an hour during dogfood, so it's documented here.

---

## "`ask` returns nothing even though hooks fire"

**What you see.** The events ring shows `raw.captured | hook:before-exit`
rows. Each is followed by `memory.routed` and `memory.stored | adapter:mempalace`.
But asking in a *new* CC session returns "no memories found".

**Why.** itsme is correctly running its in-memory adapter
(`InMemoryMemPalaceAdapter`), which is **RAM-only**. When the MCP
server process (which is the CC session) exits, the adapter's
drawer dictionary vanishes. The events ring's `memory.stored` event
still records a `drawer_id`, but the actual drawer content is gone.

The `auto` backend (default since [PR #15][pr-15]) tries to spawn the
real `mempalace` MCP server as a subprocess, but **falls back silently
to in-memory when mempalace isn't importable in the spawn environment**.

[pr-15]: https://github.com/coxon/itsme/pull/15

**Why mempalace isn't importable.** The default spawn command is
`python3 -m mempalace.mcp_server`. Two failure modes:

1. **Wrong Python.** itsme's MCP server runs inside its own `uv`-managed
   `.venv`. That venv doesn't have mempalace installed unless you put
   it there.
2. **Shell alias instead of binary.** A `~/.zshrc` line like
   `alias mempalace="python3 -m mempalace"` works in your interactive
   shell, but `subprocess.Popen` in itsme can't see aliases.

**Fix.** Point itsme at the interpreter that *does* have mempalace
installed. On macOS with the system Python that's `/usr/bin/python3`;
on Linux, Homebrew, or pyenv it'll be elsewhere. Find it by running
`command -v python3` (or `pyenv which python3`) in the shell where
`python3 -c "import mempalace"` succeeds, then paste that absolute
path into the env var. Don't try to embed `$(...)` substitutions in
the env var itself — itsme `shlex.split`-s the value and exec's it
directly, so command substitutions are not evaluated.

```bash
# Substitute the absolute path you got from `command -v python3`:
export ITSME_MEMPALACE_COMMAND="/usr/bin/python3 -m mempalace.mcp_server"
export MEMPALACE_PALACE_PATH="$HOME/Documents/memory"   # mempalace's data dir
```

Verify the connection (replace the python path the same way):

```bash
ITSME_MEMPALACE_COMMAND='/usr/bin/python3 -m mempalace.mcp_server' \
MEMPALACE_PALACE_PATH="$HOME/Documents/memory" \
uv run python -c "
from itsme.core.adapters.mempalace_stdio import StdioMemPalaceAdapter
a = StdioMemPalaceAdapter.from_env()
print('connected; search smoke:', len(a.search('test', limit=3)), 'hits')
a.close()
"
```

A clean line of output (no traceback) confirms the chain is wired.
After this, drawers persist across MCP-server restarts.

---

## "CC ignores itsme and uses WebSearch for everything"

**What you see.** You ask a question that should hit private memory
("我之前对 X 怎么看", "Palantir 财报", "the bug we hit last week") and
CC immediately calls WebSearch / a different MCP server / nothing
relevant — never reaches for `mcp__itsme__ask`.

**Why.** Three contributing factors:

1. **Skill never loaded.** Run `/reload-plugins` in CC. If the output
   line says `0 skills`, the skill file isn't being discovered. The
   most common cause is an unrecognized field in
   `.claude-plugin/plugin.json` — CC silently drops *all* skill
   loading for the plugin when it hits one (see
   [r/ClaudeCode bug report][skill-bug] and
   [claude-skills#538][skill-538]). itsme's `plugin.json` used to
   declare `"skills": ["./skills/itsme"]`, which is non-standard
   (CC discovers skills by convention from `skills/<name>/SKILL.md`)
   — that field was removed in a later patch. If you're on an
   older install, `/plugin marketplace update itsme && /plugin
   install itsme@itsme` to pick up the fix.
2. **Tool selection competition.** If you also have mempalace's CC
   plugin enabled, mempalace exposes ~19 tools that overlap heavily
   with itsme's 3 (`mempalace_search`, `mempalace_get_taxonomy`, …).
   The model picks the more specific-looking name.
3. **Skill description not strong enough on routing priority.**
   Earlier itsme skill versions described *what* the tools do but not
   *when to prefer them*. The current `skills/itsme/SKILL.md` includes
   a "Tool selection priority" section telling the model to try `ask`
   before WebSearch when the query has any personal angle. If your
   plugin install predates that change, update via the marketplace.

[skill-bug]: https://www.reddit.com/r/ClaudeCode/comments/1qkygri/bug_adding_fields_to_pluginjson_silently_breaks/
[skill-538]: https://github.com/alirezarezvani/claude-skills/issues/538

**Fix.**

1. **Confirm the skill is loaded.** After `/reload-plugins`, the count
   line should include itsme's skill (in CC v2.1.x the skill may be
   counted under `agents` rather than `skills`; what matters is that
   the number went up by one when itsme was installed). If it didn't,
   check `.claude-plugin/plugin.json` for unrecognized fields.

2. **Disable mempalace's CC plugin** (you keep the pip package — itsme
   uses it as a subprocess for storage, but you don't need the 19 raw
   tools cluttering CC's tool list). Note: `enabledPlugins[...] =
   false` in `settings.json` is sometimes ignored by `/reload-plugins`;
   the reliable path is `/plugin uninstall mempalace@mempalace`:

   ```bash
   /plugin uninstall mempalace@mempalace
   /reload-plugins
   ```

3. Update itsme:
   ```bash
   /plugin marketplace update itsme && /plugin install itsme@itsme
   ```

4. If the model *still* skips itsme, you can always force it by being
   explicit ("ask itsme: …"). The model isn't broken, it just needs
   the cue. Older Opus versions (4.6 and earlier) respect skill
   descriptions less strongly than 4.7+ — if you're on 4.6, the
   explicit cue is sometimes the path of least resistance.

---

## "MCP server keeps restarting / `Plugin offline` flashes in CC"

**What you see.** Bottom-of-screen indicator shows itsme as offline /
restarting; tool calls intermittently error out.

**Why.** Three plausible causes, in decreasing likelihood:

1. **`uv sync` is failing.** First fire after install pays a one-time
   `uv sync` cost (~5-10s). If your network can't reach PyPI / your
   CA bundle is wrong, the sync fails repeatedly and CC keeps
   respawning a dead server.
2. **A hook subprocess is crashing on stdin parsing.** Look for
   `ModuleNotFoundError`, `IndexError`, or JSON parse errors in
   the CC debug log: `claude --debug hooks --debug-file /tmp/cc.log`.
3. **Stdio adapter handshake timeout.** If `python3 -m mempalace.mcp_server`
   takes more than 5 seconds to print its first JSON-RPC line (for
   example because it's pulling chromadb deps), the adapter gives up.
   Increase the timeout: `export ITSME_MEMPALACE_HANDSHAKE_TIMEOUT=15`.

**Fix.** Tail `~/.claude/logs/` and the CC debug log; the actual
exception text usually points right at the problem.

---

## "`status` shows recent activity I don't recognize"

**What you see.** `status(scope="recent")` lists `raw.captured` entries
whose content you don't remember writing.

**Why.** Working as designed. itsme passively captures transcript
tails on `SessionEnd` / `PreCompact` / context-pressure crossings.
Anything you typed (or that Claude said) in a session that exited or
compacted will appear as `raw.captured | hook:before-exit` /
`hook:before-compact` / `hook:context-pressure`.

If you don't want passive capture, set `ITSME_HOOKS_DISABLED=1` in
your environment.

If you want passive capture in general but not for a particular
session, there's currently no per-session runtime opt-out — that
feature is on the v0.0.2 roadmap. For now, launch that one session
with `ITSME_HOOKS_DISABLED=1` set in its environment.
