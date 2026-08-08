"""SCIP index reading and symbol-string interpretation.

Symbol grammar facts learned in M0 (see m0/REPORT.md):
  - display_name is unreliable; names are parsed from the symbol grammar
  - parameter `(x)` / type-param `[T]` symbols are body-local noise
  - Python module symbols end `__init__:`, TS file namespaces end `/`
  - scip-typescript emits one Document per (file, tsconfig) pair; documents
    must be merged by relative_path before loading
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from osprey.scip import scip_pb2

ROLE_DEFINITION, ROLE_IMPORT, ROLE_WRITE = 0x1, 0x2, 0x4


def read_index(path: Path) -> scip_pb2.Index:
    index = scip_pb2.Index()
    index.ParseFromString(path.read_bytes())
    return index


def merged_documents(index: scip_pb2.Index):
    """Yield (relative_path, [documents]) with multi-tsconfig duplicates
    grouped, preserving first-seen order."""
    groups: dict[str, list] = defaultdict(list)
    order: list[str] = []
    for doc in index.documents:
        if doc.relative_path not in groups:
            order.append(doc.relative_path)
        groups[doc.relative_path].append(doc)
    for path in order:
        yield path, groups[path]


def is_local(sym: str) -> bool:
    return sym.startswith("local ")


def is_param(sym: str) -> bool:
    return sym.endswith(")") or sym.endswith("]")


def is_module(sym: str) -> bool:
    return sym.endswith("__init__:") or sym.endswith("/")


def stable_symbol(sym: str) -> str:
    """Replace the version segment so symbols compare across snapshots
    despite per-release package-version churn."""
    parts = sym.split(" ")
    if len(parts) >= 5:
        parts[3] = "_"
    return " ".join(parts)


def kind_of(sym: str) -> str:
    d = sym.rsplit(" ", 1)[-1]
    if d.endswith("/") or d.endswith("__init__:"):
        return "module"
    if d.endswith("#"):
        return "class"
    if d.endswith(")."):
        return "method" if "#" in d else "function"
    return "variable"


def name_from_symbol(sym: str) -> str:
    d = sym.rsplit(" ", 1)[-1]
    if d.endswith("__init__:"):
        mod = d.rsplit("/", 1)[0] if "/" in d else d[: -len("__init__:")]
        return mod.replace("`", "")
    if d.endswith("/"):
        # namespace descriptors quote path segments (`src/utils/`url.ts`/) —
        # backticks are grammar, not name
        return d[:-1].replace("`", "")
    tail = d.rsplit("/", 1)[-1]
    for suffix in ("().", "#", ".", ":", "!"):
        if tail.endswith(suffix):
            tail = tail[: -len(suffix)]
            break
    if "#" in tail:
        tail = tail.rsplit("#", 1)[-1]
    return tail.strip("`") or sym
