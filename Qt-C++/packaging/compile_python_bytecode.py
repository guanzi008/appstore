#!/usr/bin/env python3
from __future__ import annotations

import py_compile
import sys
from pathlib import Path


def compile_tree(project_root: Path, output_root: Path, package_names: list[str]) -> None:
    for package_name in package_names:
        source_root = project_root / package_name
        if not source_root.exists():
            continue
        for source_path in source_root.rglob("*.py"):
            if any(part in {"__pycache__", "tests", "examples", "cache", "output"} for part in source_path.parts):
                continue
            relative_path = source_path.relative_to(project_root).with_suffix(".pyc")
            target_path = output_root / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            py_compile.compile(
                str(source_path),
                cfile=str(target_path),
                doraise=True,
                optimize=0,
                invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
            )


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: compile_python_bytecode.py PROJECT_ROOT OUTPUT_ROOT PACKAGE...", file=sys.stderr)
        return 2
    project_root = Path(argv[1]).resolve()
    output_root = Path(argv[2]).resolve()
    compile_tree(project_root, output_root, argv[3:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
