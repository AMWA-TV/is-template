#!/usr/bin/env python3
"""Render repository API, schema, and example assets for Zensical.

The source repositories keep these assets outside docs/. Zensical only builds
from docs/, so this script creates a temporary documentation tree and derives
all index pages by discovery rather than maintaining lists in configuration.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any


ROOT = Path.cwd()
DOCS = ROOT / "docs"
API_SOURCE = ROOT / "APIs"
EXAMPLE_SOURCE = ROOT / "examples"
API_DOCS = DOCS / "APIs"
SCHEMA_DOCS = API_DOCS / "schemas"
EXAMPLE_DOCS = DOCS / "examples"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def json_scalar(value: Any) -> str:
    if isinstance(value, str):
        rendered = json.dumps(value, ensure_ascii=False)
        return f'<span class="json-string">{html.escape(rendered)}</span>'
    if value is True or value is False:
        return f'<span class="json-boolean">{str(value).lower()}</span>'
    if value is None:
        return '<span class="json-null">null</span>'
    return f'<span class="json-number">{html.escape(json.dumps(value))}</span>'


def json_label(label: str | None) -> str:
    if label is None:
        return ""
    return f'<span class="json-key">{html.escape(json.dumps(label))}</span>: '


def json_tree(
    value: Any,
    label: str | None = None,
    root: bool = False,
    trailing_comma: bool = False,
) -> str:
    """Render JSON as a pretty, nested, collapsible HTML tree."""
    comma = '<span class="json-comma">,</span>' if trailing_comma else ''
    if not isinstance(value, (dict, list)):
        return f'<div class="json-line">{json_label(label)}{json_scalar(value)}{comma}</div>'

    is_array = isinstance(value, list)
    opening = "[" if is_array else "{"
    closing = "]" if is_array else "}"
    summary = (
        f'{json_label(label)}{opening} '
        f'<span class="json-fold">…</span> '
        f'<span class="json-collapsed-close">{closing}{comma}</span>'
    )
    lines = [
        f'<details class="json-node"{" open" if root else ""}>',
        f"  <summary>{summary}</summary>",
        '  <div class="json-children">',
    ]
    items = list(enumerate(value)) if is_array else list(value.items())
    for index, (key, child) in enumerate(items):
        child_label = None if is_array else str(key)
        rendered = json_tree(
            child,
            child_label,
            False,
            trailing_comma=index < len(items) - 1,
        )
        lines.append("    " + rendered.replace("\n", "\n    "))
    closing_comma = '<span class="json-comma">,</span>' if trailing_comma else ''
    lines.extend([
        "  </div>",
        f'  <div class="json-close">{closing}{closing_comma}</div>',
        "</details>",
    ])
    return "\n".join(lines)


def render_json(value: Any) -> str:
    return (
        '<div class="json-viewer">\n'
        '  <div class="json-controls" role="group" aria-label="JSON folding controls">\n'
        '    <button type="button" class="json-control" data-json-action="expand">Expand all</button>\n'
        '    <button type="button" class="json-control" data-json-action="collapse">Collapse all</button>\n'
        '  </div>\n'
        + json_tree(value, root=True)
        + "\n</div>\n"
    )


def render_json_js() -> str:
    return """document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) return;
  const button = event.target.closest("[data-json-action]");
  if (!(button instanceof HTMLButtonElement)) return;
  const viewer = button.closest(".json-viewer");
  if (!viewer) return;
  const expanded = button.dataset.jsonAction === "expand";
  viewer.querySelectorAll("details.json-node").forEach((node) => {
    node.open = expanded;
  });
});
"""


def render_json_css() -> str:
    return """.json-viewer {
  margin: 1rem 0;
  padding: 0.8rem 1rem;
  overflow-x: auto;
  border-radius: 0.2rem;
  background: var(--md-code-bg-color);
  color: var(--md-code-fg-color);
  font-family: var(--md-code-font-family, monospace);
  font-size: 0.85rem;
  line-height: 1.5;
}

.json-controls {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
}

.json-control {
  background: var(--md-default-bg-color);
  border: 1px solid var(--md-default-fg-color--lighter);
  border-radius: 0.2rem;
  color: var(--md-default-fg-color);
  cursor: pointer;
  font: inherit;
  padding: 0.2rem 0.5rem;
}

.json-control:hover {
  border-color: var(--md-accent-fg-color);
  color: var(--md-accent-fg-color);
}

.json-node > summary {
  cursor: pointer;
  padding-left: 0 !important;
  white-space: nowrap;
}

.md-typeset .json-node > summary::before {
  display: none !important;
}

.json-node[open] > summary > .json-fold,
.json-node[open] > summary > .json-collapsed-close {
  display: none !important;
}

.json-node:not([open]) > summary > .json-collapsed-close {
  display: inline !important;
}

.headerlink {
  display: none !important;
}

.json-node > summary:hover {
  color: var(--md-accent-fg-color);
}

.json-children {
  margin-left: 1.5rem;
  padding-left: 1rem;
  border-left: 1px solid var(--md-default-fg-color--lightest);
}

.json-line,
.json-close {
  white-space: pre-wrap;
}

