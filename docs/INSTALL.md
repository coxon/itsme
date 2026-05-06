# itsme — Installation Matrix

> Status: **v0.0.1a** alpha. CC fully wired, Codex pending T1.18.

---

## Supported IDEs (v0.0.1)

| IDE | Plugin mechanism | Hooks (CC event → script) | Status |
|---|---|---|---|
| **Claude Code** | `/plugin marketplace add coxon/itsme` (or `~/.claude/plugins/<name>` symlink for dev) | `SessionEnd=before-exit`, `PreCompact=before-compact`, `UserPromptSubmit`/`PostToolUse=context-pressure` | ✅ v0.0.1 |
| **Codex** | TBD (driven by T1.18) | semantic equivalents | ⏳ v0.0.1 |
| Cursor / Continue / others | — | — | not planned (v0.0.5+) |

---

## Claude Code

### Install (recommended — CC plugin marketplace)

itsme is its own marketplace (`.claude-plugin/marketplace.json` at
the repo root lists exactly one plugin: itsme itself), so the CC
standard two-step works:

```text
/plugin marketplace add coxon/itsme
/plugin install itsme@itsme
```

Subsequent updates:

```text
/plugin marketplace update itsme    # pull catalog metadata
/plugin install itsme@itsme         # re-install at the new pinned version
```

CC also runs auto-updates in the background at startup.

**Prerequisite**: [`uv`](https://docs.astral.sh/uv/) on `$PATH`. The
MCP server is launched as `uv run --project ${CLAUDE_PLUGIN_ROOT}
python -m itsme.mcp.server`, so uv handles dep resolution from the
plugin's own `pyproject.toml` — no global `pip install itsme` is
needed. First boot pays a one-time `uv sync` (~5-10s); subsequent
spawns reuse the cached venv.

### Install (developer mode — local clone + symlink)

For hacking on itsme:

```bash
git clone https://github.com/coxon/itsme
cd itsme
uv sync

mkdir -p ~/.claude/plugins
ln -snf "$PWD" ~/.claude/plugins/itsme
```

Restart CC. Source edits flow through immediately; `/reload-plugins`
picks them up without restart.

### Plugin manifest shape

`.claude-plugin/plugin.json` (excerpt — the version in this repo
includes the full inline `"hooks"` block; one entry shown for shape):

```json
{
  "name": "itsme",
  "version": "0.0.1a0",
  "description": "Long-term memory plugin for agent IDEs — remember / ask / status",
  "skills": ["./skills/itsme"],
  "mcpServers": {
    "itsme": {
      "command": "uv",
      "args": [
        "run", "--project", "${CLAUDE_PLUGIN_ROOT}",
        "python", "-m", "itsme.mcp.server"
      ]
    }
  },
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/cc/before-exit.sh\"",
            "timeout": 15
          }
        ]
      }
    ]
    // PreCompact, UserPromptSubmit, PostToolUse follow the same shape
    // — see the file in this repo for the full block.
  }
}
```

`.claude-plugin/marketplace.json` (single-plugin self-host, plugin
lives at marketplace root):

```json
{
  "name": "itsme",
  "owner": {"name": "coxon", "url": "https://github.com/coxon/itsme"},
  "plugins": [
    {
      "name": "itsme",
      "source": "./",
      "version": "0.0.1a0"
    }
  ]
}
```

> **Fallback if `"source": "./"` ever stops being accepted.** The CC
> docs say the source path "Must start with `./`" without explicitly
> blessing the bare `./` (root-of-marketplace) form, so if a future
> validator tightens, replace the source with a remote one — the
> repo is also a valid plugin payload by itself:
>
> ```json
> "source": {"source": "github", "repo": "coxon/itsme"}
> ```
>
> This costs one extra clone per install (CC fetches the marketplace
> + the plugin separately) but is bulletproof.

Hooks are wired inline in `.claude-plugin/plugin.json` under the
`"hooks"` field (the four lifecycle / pressure events). Each entry
maps to a shell shim in `hooks/cc/`, and each shim dispatches into
`uv run --project ${CLAUDE_PLUGIN_ROOT} python -m itsme.hooks <name>`.

> **Why inline rather than `hooks/hooks.json`?** CC's plugin spec
> documents *both* forms, but the external-file form has a known
> reliability bug — see [anthropics/claude-code#45296][cc-45296]
> (framework deletes `hooks/hooks.json` from the working tree after
> loading) and #54810 (some marketplace metadata paths fail to
> register external hooks). Inlining sidesteps both. We keep the
> shim scripts in `hooks/cc/` because that part of the spec is
> stable and the path expansion of `${CLAUDE_PLUGIN_ROOT}` works
> identically either way.

[cc-45296]: https://github.com/anthropics/claude-code/issues/45296

### Hook contract

Each shim:

- Reads CC's hook JSON envelope from stdin (`session_id`,
  `transcript_path`, `cwd`, `hook_event_name`).
