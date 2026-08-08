"""SCIP index -> normalized graph rows (ARCHITECTURE.md §5 stages 5-6).

Attribution: every reference occurrence is attributed to the tightest
enclosing definition span in its file, else the file's module symbol.
Classification: the per-language classifier decides CALLS vs REFERENCES,
recovers IMPORTS, and extracts INHERITS from heritage clauses; SCIP-native
is_implementation relationships contribute INHERITS as well.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from osprey.classifier import FileFacts, language_for_path
from osprey.scip import reader as R
from osprey.scip.scip_pb2 import Index


@dataclass
class SymbolRow:
    kind: str
    name: str
    path: str | None = None
    line: int | None = None
    end: int | None = None
    external: bool = True
    entry_kind: str | None = None


@dataclass
class GraphData:
    symbols: dict[str, SymbolRow] = field(default_factory=dict)
    files: dict[str, tuple[str, int]] = field(default_factory=dict)
    occurrences: list[tuple] = field(default_factory=list)
    # (src_sym, dst_sym, kind) -> [weight, first_path, first_line]
    edges: dict[tuple, list] = field(default_factory=dict)

    def ensure_symbol(self, sym: str, external: bool = True) -> SymbolRow:
        row = self.symbols.get(sym)
        if row is None:
            row = SymbolRow(kind=R.kind_of(sym),
                            name=R.name_from_symbol(sym), external=external)
            self.symbols[sym] = row
        return row

    def add_edge(self, src: str, dst: str, kind: str, path: str,
                 line: int | None) -> None:
        e = self.edges.setdefault((src, dst, kind), [0, path, line])
        e[0] += 1


def normalize(repo_path: Path, indexes: list[Index]) -> GraphData:
    g = GraphData()
    for index in indexes:
        for ext in index.external_symbols:
            if R.is_local(ext.symbol) or R.is_param(ext.symbol):
                continue
            row = g.ensure_symbol(ext.symbol, external=True)
            if ext.display_name:
                row.name = ext.display_name
        for path, docs in R.merged_documents(index):
            _process_file(g, repo_path, path, docs)
    return g


def _process_file(g: GraphData, repo_path: Path, path: str,
                  docs: list) -> None:
    lang = language_for_path(path)
    fp = repo_path / path
    if lang is None or not fp.exists() or path in g.files:
        return
    facts = FileFacts(fp.read_bytes(), lang)
    g.files[path] = (lang, facts.loc)

    name_pos_to_sym: dict[tuple[int, int], str] = {}
    module_sym: str | None = None
    seen_occ: set[tuple] = set()          # dedupe across merged documents

    for doc in docs:
        for si in doc.symbols:
            if R.is_local(si.symbol) or R.is_param(si.symbol):
                continue
            row = g.ensure_symbol(si.symbol, external=False)
            if si.display_name:
                row.name = si.display_name
            for rel in si.relationships:
                if rel.is_implementation and not R.is_local(rel.symbol):
                    g.ensure_symbol(rel.symbol)
                    g.add_edge(si.symbol, rel.symbol, "INHERITS", path, None)

        for occ in doc.occurrences:
            if (R.is_local(occ.symbol) or R.is_param(occ.symbol)
                    or not occ.range):
                continue
            key = (occ.symbol, occ.range[0], occ.range[1], occ.symbol_roles)
            if key in seen_occ:
                continue
            seen_occ.add(key)
            line, char = occ.range[0], occ.range[1]

            if occ.symbol_roles & R.ROLE_DEFINITION:
                name_pos_to_sym[(line, char)] = occ.symbol
                row = g.ensure_symbol(occ.symbol, external=False)
                if row.path is None:
                    row.path, row.line, row.external = path, line, False
                if R.is_module(occ.symbol) and row.path == path:
                    module_sym = occ.symbol
                g.occurrences.append((occ.symbol, path, line, char,
                                      "definition", None))

    if module_sym is None:
        module_sym = f"osprey . . . `{path}`/"
        row = g.ensure_symbol(module_sym, external=False)
        row.kind, row.name, row.path, row.line = "module", path, path, 0

    for sl, sc, el, ec, name_pos in facts.def_spans:
        sym = name_pos_to_sym.get(name_pos)
        if sym is not None and g.symbols[sym].path == path:
            g.symbols[sym].line, g.symbols[sym].end = sl, el

    # entry points (§8.3): decorated handlers + module main guards
    for name_pos, entry_kind in facts.entry_points.items():
        sym = name_pos_to_sym.get(name_pos)
        if sym is not None:
            g.symbols[sym].entry_kind = entry_kind
    if facts.has_main_guard:
        g.symbols[module_sym].entry_kind = "main"

    seen_ref: set[tuple] = set()
    for doc in docs:
        for occ in doc.occurrences:
            if (R.is_local(occ.symbol) or R.is_param(occ.symbol)
                    or not occ.range or occ.symbol_roles & R.ROLE_DEFINITION):
                continue
            key = (occ.symbol, occ.range[0], occ.range[1])
            if key in seen_ref:
                continue
            seen_ref.add(key)
            _process_reference(g, facts, module_sym, name_pos_to_sym, path,
                               occ)


def _process_reference(g: GraphData, facts: FileFacts, module_sym: str,
                       name_pos_to_sym: dict, path: str, occ) -> None:
    line, char = occ.range[0], occ.range[1]
    target = occ.symbol
    g.ensure_symbol(target)

    if ((occ.symbol_roles & R.ROLE_IMPORT)
            or facts.in_import_statement(line, char)):
        g.occurrences.append((target, path, line, char, "import", module_sym))
        g.add_edge(module_sym, target, "IMPORTS", path, line)
        return

    encl_pos = facts.enclosing_def(line, char)
    encl_sym = (name_pos_to_sym.get(encl_pos) if encl_pos else None) \
        or module_sym

    base_cls_pos = facts.in_base_list(line, char)
    if base_cls_pos is not None and g.symbols[target].kind == "class":
        src = name_pos_to_sym.get(base_cls_pos)
        if src is not None:
            g.add_edge(src, target, "INHERITS", path, line)
        g.occurrences.append((target, path, line, char, "reference",
                              encl_sym))
        return

    role = "write" if occ.symbol_roles & R.ROLE_WRITE else "reference"
    g.occurrences.append((target, path, line, char, role, encl_sym))
    kind = "CALLS" if facts.is_call_position(line, char) else "REFERENCES"
    g.add_edge(encl_sym, target, kind, path, line)

    # handler identifiers handed to route registrations are HTTP entries
    if ((line, char) in facts.entry_ref_positions
            and not g.symbols[target].external
            and g.symbols[target].entry_kind is None):
        g.symbols[target].entry_kind = "http"
