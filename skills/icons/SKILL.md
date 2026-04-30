---
name: icons
description: How to design and create itsme project icons. Load this when the user asks to design / create / add / modify any icon.
triggers:
  - design icon
  - create icon
  - add icon
  - modify icon
  - 画图标
  - 做图标
  - 设计图标
  - 改图标
status: active
---

# itsme — icon authoring skill

> When loaded, follow this workflow **verbatim**. Do not freehand SVG.

## STOP-READ-COPY-EDIT-VERIFY

```text
1. STOP    — read this whole file before touching SVG.
2. READ    — open docs/ICONS.md §3 §4 §5.
3. COPY    — duplicate docs/assets/icons/_template.svg → <scope>-<name>.svg.
4. EDIT    — only the inner shapes. Never touch viewBox / stroke / fill.
5. VERIFY  — run `python3 scripts/lint-icons.py`. Must pass.
6. PREVIEW — append/update row in docs/assets/icons/README.md table.
```

---

## Hard rules (mirrored from `docs/ICONS.md` §4)

| Attribute | Required value |
|---|---|
| `viewBox` | `0 0 24 24` |
| Root `<svg>` `width` / `height` | omit (let consumer size) |
| `fill` (root) | `none` |
| `stroke` (root) | `currentColor` |
| `stroke-width` | `2` |
| `stroke-linecap` | `round` |
| `stroke-linejoin` | `round` |
| Allowed `fill="currentColor"` | only on intentional accent dots / stars |
| Hard-coded colors (`#hex`, `rgb()`, named) | ❌ banned |
| `<linearGradient>` / `<radialGradient>` / `<filter>` | ❌ banned |
| Root `<svg transform>` | ❌ banned |
| Embedded fonts / `<text>` | ❌ banned (paths only) |

## Naming

```text
docs/assets/icons/<scope>-<name>.svg
```

Allowed scopes: `brand` (no prefix — just `itsme.svg`), `engine`, `verb`, `hook`.

Examples — **good**: `verb-remember.svg`, `engine-aleph.svg`, `hook-before-exit.svg`.
**Bad**: `Remember.svg`, `verb_remember.svg`, `aleph-icon.svg`.

## Visual checklist

Before reporting done, eyeball the icon and confirm:

- [ ] Visual mass centered on 24×24 grid
- [ ] ≥ 1.5px breathing room from canvas edges
- [ ] Stroke width matches sibling icons (no accidental 1.5 / 2.5)
- [ ] Renders correctly in both light and dark themes (use `currentColor`)
- [ ] No raster fallback / no PNG embedding

## When the user is vague

If the user says "make me an icon" without specifying scope/name, ask:

1. What does it represent? (verb / engine / hook / something else)
2. Suggested filename?
3. Concept hint (one sentence)?

Then propose 1-2 concept sketches **in chat first** before writing files.

## Output discipline

After creating one or more icons, end your reply with:

```text
✅ Created: <list>
✅ Lint: PASS / FAIL (if FAIL, show errors)
✅ Preview: docs/assets/icons/README.md updated
```
