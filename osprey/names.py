"""Repo-name validation, shared by the API and the CLI.

A repo name becomes a filesystem path segment on the worker
(`repo_root / name`), so it must be a single innocuous segment: no path
separators, no '..', no leading dot or dash. Kept in one place so every
entry point enforces the same rule.
"""

from __future__ import annotations

import re

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def safe_repo_name(name: str) -> str:
    n = (name or "").strip()
    if _NAME_RE.fullmatch(n) is None or n in (".", ".."):
        raise ValueError(
            "repo name must be a single path segment of letters, digits, "
            "'.', '_' or '-' (no slashes, no leading dot or dash)")
    return n
