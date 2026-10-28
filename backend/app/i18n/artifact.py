"""Generate the frontend contract and English backend-message catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from string import Formatter
from typing import Any

from app.i18n.messages import REGISTRY, MessageRef, render, sample_english_params

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_PATH = REPOSITORY_ROOT / "frontend/src/i18n/backendCodes.generated.json"
ENGLISH_CATALOG_PATH = REPOSITORY_ROOT / "frontend/messages/en/backend.json"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def artifact_value() -> dict[str, object]:
    """The flat code contract consumed by the frontend reader and its tests."""
    out: dict[str, object] = {}
    for spec in REGISTRY:
        ref = MessageRef(spec.code, spec.sample)
        wire = ref.to_wire()
        out[spec.code] = {
            "template": spec.template,
            "params": {
                name: param.to_artifact()
                for name, param in sorted(spec.params.items())
            },
            "sample": wire["params"],
            "sample_english": render(ref),
            "sample_english_params": sample_english_params(spec),
        }
    return out


def _icu_literal(text: str) -> str:
    # ICU quotes syntax characters with apostrophes, and represents a literal
    # apostrophe by doubling it. Do apostrophes first so the quotes added for
    # braces remain syntax rather than literal pairs.
    return text.replace("'", "''").replace("{", "'{'").replace("}", "'}'")


def icu_template(template: str) -> str:
    """Convert a Python ``str.format`` template to an ICU-safe template."""
    parts: list[str] = []
    for literal, name, _format_spec, _conversion in Formatter().parse(template):
        parts.append(_icu_literal(literal))
        if name is not None:
            parts.append(f"{{{name}}}")
    return "".join(parts)


def english_catalog_value() -> dict[str, object]:
    """Nested templates matching the dotted paths below the `backend` namespace."""
    root: dict[str, Any] = {}
    for spec in REGISTRY:
        cursor = root
        segments = spec.code.split(".")
        for segment in segments[:-1]:
            cursor = cursor.setdefault(segment, {})
        cursor[segments[-1]] = icu_template(spec.template)
    return root


def generated_files() -> dict[Path, str]:
    return {
        ARTIFACT_PATH: _json(artifact_value()),
        ENGLISH_CATALOG_PATH: _json(english_catalog_value()),
    }


def write() -> None:
    for path, content in generated_files().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def check() -> bool:
    return all(
        path.is_file() and path.read_text() == content
        for path, content in generated_files().items()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write:
        write()
        return 0
    if check():
        return 0
    print("backend message artifacts are stale; run python -m app.i18n.artifact --write")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
