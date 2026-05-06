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

`.claude-plugin/plugin.json` (the version in this repo):

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

Hooks are wired separately at `hooks/hooks.json` (CC's plugin spec
loads them from the same root). The four hook entries map to four
shell shims in `hooks/cc/`, each of which dispatches into
`uv run --project ${CLAUDE_PLUGIN_ROOT} python -m itsme.hooks <name>`.

### Hook contract

Each shim:

- Reads CC's hook JSON envelope from stdin (`session_id`,
  `transcript_path`, `cwd`, `hook_event_name`).
- Always exits 0. Hook failures are logged to stderr; surfacing them
  as non-zero exits would render in the CC UI as red errors, which
  is the wrong UX for a passive-capture plugin.
- Never blocks the IDE: timeouts (10-15s per hook) are configured in
  `hooks/hooks.json`. Timeouts are higher than they need to be in
  steady state to absorb the one-time cold-start `uv sync` if a
  hook fires before the MCP server has been activated.

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

## v0.0.1 acceptance criteria

- [x] CC: plugin loads; `remember` / `ask` / `status` show up as MCP tools
- [x] CC: SessionEnd / PreCompact / context-pressure hooks emit `raw.captured`
- [ ] CC: end-to-end smoke (T1.20 — chat → exit → drawer in MP → ask retrieves)
- [ ] Codex: equivalent flow (T1.18 + T1.21)
