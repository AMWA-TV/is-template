#!/usr/bin/env python3
"""Render repository API, schema, and example assets for Zensical.

The source repositories keep these assets outside docs/. Zensical only builds
from docs/, so this script creates a temporary documentation tree and derives
all index pages by discovery rather than maintaining lists in configuration.
"""

from __future__ import annotations

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


def json_fence(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2) + "\n```\n"


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
        raw_tab = textwrap.indent(json_fence(raw_value).rstrip(), "    ")
        resolved_tab = textwrap.indent(json_fence(resolved_value).rstrip(), "    ")
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
            + json_fence(load_json(example_path)),
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


if __name__ == "__main__":
    main()
