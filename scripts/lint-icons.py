#!/usr/bin/env python3
"""Lint icon SVGs against itsme spec (docs/ICONS.md §4).

Usage:
    python scripts/lint-icons.py
    python scripts/lint-icons.py path/to/file.svg [more.svg ...]

Exit 0 = all pass · Exit 1 = at least one violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ICONS_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets" / "icons"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

REQUIRED_ROOT_ATTRS = {
    "viewBox": "0 0 24 24",
    "fill": "none",
    "stroke": "currentColor",
    "stroke-width": "2",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
}

BANNED_ELEMENTS = {
    "linearGradient",
    "radialGradient",
    "filter",
    "mask",
    "pattern",
    "image",
    "text",
    "foreignObject",
    "style",
}

NAME_RE = re.compile(r"^(itsme|(engine|verb|hook)-[a-z0-9-]+)\.svg$")
HARD_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgb\(|rgba\(|hsl\(")
ALLOWED_FILLS = {"none", "currentColor"}
NON_ICON_NAMES = {"_template.svg", "README.md"}


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def lint_file(path: Path) -> list[str]:
    errors: list[str] = []

    # 1. naming
    if path.name not in NON_ICON_NAMES and not NAME_RE.match(path.name):
        errors.append(
            f"name: '{path.name}' violates <scope>-<name>.svg "
            f"(scope ∈ engine|verb|hook, or 'itsme.svg' for brand)"
        )

    # 2. parse
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        return [f"parse: {e}"]
    root = tree.getroot()
    if _strip_ns(root.tag) != "svg":
        return [f"root: expected <svg>, got <{_strip_ns(root.tag)}>"]

    # 3. required root attrs
    for attr, expected in REQUIRED_ROOT_ATTRS.items():
        actual = root.get(attr)
        if actual != expected:
            errors.append(f"root @{attr}: expected '{expected}', got '{actual}'")

    # 4. forbidden root attrs
    for forbid in ("transform", "width", "height"):
        if root.get(forbid) is not None:
            errors.append(f"root @{forbid}: must be absent (got '{root.get(forbid)}')")

    # 5. walk children
    raw_text = path.read_text(encoding="utf-8")
    if HARD_COLOR_RE.search(raw_text):
        errors.append("hard-coded color (#hex / rgb() / hsl()) detected — must use currentColor")

    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag in BANNED_ELEMENTS:
            errors.append(f"banned element <{tag}>")

        # fill / stroke on children
        fill = elem.get("fill")
        if fill is not None and fill not in ALLOWED_FILLS:
            errors.append(f"<{tag}> @fill='{fill}': only 'none' or 'currentColor' allowed")

        stroke = elem.get("stroke")
        if stroke is not None and stroke not in ALLOWED_FILLS:
            errors.append(f"<{tag}> @stroke='{stroke}': only 'none' or 'currentColor' allowed")

    return errors


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        targets = [Path(p) for p in argv[1:]]
    else:
        if not ICONS_DIR.exists():
            print(f"icons dir not found: {ICONS_DIR}", file=sys.stderr)
            return 1
        targets = sorted(ICONS_DIR.glob("*.svg"))

    if not targets:
        print("no SVG files to lint")
        return 0

    failed = 0
    for path in targets:
        errors = lint_file(path)
        if errors:
            failed += 1
            print(f"\x1b[31m✗\x1b[0m {path}")
            for e in errors:
                print(f"    - {e}")
        else:
            print(f"\x1b[32m✓\x1b[0m {path}")

    print(f"\n{len(targets) - failed}/{len(targets)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
