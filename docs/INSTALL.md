# itsme — Installation Matrix

> Status: **v0.0.1a** alpha. CC fully wired, Codex pending T1.18.

---

## Supported IDEs (v0.0.1)

| IDE | Plugin mechanism | Hooks (CC event → script) | Status |
|---|---|---|---|
| **Claude Code** | `~/.claude/plugins/<name>` symlink | `SessionEnd=before-exit`, `PreCompact=before-compact`, `UserPromptSubmit`/`PostToolUse=context-pressure` | ✅ v0.0.1 |
| **Codex** | TBD (driven by T1.18) | semantic equivalents | ⏳ v0.0.1 |
| Cursor / Continue / others | — | — | not planned (v0.0.5+) |

---

## Claude Code

### Install (alpha — local clone)

```bash
git clone https://github.com/coxon/itsme
cd itsme
uv sync                     # or: pip install -e .

# Wire into CC
mkdir -p ~/.claude/plugins
ln -s "$(pwd)" ~/.claude/plugins/itsme
```

Restart CC. The plugin manifest (`.claude-plugin/plugin.json`) is
discovered automatically.

> A future `cc plugin install https://github.com/coxon/itsme`
> path is on the v0.0.5+ roadmap once we ship a real wheel.

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
      "command": "python",
      "args": ["-m", "itsme.mcp.server"]
    }
  }
}
```

Hooks are wired separately at `hooks/hooks.json` (CC's plugin spec
loads them from the same root). The four hook entries map to four
shell shims in `hooks/cc/`, each of which dispatches into
`python -m itsme.hooks <name>`.

### Hook contract

Each shim:

- Reads CC's hook JSON envelope from stdin (`session_id`,
  `transcript_path`, `cwd`, `hook_event_name`).
- Always exits 0. Hook failures are logged to stderr; surfacing them
  as non-zero exits would render in the CC UI as red errors, which
  is the wrong UX for a passive-capture plugin.
- Never blocks the IDE: timeouts (3-5s per hook) are configured in
  `hooks/hooks.json`.

```bash
# hooks/cc/before-exit.sh
#!/usr/bin/env bash
set -u
python -m itsme.hooks before-exit
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

The hook process and the MCP server both read the same env vars, so
they always end up writing to the same events ring.

---

## v0.0.1 acceptance criteria

- [x] CC: plugin loads; `remember` / `ask` / `status` show up as MCP tools
- [x] CC: SessionEnd / PreCompact / context-pressure hooks emit `raw.captured`
- [ ] CC: end-to-end smoke (T1.20 — chat → exit → drawer in MP → ask retrieves)
- [ ] Codex: equivalent flow (T1.18 + T1.21)
