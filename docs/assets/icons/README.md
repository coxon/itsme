# itsme — Icons

> Spec: [docs/ICONS.md](../../ICONS.md) · Authoring: [skills/icons/SKILL.md](../../../skills/icons/SKILL.md)
> All icons are 24×24 SVG, single-color via `currentColor`.

## I1 — Core 7 (v0.0.1)

<table>
  <thead>
    <tr><th>Preview</th><th>File</th><th>Concept</th></tr>
  </thead>
  <tbody>
    <tr>
      <td><img src="itsme.svg" width="32" height="32" alt="itsme"></td>
      <td><code>itsme.svg</code></td>
      <td>Brand mark — memory ribbon (m-shape) with self-anchor dot</td>
    </tr>
    <tr>
      <td><img src="verb-remember.svg" width="32" height="32" alt="remember"></td>
      <td><code>verb-remember.svg</code></td>
      <td>Verb · <code>remember()</code> — bookmark + plus</td>
    </tr>
    <tr>
      <td><img src="verb-ask.svg" width="32" height="32" alt="ask"></td>
      <td><code>verb-ask.svg</code></td>
      <td>Verb · <code>ask()</code> — speech bubble + question</td>
    </tr>
    <tr>
      <td><img src="verb-status.svg" width="32" height="32" alt="status"></td>
      <td><code>verb-status.svg</code></td>
      <td>Verb · <code>status()</code> — heartbeat / pulse</td>
    </tr>
    <tr>
      <td><img src="hook-before-exit.svg" width="32" height="32" alt="before-exit"></td>
      <td><code>hook-before-exit.svg</code></td>
      <td>Hook · <code>before-exit</code> — door + arrow out</td>
    </tr>
    <tr>
      <td><img src="hook-before-clear.svg" width="32" height="32" alt="before-clear"></td>
      <td><code>hook-before-clear.svg</code></td>
      <td>Hook · <code>before-clear</code> — sweeping motion</td>
    </tr>
    <tr>
      <td><img src="hook-before-compact.svg" width="32" height="32" alt="before-compact"></td>
      <td><code>hook-before-compact.svg</code></td>
      <td>Hook · <code>before-compact</code> — inward arrows</td>
    </tr>
  </tbody>
</table>

## I2 — Engines (v0.0.2 · planned)

- `engine-mempalace.svg` — hippocampus / palace pillars
- `engine-aleph.svg` — neocortex / open book + star

## Usage

```markdown
![itsme](docs/assets/icons/itsme.svg)
```

```html
<img src="docs/assets/icons/verb-remember.svg" width="20" height="20" alt="remember">
```

Tint via CSS by setting `color`:

```css
.icon { color: var(--text-accent); }
```

## Adding a new icon

1. Read [`docs/ICONS.md`](../../ICONS.md) §3-§5
2. `cp _template.svg <scope>-<name>.svg`
3. Edit only inner shapes
4. `python3 scripts/lint-icons.py`
5. Add a row to the table above
