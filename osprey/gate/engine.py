"""Gate evaluation (ARCHITECTURE.md §8): pure functions over API payloads —
no HTTP in here, so every behavior is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from osprey.gate.rules import Rules

# payload shapes (as returned by the API endpoints)
ModuleEdge = dict          # {src_module, dst_module, kind, weight}
EdgeSite = dict            # {src_name, dst_name, kind, path, line}
SitesFetch = Callable[[str, str], list[EdgeSite]]


@dataclass
class Violation:
    rule: str
    severity: str
    message: str
    evidence: list[str] = field(default_factory=list)


def evaluate(rules: Rules,
             head_module_edges: list[ModuleEdge],
             base_cycles: list[list[str]],
             head_cycles: list[list[str]],
             fetch_sites: SitesFetch | None = None) -> list[Violation]:
    violations: list[Violation] = []
    violations += _check_deny(rules, head_module_edges, fetch_sites)
    if rules.cycles is not None:
        violations += _check_cycles(rules, base_cycles, head_cycles)
    violations.sort(key=lambda v: (v.severity != "error", v.rule))
    return violations


def _check_deny(rules: Rules, edges: list[ModuleEdge],
                fetch_sites: SitesFetch | None) -> list[Violation]:
    out: list[Violation] = []
    for rule in rules.deny:
        # one violation per offending module pair, kinds aggregated —
        # IMPORTS + CALLS across the same boundary is one problem, not two
        by_pair: dict[tuple[str, str], list[ModuleEdge]] = {}
        for e in edges:
            if (e["kind"] in ("CALLS", "IMPORTS", "INHERITS")
                    and rules.layer_of(e["src_module"]) == rule.src_layer
                    and rules.layer_of(e["dst_module"]) == rule.dst_layer):
                by_pair.setdefault(
                    (e["src_module"], e["dst_module"]), []).append(e)
        for (src, dst), pair_edges in sorted(by_pair.items()):
            kinds = "+".join(sorted({e["kind"] for e in pair_edges}))
            sites = sum(e["weight"] for e in pair_edges)
            evidence: list[str] = []
            if fetch_sites is not None:
                for s in fetch_sites(src, dst):
                    loc = f"{s['path']}:{s['line']}" if s.get("path") else "?"
                    evidence.append(
                        f"{s['src_name']} -{s['kind']}-> {s['dst_name']}  ({loc})")
            out.append(Violation(
                rule=f"deny: {rule.src_layer} -> {rule.dst_layer}",
                severity=rule.severity,
                message=f"{src} depends on {dst} ({kinds}, {sites} site(s))",
                evidence=evidence,
            ))
    return out


def _check_cycles(rules: Rules, base: list[list[str]],
                  head: list[list[str]]) -> list[Violation]:
    base_set = {tuple(c) for c in base}
    new = [c for c in head if tuple(c) not in base_set]
    if len(head) <= len(base) and not new:
        return []
    out: list[Violation] = []
    for cycle in new:
        out.append(Violation(
            rule="no_new_cycles",
            severity=rules.cycles.severity,
            message=f"new package cycle: {' -> '.join(cycle)} -> {cycle[0]}",
        ))
    if not out and len(head) > len(base):
        out.append(Violation(
            rule="no_new_cycles", severity=rules.cycles.severity,
            message=f"package cycle count rose {len(base)} -> {len(head)}"))
    return out


def render_text(violations: list[Violation], base_label: str,
                head_label: str) -> str:
    lines = [f"osprey-gate: {head_label} vs {base_label}"]
    if not violations:
        lines.append("✓ no violations")
        return "\n".join(lines)
    for v in violations:
        mark = "✗" if v.severity == "error" else "⚠"
        lines.append(f"{mark} [{v.severity}] {v.rule}")
        lines.append(f"    {v.message}")
        for ev in v.evidence[:5]:
            lines.append(f"      {ev}")
    errors = sum(1 for v in violations if v.severity == "error")
    warns = len(violations) - errors
    lines.append(f"{errors} error(s), {warns} warning(s)")
    return "\n".join(lines)


def render_markdown(violations: list[Violation], base_label: str,
                    head_label: str) -> str:
    lines = [f"### osprey-gate: `{head_label}` vs `{base_label}`", ""]
    if not violations:
        lines.append("✅ **No architecture violations.**")
        return "\n".join(lines)
    errors = sum(1 for v in violations if v.severity == "error")
    warns = len(violations) - errors
    lines.append(f"**{errors} error(s), {warns} warning(s)**")
    lines.append("")
    for v in violations:
        icon = "❌" if v.severity == "error" else "⚠️"
        lines.append(f"- {icon} **{v.rule}**: {v.message}")
        for ev in v.evidence[:5]:
            lines.append(f"  - `{ev}`")
    return "\n".join(lines)
