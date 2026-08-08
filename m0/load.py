"""Osprey M0: parse a .scip index, derive edges, load Postgres, verify.

Implements ARCHITECTURE.md §6 (attribution + call classification) for Python
and TypeScript/TSX. Spike quality: single file, prints phase timings.

Usage:
  load.py --repo PATH --index PATH --project NAME
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import psycopg
import scip_pb2
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Parser

DSN = "host=127.0.0.1 port=5433 dbname=osprey user=postgres password=osprey"
ROLE_DEFINITION, ROLE_IMPORT, ROLE_WRITE = 0x1, 0x2, 0x4

LANGUAGES = {
    "python": Language(tree_sitter_python.language()),
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
}
EXT_LANG = {".py": "python", ".ts": "typescript", ".mts": "typescript",
            ".cts": "typescript", ".js": "typescript", ".mjs": "typescript",
            ".cjs": "typescript", ".jsx": "tsx", ".tsx": "tsx"}

# per-language grammar facts for the three classifier questions
PY = {
    "defs": {"function_definition", "class_definition"},
    "class_def": "class_definition",
    "heritage_field": "superclasses",
    "imports": {"import_statement", "import_from_statement",
                "future_import_statement"},
    "ident": {"identifier"},
}
TS = {
    "defs": {"function_declaration", "generator_function_declaration",
             "method_definition", "class_declaration",
             "abstract_class_declaration"},
    "class_def": "class_declaration",
    "heritage_field": None,           # TS heritage is a child node, not field
    "imports": {"import_statement"},
    "ident": {"identifier", "property_identifier", "type_identifier",
              "private_property_identifier"},   # this.#method() calls
}
GRAMMAR = {"python": PY, "typescript": TS, "tsx": TS}

t0 = time.time()


def phase(msg: str) -> None:
    print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)


def stable(sym: str) -> str:
    parts = sym.split(" ")
    if len(parts) >= 5:
        parts[3] = "_"
    return " ".join(parts)


def kind_of(sym: str, display: str) -> str:
    d = sym.rsplit(" ", 1)[-1]
    if d.endswith("/") or d.endswith("__init__:"):
        return "module"
    if d.endswith("#"):
        return "class"
    if d.endswith(")."):
        return "method" if "#" in d else "function"
    return "variable"


def is_param(sym: str) -> bool:
    return sym.endswith(")") or sym.endswith("]")


def name_from_symbol(sym: str) -> str:
    d = sym.rsplit(" ", 1)[-1]
    if d.endswith("__init__:"):
        mod = d.rsplit("/", 1)[0] if "/" in d else d[: -len("__init__:")]
        return mod.strip("`")
    if d.endswith("/"):
        return d[:-1].strip("`")
    tail = d.rsplit("/", 1)[-1]
    for suffix in ("().", "#", ".", ":", "!"):
        if tail.endswith(suffix):
            tail = tail[: -len(suffix)]
            break
    if "#" in tail:
        tail = tail.rsplit("#", 1)[-1]
    return tail.strip("`") or sym


class FileFacts:
    __slots__ = ("tree", "src", "lang", "g", "def_spans", "class_bases")

    def __init__(self, path: Path, lang: str):
        self.lang = lang
        self.g = GRAMMAR[lang]
        self.src = path.read_bytes()
        self.tree = Parser(LANGUAGES[lang]).parse(self.src)
        self.def_spans: list[tuple] = []
        self.class_bases: list[tuple] = []
        self._walk(self.tree.root_node)
        self.def_spans.sort(key=lambda s: (s[2] - s[0], s[3] - s[1]))

    def _add_def(self, node, name_node) -> None:
        if name_node is not None:
            self.def_spans.append(
                (node.start_point[0], node.start_point[1],
                 node.end_point[0], node.end_point[1],
                 (name_node.start_point[0], name_node.start_point[1])))

    def _walk(self, node) -> None:
        for child in node.children:
            if child.type in self.g["defs"]:
                name = child.child_by_field_name("name")
                self._add_def(child, name)
                if child.type in (self.g["class_def"],
                                  "abstract_class_declaration") and name:
                    self._collect_heritage(child, name)
            elif self.lang != "python" and child.type == "variable_declarator":
                # const f = () => {} / const f = function () {}
                value = child.child_by_field_name("value")
                if value is not None and value.type in (
                        "arrow_function", "function_expression"):
                    self._add_def(child, child.child_by_field_name("name"))
            elif self.lang != "python" and child.type in (
                    "public_field_definition", "field_definition"):
                # class field holding an arrow function (method-ish)
                value = child.child_by_field_name("value")
                if value is not None and value.type in (
                        "arrow_function", "function_expression"):
                    self._add_def(child, child.child_by_field_name("name"))
            self._walk(child)

    def _collect_heritage(self, cls_node, name_node) -> None:
        name_pos = (name_node.start_point[0], name_node.start_point[1])
        if self.g["heritage_field"]:                      # python
            sup = cls_node.child_by_field_name(self.g["heritage_field"])
            if sup is not None:
                self.class_bases.append(
                    (sup.start_point, sup.end_point, name_pos))
        else:                                             # ts: class_heritage
            for child in cls_node.children:
                if child.type == "class_heritage":
                    self.class_bases.append(
                        (child.start_point, child.end_point, name_pos))

    def enclosing_def(self, line: int, char: int):
        for sl, sc, el, ec, name_pos in self.def_spans:
            if (sl, sc) <= (line, char) <= (el, ec):
                return name_pos
        return None

    def is_call_position(self, line: int, char: int) -> bool:
        node = self.tree.root_node.descendant_for_point_range(
            (line, char), (line, char))
        if node is None or node.type not in self.g["ident"]:
            return False
        p = node.parent
        if p is None:
            return False
        if self.lang == "python":
            if p.type == "call" and p.child_by_field_name("function") == node:
                return True
            if (p.type == "attribute"
                    and p.child_by_field_name("attribute") == node
                    and p.parent is not None and p.parent.type == "call"
                    and p.parent.child_by_field_name("function") == p):
                return True
            return False
        # typescript / tsx
        if (p.type == "call_expression"
                and p.child_by_field_name("function") == node):
            return True
        if (p.type == "member_expression"
                and p.child_by_field_name("property") == node
                and p.parent is not None
                and p.parent.type == "call_expression"
                and p.parent.child_by_field_name("function") == p):
            return True
        if (p.type == "new_expression"
                and p.child_by_field_name("constructor") == node):
            return True
        return False

    def in_import_statement(self, line: int, char: int) -> bool:
        node = self.tree.root_node.descendant_for_point_range(
            (line, char), (line, char))
        while node is not None:
            if node.type in self.g["imports"]:
                return True
            if (node.type == "export_statement"
                    and node.child_by_field_name("source") is not None):
                return True                     # export ... from './x'
            node = node.parent
        return False

    def in_base_list(self, line: int, char: int):
        for start, end, cls_name_pos in self.class_bases:
            if start <= (line, char) < end:
                return cls_name_pos
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--index", type=Path, required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()

    phase(f"parsing {args.index.name}")
    index = scip_pb2.Index()
    index.ParseFromString(args.index.read_bytes())
    phase(f"  {len(index.documents)} documents, "
          f"{len(index.external_symbols)} external symbols")

    phase("building symbol/occurrence/edge tables")
    symbols: dict[str, dict] = {}
    files: dict[str, tuple[str, int]] = {}      # path -> (lang, loc)
    occurrences: list[tuple] = []
    edge_counter: dict[tuple, list] = {}

    def ensure_symbol(sym: str, external: bool) -> None:
        symbols.setdefault(sym, {
            "kind": kind_of(sym, ""), "name": name_from_symbol(sym),
            "path": None, "line": None, "end": None, "external": external})

    for ext in index.external_symbols:
        if ext.symbol.startswith("local ") or is_param(ext.symbol):
            continue
        ensure_symbol(ext.symbol, True)
        if ext.display_name:
            symbols[ext.symbol]["name"] = ext.display_name

    n_docs = 0
    inherits_from_scip = 0
    for doc in index.documents:
        path = doc.relative_path
        lang = EXT_LANG.get(Path(path).suffix)
        fp = args.repo / path
        if lang is None or not fp.exists():
            continue
        facts = FileFacts(fp, lang)
        files[path] = (lang, facts.src.count(b"\n") + 1)
        n_docs += 1

        name_pos_to_sym: dict[tuple[int, int], str] = {}
        module_sym = None
        for si in doc.symbols:
            if si.symbol.startswith("local ") or is_param(si.symbol):
                continue
            ensure_symbol(si.symbol, False)
            if si.display_name:
                symbols[si.symbol]["name"] = si.display_name
            for rel in si.relationships:
                if rel.is_implementation and not rel.symbol.startswith("local "):
                    inherits_from_scip += 1
                    ensure_symbol(rel.symbol, True)
                    k = (si.symbol, rel.symbol, "INHERITS")
                    edge_counter.setdefault(k, [0, path, None])[0] += 1

        for occ in doc.occurrences:
            if (occ.symbol.startswith("local ") or is_param(occ.symbol)
                    or not occ.range):
                continue
            line, char = occ.range[0], occ.range[1]
            if occ.symbol_roles & ROLE_DEFINITION:
                name_pos_to_sym[(line, char)] = occ.symbol
                ensure_symbol(occ.symbol, False)
                row = symbols[occ.symbol]
                if row["path"] is None:
                    row["path"], row["line"] = path, line
                    row["external"] = False
                # scip-typescript emits the same file once per tsconfig that
                # includes it; reuse the module symbol claimed by this path
                # instead of synthesizing a duplicate (measured fact, M0)
                if ((occ.symbol.endswith("__init__:")
                     or occ.symbol.endswith("/"))
                        and row["path"] == path):
                    module_sym = occ.symbol

        if module_sym is None:
            module_sym = f"m0 {args.project} . . `{path}`/"
            symbols.setdefault(module_sym, {
                "kind": "module", "name": path, "path": path,
                "line": 0, "end": None, "external": False})

        for sl, sc, el, ec, name_pos in facts.def_spans:
            sym = name_pos_to_sym.get(name_pos)
            if sym and symbols[sym].get("path") == path:
                symbols[sym]["line"] = sl
                symbols[sym]["end"] = el

        for occ in doc.occurrences:
            if (occ.symbol.startswith("local ") or is_param(occ.symbol)
                    or not occ.range):
                continue
            if occ.symbol_roles & ROLE_DEFINITION:
                occurrences.append((occ.symbol, path, occ.range[0],
                                    occ.range[1], "definition", None))
                continue
            line, char = occ.range[0], occ.range[1]
            target = occ.symbol
            ensure_symbol(target, True)

            if ((occ.symbol_roles & ROLE_IMPORT)
                    or facts.in_import_statement(line, char)):
                occurrences.append((target, path, line, char, "import",
                                    module_sym))
                e = edge_counter.setdefault((module_sym, target, "IMPORTS"),
                                            [0, path, line])
                e[0] += 1
                continue

            encl_pos = facts.enclosing_def(line, char)
            encl_sym = (name_pos_to_sym.get(encl_pos) if encl_pos else None) \
                or module_sym

            base_cls_pos = facts.in_base_list(line, char)
            if base_cls_pos is not None and symbols[target]["kind"] == "class":
                src = name_pos_to_sym.get(base_cls_pos)
                if src:
                    e = edge_counter.setdefault((src, target, "INHERITS"),
                                                [0, path, line])
                    e[0] += 1
                occurrences.append((target, path, line, char, "reference",
                                    encl_sym))
                continue

            role = "write" if occ.symbol_roles & ROLE_WRITE else "reference"
            occurrences.append((target, path, line, char, role, encl_sym))
            kind = "CALLS" if facts.is_call_position(line, char) \
                else "REFERENCES"
            e = edge_counter.setdefault((encl_sym, target, kind),
                                        [0, path, line])
            e[0] += 1

    phase(f"  {n_docs} files, {len(symbols)} symbols, "
          f"{len(occurrences)} occurrences, {len(edge_counter)} edges "
          f"(INHERITS via SCIP relationships: {inherits_from_scip})")

    phase("loading Postgres")
    sha = subprocess.run(["git", "-C", str(args.repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM snapshots WHERE repo = %s", (args.project,))
        cur.execute(
            "INSERT INTO snapshots (repo, commit_sha, status,"
            " indexer_versions) VALUES (%s, %s, 'indexing', %s) RETURNING id",
            (args.project, sha, json.dumps({"osprey-m0": "0.1"})))
        snap = cur.fetchone()[0]

        with cur.copy("COPY files (snapshot_id, path, language, loc)"
                      " FROM STDIN") as cp:
            for path, (lang, loc) in files.items():
                cp.write_row((snap, path, lang, loc))
        cur.execute("SELECT path, id FROM files WHERE snapshot_id = %s",
                    (snap,))
        file_ids = dict(cur.fetchall())

        with cur.copy("COPY symbols (snapshot_id, scip_symbol, stable_symbol,"
                      " kind, name, file_id, start_line, end_line,"
                      " is_external) FROM STDIN") as cp:
            for s, row in symbols.items():
                cp.write_row((snap, s, stable(s), row["kind"], row["name"],
                              file_ids.get(row["path"]), row["line"],
                              row["end"], row["external"]))
        cur.execute("SELECT scip_symbol, id FROM symbols"
                    " WHERE snapshot_id = %s", (snap,))
        sym_ids = dict(cur.fetchall())

        with cur.copy("COPY occurrences (snapshot_id, symbol_id, file_id,"
                      " start_line, start_char, role, enclosing_symbol_id)"
                      " FROM STDIN") as cp:
            for s, path, line, char, role, encl in occurrences:
                cp.write_row((snap, sym_ids[s], file_ids[path], line, char,
                              role, sym_ids.get(encl)))

        with cur.copy("COPY edges (snapshot_id, src_id, dst_id, kind, weight,"
                      " first_file_id, first_line) FROM STDIN") as cp:
            for (src, dst, kind), (w, path, line) in edge_counter.items():
                cp.write_row((snap, sym_ids[src], sym_ids[dst], kind, w,
                              file_ids.get(path), line))

        cur.execute("UPDATE snapshots SET status='ready', ready_at=now()"
                    " WHERE id=%s", (snap,))

    phase(f"loaded snapshot {snap}")
    print(f"SNAPSHOT_ID={snap}")


main()
