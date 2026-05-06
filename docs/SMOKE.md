# itsme — manual smoke runbook (T1.20)

> **Audience**: maintainers tagging a v0.0.1 release, or anyone debugging
> a "memory feels broken" report. The automated smoke under
> `tests/smoke/` covers the in-process + bash-shim layers; this doc
> covers what only a real Claude Code session can validate.

**Time required**: ~5 min for the happy path, ~15 min if you exercise
hooks + cross-session.

---

## Pre-flight

```bash
uv run pytest tests/smoke/ -v   # 17 tests, ~2s — must all be green
```

If the automated smoke fails, **stop here** — the bash shims, MCP
boot, or router consume loop is broken; the manual run will only
duplicate that finding.

---

## Install the plugin

Pick one of two paths.

**Marketplace** (matches what end users will do):

```text
/plugin marketplace add coxon/itsme
/plugin install itsme@itsme
```

**Developer symlink** (matches what maintainers do while iterating):

```bash
# from a checkout of github.com/coxon/itsme
ln -snf "$PWD" ~/.claude/plugins/itsme
uv sync                                  # primes the venv
```

Both routes need [`uv`](https://docs.astral.sh/uv/) on `$PATH` — the
plugin spawns its MCP server via `uv run --project
${CLAUDE_PLUGIN_ROOT} python -m itsme.mcp.server` so deps resolve from
the plugin's own `pyproject.toml`.

Restart Claude Code. The MCP server is auto-spawned by the plugin
manifest; you should see `itsme` in `/mcp` listing within ~2-10s of
CC boot (longer on first launch — uv has to sync deps once).

---

## Smoke matrix

Mark each row ✅ pass / ❌ fail / ➖ N/A as you go. Times are wall-clock
estimates against a fresh ~/.itsme/events.db.

### A. Boot

| #  | Step                                                  | Expected                                                       | OK |
|----|-------------------------------------------------------|----------------------------------------------------------------|----|
| A1 | Open CC in any project                                | No red error toast about itsme                                 |    |
| A2 | Run `/mcp` in CC                                      | `itsme` listed, status connected                               |    |
| A3 | `ls ~/.itsme/`                                        | `events.db` exists (created on first hook fire OR first verb)  |    |

### B. Capture (explicit)

| #  | Step                                                                                        | Expected                                                       | OK |
|----|---------------------------------------------------------------------------------------------|----------------------------------------------------------------|----|
| B1 | "Use the remember tool to save: 'B1 smoke test entry'"                                      | Tool returns JSON with `drawer_id`, `stored_event_id`           |    |
| B2 | "Use the status tool to show recent events"                                                 | At least one `raw.captured` and one `memory.stored` row        |    |
| B3 | "Use the ask tool to query: 'B1 smoke'"                                                     | Returns the B1 content as a source                              |    |

### C. Capture (passive — lifecycle hook)

| #  | Step                                                                                        | Expected                                                       | OK |
|----|---------------------------------------------------------------------------------------------|----------------------------------------------------------------|----|
| C1 | Have a real conversation (≥ 3 turns) then close the CC window cleanly                       | (no UI feedback expected)                                       |    |
| C2 | Reopen CC, "Use the status tool to show recent events"                                      | A `raw.captured` row with `source=hook:before-exit`             |    |
| C3 | Inspect that row's payload                                                                  | Has `transcript_ref.path` pointing to a CC transcript file     |    |

### D. Capture (passive — pre-compact hook)

| #  | Step                                                                                        | Expected                                                       | OK |
|----|---------------------------------------------------------------------------------------------|----------------------------------------------------------------|----|
| D1 | In a long session, run `/compact`                                                           | (no UI feedback expected)                                       |    |
| D2 | After compact completes, "Use the status tool to show recent events"                        | A `raw.captured` row with `source=hook:before-compact`          |    |

### E. Capture (passive — context-pressure hook)

> Tip: easiest way to trigger is to run a few large file reads /
> long-output commands until the CC context indicator climbs past 70%.

| #  | Step                                                                                        | Expected                                                       | OK |
|----|---------------------------------------------------------------------------------------------|----------------------------------------------------------------|----|
| E1 | Drive CC context to ~70%+                                                                   | A toast like `itsme: captured at 72% context pressure`          |    |
| E2 | Status check                                                                                | New `raw.captured` row with `source=hook:context-pressure`      |    |
| E3 | Continue working without dipping pressure significantly                                     | NO additional pressure rows (Schmitt-trigger debounce works)    |    |
| E4 | Run `/compact` then push pressure back over 70%                                             | One more pressure capture (re-armed after relief)               |    |

### F. Cross-session — known v0.0.1 gap

This documents a real bug we shipped knowingly (tracked: T1.13.5).
Don't fail the release on F2; flip the expectation when T1.13.5 lands.

| #  | Step                                                                                        | Expected (v0.0.1)                                               | OK |
|----|---------------------------------------------------------------------------------------------|----------------------------------------------------------------|----|
| F1 | In session 1: `remember "F-cross-session-token"`                                            | Stored OK, ask("F-cross-session-token") returns it              |    |
| F2 | Quit CC fully (kill the MCP server). Reopen CC. ask("F-cross-session-token")                | Returns nothing (drawer was in-RAM, lost on restart)            |    |
| F3 | But: status shows the historical `raw.captured` + `memory.stored` rows still present       | Yes (events.db is sqlite-persistent)                            |    |

### G. Reliability

| #  | Step                                                                                        | Expected                                                       | OK |
|----|---------------------------------------------------------------------------------------------|----------------------------------------------------------------|----|
| G1 | `ITSME_HOOKS_DISABLED=1` then exit a CC session                                            | No new `raw.captured` row appears in status                     |    |
| G2 | Make `~/.itsme/events.db` read-only, exit a CC session                                      | CC does NOT show a red hook error (graceful 0-exit)             |    |

---

## Triaging failures

### A1 fails — itsme MCP not loading

1. `cat ~/.claude/plugins/itsme/.claude-plugin/plugin.json` — must
   have `mcpServers.itsme.command = "uv"` and args including
   `--project ${CLAUDE_PLUGIN_ROOT}`.
2. `uv --version` — must be on `$PATH`. Install via
   `curl -LsSf https://astral.sh/uv/install.sh | sh` if missing.
3. From the plugin dir: `uv run --project . python -m itsme.mcp.server`
   — should hang waiting for stdin (that's correct; Ctrl-C to exit).
   First run takes 5-10s for the venv sync.
4. Check CC's MCP log (varies by CC version).

### B/C/D fail but A passes — events ring not being written

1. `sqlite3 ~/.itsme/events.db 'select count(*) from events'` —
   should be > 0 after any activity.
2. `ITSME_DB_PATH` set to something weird? `env | grep ITSME`.
3. Permissions on `~/.itsme/`?

### E fails — pressure hook never fires

1. `cat ~/.itsme/state/pressure-*.json` — there should be one file
   per session. If `armed:false` and `last_triggered` is high, the
   hook is debounce-disarmed and waiting for relief.
2. `ITSME_CTX_THRESHOLD` / `ITSME_CTX_MAX` set to weird values?
3. `python -m itsme.hooks context-pressure` won't fire without real
   stdin; this is by design (silence on bad input).

### F2 returns the token after restart — your release is BETTER than expected

T1.13.5 (persistent MemPalace adapter) likely landed without flipping
this row. Update the expectation.

---

## Recording results

For release tags, drop a copy of this file with the table filled in
into `docs/release-notes/v0.0.1-smoke-<date>.md`. The matrix is
intentionally short so this is a 5-minute exercise, not a chore.
