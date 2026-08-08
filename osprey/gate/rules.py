"""osprey.rules.yaml parsing (ARCHITECTURE.md §8.2).

Supported in this version:

  layers:
    core:         ["src/core/**"]
    experimental: ["src/experimental/**"]
  rules:
    - deny: "core -> experimental"
      severity: error            # default
    - no_new_cycles: package
      severity: error

public_api_freeze is documented but deferred: it needs is_exported facts and
a path-aware symbol diff, both on the backlog. The parser rejects it loudly
rather than silently ignoring it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SEVERITIES = ("error", "warn")


@dataclass
class DenyRule:
    src_layer: str
    dst_layer: str
    severity: str = "error"


@dataclass
class CycleRule:
    severity: str = "error"


@dataclass
class Rules:
    layers: dict[str, list[str]] = field(default_factory=dict)
    deny: list[DenyRule] = field(default_factory=list)
    cycles: CycleRule | None = None

    def layer_of(self, module_dir: str) -> str | None:
        """First layer whose globs match this module directory."""
        for name, globs in self.layers.items():
            for glob in globs:
                if _match(module_dir, glob):
                    return name
        return None


def _match(module_dir: str, glob: str) -> bool:
    if glob.endswith("/**"):
        prefix = glob[:-3]
        return module_dir == prefix or module_dir.startswith(prefix + "/")
    return module_dir == glob.rstrip("/")


def parse_rules(path: Path) -> Rules:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at top level")

    rules = Rules()
    for name, globs in (data.get("layers") or {}).items():
        if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
            raise ValueError(f"layer {name!r}: globs must be a list of strings")
        rules.layers[str(name)] = globs

    for i, entry in enumerate(data.get("rules") or []):
        if not isinstance(entry, dict):
            raise ValueError(f"rules[{i}]: expected a mapping")
        severity = str(entry.get("severity", "error"))
        if severity not in SEVERITIES:
            raise ValueError(f"rules[{i}]: severity must be one of {SEVERITIES}")
        if "deny" in entry:
            m = re.fullmatch(r"\s*(\S+)\s*->\s*(\S+)\s*", str(entry["deny"]))
            if m is None:
                raise ValueError(
                    f"rules[{i}]: deny must look like 'layerA -> layerB'")
            src, dst = m.group(1), m.group(2)
            for layer in (src, dst):
                if layer not in rules.layers:
                    raise ValueError(
                        f"rules[{i}]: unknown layer {layer!r} "
                        f"(defined: {sorted(rules.layers)})")
            rules.deny.append(DenyRule(src, dst, severity))
        elif "no_new_cycles" in entry:
            rules.cycles = CycleRule(severity)
        elif "public_api_freeze" in entry:
            raise ValueError(
                f"rules[{i}]: public_api_freeze is not supported yet "
                "(needs export facts; on the roadmap)")
        else:
            raise ValueError(f"rules[{i}]: unknown rule type {sorted(entry)}")
    return rules
