# CLAUDE.md

> Project guidance for Claude Code. Read once per session.

## Project

**itsme** — long-term memory plugin for agent IDEs (CC · Codex). Python. v0.0.x · design phase.

## Hard rules

- ❌ No direct commit / push to `main`. Use `feature/*` + PR.
- ❌ No `--force` / `--no-verify` unless user authorizes in this session.
- ❌ Don't commit / push / open PR unless asked.
- ❌ Don't invent alternatives to locked decisions (see `docs/ARCHITECTURE.md` §10 + §12).
- ✅ Conventional Commits.
- ✅ If architecture changes → update `docs/ARCHITECTURE.md`. If scope changes → update `docs/ROADMAP.md`.

## Where to look

| Need | File |
|---|---|
| Architecture, decisions, flows | `docs/ARCHITECTURE.md` |
| Tasks, milestones, open Qs | `docs/ROADMAP.md` |
| Install / IDE matrix | `docs/INSTALL.md` |
| Branch model, commit / PR rules | `CONTRIBUTING.md` |
| **Icons — when asked to create/modify any icon** | **`docs/ICONS.md` + `skills/icons/SKILL.md`** |

When in doubt: check the docs first, then ask the user.

## Icon work — mandatory workflow

If the user asks you to design, create, add, or modify any icon:

1. Load the `icons` skill (`skills/icons/SKILL.md`).
2. Re-read `docs/ICONS.md` §3 (set), §4 (visual spec), §5 (naming).
3. Start from `docs/assets/icons/_template.svg` (copy, don't freehand).
4. Run `python3 scripts/lint-icons.py` before reporting done.
5. Update `docs/assets/icons/README.md` preview table.

Never invent SVG parameters (viewBox / stroke-width / color) — always inherit from the template.