- Always exits 0. Hook failures are logged to stderr; surfacing them
  as non-zero exits would render in the CC UI as red errors, which
  is the wrong UX for a passive-capture plugin.
- Never blocks the IDE: timeouts (10-15s per hook) are configured in
  the inline `"hooks"` block in `plugin.json`. Timeouts are higher
  than they need to be in steady state to absorb the one-time
  cold-start `uv sync` if a hook fires before the MCP server has
  been activated.

```bash
# hooks/cc/before-exit.sh
#!/usr/bin/env bash
set -u

plugin_root="${CLAUDE_PLUGIN_ROOT:-}"
if [[ -z "${plugin_root}" ]]; then
    echo "itsme hook: CLAUDE_PLUGIN_ROOT unset; skipping capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
    exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "itsme hook: 'uv' not found on PATH; skipping capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
    exit 0
fi

if ! uv run --project "${plugin_root}" python -m itsme.hooks before-exit; then
    echo "itsme hook: bootstrap failed; continuing without capture." >&2
    printf '{"continue": true, "suppressOutput": true}\n'
fi
exit 0
```

### Disable temporarily

```bash
export ITSME_HOOKS_DISABLED=1   # all hooks become no-ops
```

---

## Codex

Pending T1.18 (research Codex's hook API and mirror the contract).
The MCP server is reusable as-is once Codex's plugin packaging is
chosen — only the hook shim layer is IDE-specific.

| Semantic | CC | Codex |
|---|---|---|
| Session ends | `SessionEnd` → `before-exit` | TBD |
| Context will compact | `PreCompact` → `before-compact` | TBD |
| Context pressure tick | `UserPromptSubmit` / `PostToolUse` → `context-pressure` | TBD |

---

## Runtime configuration

v0.0.1 is environment-variable driven. (A `~/.itsme/config.toml`
lands in v0.0.4 — see ROADMAP T4.x.)

| Env | Default | Effect |
|---|---|---|
| `ITSME_DB_PATH` | `~/.itsme/events.db` | SQLite ring buffer location |
| `ITSME_PROJECT` | `default` | Wing prefix for namespacing |
| `ITSME_HOOKS_DISABLED` | _(unset)_ | `1`/`true`/`yes` ⇒ all hooks are no-ops |
| `ITSME_CTX_THRESHOLD` | `0.70` | Fraction of context that triggers proactive salvage |
| `ITSME_CTX_MAX` | `200000` | Assumed context window (override per model) |
| `ITSME_STATE_DIR` | `~/.itsme/state` | Per-session debounce state files |
| `ITSME_MEMPALACE_BACKEND` | `auto` | `auto` (try stdio, fall back to inmemory + warn), `stdio` (hard-fail if missing), or `inmemory` (RAM-only, drawers don't survive MCP restarts) |
| `ITSME_MEMPALACE_COMMAND` | `python3 -m mempalace.mcp_server` | Argv for the MemPalace stdio subprocess (only when backend ≠ `inmemory`) |

The hook process and the MCP server both read the same env vars, so
they always end up writing to the same events ring.

---

## Real-world setup notes

These are the gotchas we hit dogfooding v0.0.1 on macOS + a custom
gateway. Not strictly part of the spec, but the difference between
"works in CI" and "works on your laptop".

### `claude --bare` skips hooks

CC's `--bare` flag enables minimal mode. Per `claude --help`:

> Minimal mode: skip hooks, LSP, plugin sync, attribution, auto-memory,
> background prefetches, keychain reads, and CLAUDE.md auto-discovery.

If you launch CC via a wrapper that adds `--bare` (a common pattern in
zshrc helper functions for swapping API keys / models), itsme's MCP
tools still work but **`SessionEnd` / `PreCompact` / `UserPromptSubmit`
/ `PostToolUse` hooks never fire**. You'll see `raw.captured | explicit`
events from manual `remember` calls but no `hook:before-exit` events.

**Fix:** drop `--bare` from the wrapper. The plugin-sync cost it
guards against is one-time on first install (`uv sync`); steady-state
overhead is negligible.

### Custom gateway: use `ANTHROPIC_AUTH_TOKEN`, not `ANTHROPIC_API_KEY`

When pointing CC at a non-Anthropic gateway (`ANTHROPIC_BASE_URL=...`),
`ANTHROPIC_API_KEY` is **not** the right variable — CC will treat the
session as logged-out and prompt `/login`. Use `ANTHROPIC_AUTH_TOKEN`
instead; it's sent as the bearer token to your gateway and CC accepts
it as a valid auth method without keychain interference.

```bash
export ANTHROPIC_AUTH_TOKEN="sk-..."          # ← not ANTHROPIC_API_KEY
export ANTHROPIC_BASE_URL="https://your-gateway.example.com"
export ANTHROPIC_MODEL="your/model-id"
claude
```

This is independent of itsme but bites first-time users hard enough
that we mention it here.

### Persistent storage: pointing the stdio adapter at MemPalace

itsme's default `ITSME_MEMPALACE_BACKEND=auto` tries to spawn
`python3 -m mempalace.mcp_server` as a subprocess for persistent
storage. Two failure modes are common:

1. **`uv run` finds no mempalace.** The MCP server boots inside
   itsme's `.venv` (managed by `uv`). That venv won't have
   mempalace installed unless you put it there explicitly. The
   subprocess fails with `ModuleNotFoundError: No module named
   'mempalace'`, the adapter logs a warning, and itsme falls back to
   the in-memory adapter — which means **drawers vanish when the MCP
   server exits**.

2. **`mempalace` is shell alias only.** A `~/.zshrc` line like
   `alias mempalace="python3 -m mempalace"` works in your shell but
   subprocess spawns don't see aliases.

**Fix:** point itsme at the system Python (or whatever interpreter has
mempalace installed):

```bash
export ITSME_MEMPALACE_COMMAND="/usr/bin/python3 -m mempalace.mcp_server"
export MEMPALACE_PALACE_PATH="$HOME/Documents/memory"  # mempalace's data dir
```

Verify via:

```python
from itsme.core.adapters.mempalace_stdio import StdioMemPalaceAdapter
a = StdioMemPalaceAdapter.from_env()
print(a.search("anything", limit=3))
a.close()
```

If that prints hits (or even a clean empty list) without raising
`MemPalaceConnectError`, the adapter chain is wired correctly.

### Verifying end-to-end with sqlite

The events ring is your source of truth. After a `/exit` (which fires
SessionEnd) followed by any new session, run:

```bash
sqlite3 ~/.itsme/events.db \
  "SELECT ts, type, source FROM events ORDER BY id DESC LIMIT 10;"
```

A healthy chain looks like:

```
… | memory.stored   | adapter:mempalace
… | memory.routed   | worker:router
… | raw.captured    | hook:before-exit   ← hook fired
```

If you see only `raw.captured | explicit` rows and no `hook:` sources,
hooks aren't firing — re-check the `--bare` and "custom wrapper"
sections above.

If you see `hook:before-exit` but `adapter:mempalace` rows are absent,
the router is running but the stdio adapter isn't connecting — re-read
the "Persistent storage" section.

---

## v0.0.1 acceptance criteria

- [x] CC: plugin loads; `remember` / `ask` / `status` show up as MCP tools
- [x] CC: SessionEnd / PreCompact / context-pressure hooks emit `raw.captured`
- [x] CC: end-to-end smoke (T1.20 — chat → exit → drawer in MP → ask retrieves)
- [ ] Codex: equivalent flow (T1.18 + T1.21)