.json-key { color: var(--md-code-hl-function-color); }
.json-string { color: var(--md-code-hl-string-color); }
.json-number { color: var(--md-code-hl-number-color); }
.json-boolean, .json-null { color: var(--md-code-hl-constant-color); }
.json-fold, .json-comma { opacity: 0.65; }
"""


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def resolve_reference(value: Any, schema_dir: Path, stack: tuple[str, ...] = ()) -> Any:
    """Resolve local JSON Schema file references, preserving external refs."""
    if isinstance(value, list):
        return [resolve_reference(item, schema_dir, stack) for item in value]
    if not isinstance(value, dict):
        return value

    reference = value.get("$ref")
    if isinstance(reference, str) and not reference.startswith("#"):
        target_name, _, fragment = reference.partition("#")
        target = (schema_dir / target_name).resolve()
        if target.exists() and target.is_file():
            key = str(target)
            if key not in stack:
                target_value = load_json(target)
                if fragment:
                    for part in fragment.lstrip("/").split("/"):
                        target_value = target_value[part.replace("~1", "/").replace("~0", "~")]
                return resolve_reference(target_value, schema_dir, (*stack, key))

    return {key: resolve_reference(item, schema_dir, stack) for key, item in value.items()}


def render_schemas() -> None:
    source = API_SOURCE / "schemas"
    if not source.is_dir():
        return

    schema_paths = sorted(source.rglob("*.json"))
    resolved_dir = SCHEMA_DOCS / "resolved"
    raw_entries: list[tuple[str, str]] = []

    for schema_path in schema_paths:
        relative = schema_path.relative_to(source)
        # The current NMOS layout is flat; preserve subdirectories if a future
        # template adds them.
        raw_json = SCHEMA_DOCS / relative
        raw_md = raw_json.with_suffix(".md")
        raw_value = load_json(schema_path)
        shutil.copy2(schema_path, raw_json)
        resolved_value = resolve_reference(raw_value, source)

        raw_entries.append((relative.with_suffix(".md").as_posix(), relative.stem))
        resolved_json = resolved_dir / relative
        resolved_json.parent.mkdir(parents=True, exist_ok=True)
        resolved_json.write_text(json.dumps(resolved_value, indent=2) + "\n", encoding="utf-8")
        raw_link = Path(os.path.relpath(raw_json, raw_md.parent)).as_posix()
        resolved_link = Path(os.path.relpath(resolved_json, raw_md.parent)).as_posix()
        raw_tab = textwrap.indent(render_json(raw_value).rstrip(), "    ")
        resolved_tab = textwrap.indent(render_json(resolved_value).rstrip(), "    ")
        write(
            raw_md,
            f"# {relative.stem}\n\n"
            "=== \"With refs\"\n\n"
            f"    [Raw file]({raw_link})\n\n"
            f"{raw_tab}\n\n"
            "=== \"Resolved\"\n\n"
            f"    [Resolved JSON file]({resolved_link})\n\n"
            f"{resolved_tab}\n",
        )

    lines = ["# JSON Schemas", ""]
    for relative, title in raw_entries:
        lines.append(f"- [{title}]({relative})")
    write(SCHEMA_DOCS / "index.md", "\n".join(lines) + "\n")



def render_examples() -> None:
    if not EXAMPLE_SOURCE.is_dir():
        return

    entries: list[tuple[str, str]] = []
    for example_path in sorted(EXAMPLE_SOURCE.rglob("*.json")):
        relative = example_path.relative_to(EXAMPLE_SOURCE)
        output_json = EXAMPLE_DOCS / relative
        output_md = output_json.with_suffix(".md")
        shutil.copy2(example_path, output_json)
        entries.append((relative.with_suffix(".md").as_posix(), relative.name))
        write(
            output_md,
            f"# Example: {relative.name}\n\n"
            f"[Raw file]({relative.name})\n\n"
            + render_json(load_json(example_path)),
        )

    lines = ["# Examples", ""]
    for relative, title in entries:
        lines.append(f"- [{title}]({relative})")
    write(EXAMPLE_DOCS / "index.md", "\n".join(lines) + "\n")


def render_apis() -> None:
    if not API_SOURCE.is_dir():
        return

    raml_paths = sorted(API_SOURCE.rglob("*.raml"))
    entries: list[tuple[str, str]] = []
    renderer = os.environ.get("RAML2HTML_BIN", "")
    for raml_path in raml_paths:
        relative = raml_path.relative_to(API_SOURCE)
        output = API_DOCS / relative.with_suffix(".html")
        output.parent.mkdir(parents=True, exist_ok=True)
        if not renderer:
            raise RuntimeError(
                "RAML files were found but RAML2HTML_BIN is not set; "
                "install raml2html before rendering documentation"
            )
        subprocess.run(
            [renderer, "--input", str(raml_path), "--output", str(output), "--pretty"],
            cwd=ROOT,
            check=True,
        )
        entries.append((relative.with_suffix(".html").as_posix(), relative.stem))

    lines = ["# APIs", ""]
    for relative, title in entries:
        lines.append(f"- [{title}]({relative})")
    write(API_DOCS / "index.md", "\n".join(lines) + "\n")


def main() -> None:
    # README.md files in these source trees are legacy navigation stubs. The
    # generated index pages below are the actual Zensical section indexes.
    for tree in (API_DOCS, EXAMPLE_DOCS):
        if tree.is_dir():
            for readme in tree.rglob("README.md"):
                readme.unlink()
    render_apis()
    render_schemas()
    render_examples()
    write(DOCS / "stylesheets" / "extra.css", render_json_css())
    write(DOCS / "javascripts" / "json-viewer.js", render_json_js())


if __name__ == "__main__":
    main()
