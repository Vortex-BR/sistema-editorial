#!/usr/bin/env python3
"""Resolve the single Alembic head without importing application dependencies."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def assignment_value(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name and node.value is not None:
                return ast.literal_eval(node.value)
    raise ValueError(f"missing {name}")


def resolve(directory: Path) -> str:
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("__"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = assignment_value(tree, "revision")
        down_revision = assignment_value(tree, "down_revision")
        if not isinstance(revision, str) or not revision:
            raise ValueError(f"invalid revision in {path}")
        if revision in revisions:
            raise ValueError(f"duplicate revision {revision}")
        revisions.add(revision)
        if isinstance(down_revision, str):
            parents.add(down_revision)
        elif isinstance(down_revision, (tuple, list)):
            parents.update(str(item) for item in down_revision if item)
        elif down_revision is not None:
            raise ValueError(f"invalid down_revision in {path}")
    unknown = parents - revisions
    if unknown:
        raise ValueError(f"unknown down revisions: {sorted(unknown)}")
    heads = sorted(revisions - parents)
    if len(heads) != 1:
        raise ValueError(f"expected exactly one Alembic head, found {heads}")
    return heads[0]


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        print(resolve(root / "backend" / "alembic" / "versions"))
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"Alembic head resolution failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
