"""Gate rules + engine tests."""

import pytest

from osprey.gate.engine import Violation, evaluate, render_markdown, render_text
from osprey.gate.rules import parse_rules


@pytest.fixture
def rules(tmp_path):
    f = tmp_path / "osprey.rules.yaml"
    f.write_text("""
layers:
  core:         ["src/core/**"]
  experimental: ["src/experimental/**"]
rules:
  - deny: "core -> experimental"
  - no_new_cycles: package
    severity: warn
""")
    return parse_rules(f)


class TestRulesParsing:
    def test_layers_and_rules(self, rules):
        assert rules.layer_of("src/core") == "core"
        assert rules.layer_of("src/core/billing") == "core"
        assert rules.layer_of("src/corex") is None
        assert rules.layer_of("src/experimental/a/b") == "experimental"
        assert len(rules.deny) == 1
        assert rules.cycles is not None and rules.cycles.severity == "warn"

    def test_unknown_layer_rejected(self, tmp_path):
        f = tmp_path / "r.yaml"
        f.write_text('layers: {core: ["a/**"]}\nrules: [{deny: "core -> ghost"}]')
        with pytest.raises(ValueError, match="unknown layer 'ghost'"):
            parse_rules(f)

    def test_unsupported_rule_rejected_loudly(self, tmp_path):
        f = tmp_path / "r.yaml"
        f.write_text('layers: {}\nrules: [{public_api_freeze: ["x/**"]}]')
        with pytest.raises(ValueError, match="not supported yet"):
            parse_rules(f)


class TestDenyRule:
    def test_violating_edge_reported_with_evidence(self, rules):
        edges = [
            {"src_module": "src/core/billing", "dst_module":
             "src/experimental/ml", "kind": "IMPORTS", "weight": 3},
            {"src_module": "src/experimental/ml", "dst_module":
             "src/core", "kind": "CALLS", "weight": 1},   # reverse: allowed
        ]
        sites = lambda s, d: [{"src_name": "charge", "dst_name": "predict",
                               "kind": "IMPORTS", "path":
                               "src/core/billing/charge.ts", "line": 3}]
        vs = evaluate(rules, edges, [], [], sites)
        assert len(vs) == 1
        assert vs[0].severity == "error"
        assert "src/core/billing depends on src/experimental/ml" in vs[0].message
        assert "charge.ts:3" in vs[0].evidence[0]

    def test_references_edges_do_not_trigger(self, rules):
        edges = [{"src_module": "src/core", "dst_module":
                  "src/experimental", "kind": "REFERENCES", "weight": 9}]
        assert evaluate(rules, edges, [], []) == []


class TestCycleRule:
    def test_new_cycle_flagged(self, rules):
        vs = evaluate(rules, [], base_cycles=[],
                      head_cycles=[["a", "b"]])
        assert len(vs) == 1
        assert vs[0].rule == "no_new_cycles"
        assert vs[0].severity == "warn"
        assert "a -> b -> a" in vs[0].message

    def test_preexisting_cycle_tolerated(self, rules):
        cycle = [["a", "b"]]
        assert evaluate(rules, [], cycle, cycle) == []

    def test_fixed_cycle_passes(self, rules):
        assert evaluate(rules, [], [["a", "b"]], []) == []


class TestRendering:
    def test_exit_relevant_summary(self):
        vs = [Violation("deny: a -> b", "error", "m", ["e1"])]
        text = render_text(vs, "#1", "#2")
        assert "✗ [error]" in text and "1 error(s), 0 warning(s)" in text
        md = render_markdown(vs, "#1", "#2")
        assert "❌" in md and "**1 error(s), 0 warning(s)**" in md

    def test_clean_pass(self):
        assert "no violations" in render_text([], "#1", "#2")
        assert "No architecture violations" in render_markdown([], "#1", "#2")
