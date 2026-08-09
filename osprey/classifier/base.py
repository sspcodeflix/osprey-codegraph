from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser


@dataclass(frozen=True)
class Grammar:
    defs: frozenset[str]           # named definition node types
    class_defs: frozenset[str]     # subset that can declare heritage
    imports: frozenset[str]        # import statement node types
    idents: frozenset[str]         # identifier leaf node types
    fn_value_defs: bool            # const f = () => {} / class fields


PYTHON = Grammar(
    defs=frozenset({"function_definition", "class_definition"}),
    class_defs=frozenset({"class_definition"}),
    imports=frozenset({"import_statement", "import_from_statement",
                       "future_import_statement"}),
    idents=frozenset({"identifier"}),
    fn_value_defs=False,
)

TYPESCRIPT = Grammar(
    defs=frozenset({"function_declaration", "generator_function_declaration",
                    "method_definition", "class_declaration",
                    "abstract_class_declaration"}),
    class_defs=frozenset({"class_declaration", "abstract_class_declaration"}),
    imports=frozenset({"import_statement"}),
    # private_property_identifier: this.#method() calls (M0 finding - absent
    # from the set, private-method calls silently degrade to REFERENCES)
    idents=frozenset({"identifier", "property_identifier", "type_identifier",
                      "private_property_identifier"}),
    fn_value_defs=True,
)

_LANGUAGES = {
    "python": Language(tree_sitter_python.language()),
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
}
_GRAMMARS = {"python": PYTHON, "typescript": TYPESCRIPT, "tsx": TYPESCRIPT}
_EXT_LANG = {".py": "python", ".ts": "typescript", ".mts": "typescript",
             ".cts": "typescript", ".js": "typescript", ".mjs": "typescript",
             ".cjs": "typescript", ".jsx": "tsx", ".tsx": "tsx"}

Point = tuple[int, int]

# decorator attribute names that mark an HTTP/CLI entry point (§8.3);
# matched against the last dotted component of the decorator expression
_HTTP_DECORATORS = frozenset({"route", "get", "post", "put", "delete",
                              "patch", "head", "options", "websocket"})
_CLI_DECORATORS = frozenset({"command"})
# TS/JS route registration: app.get('/path', handler) and friends
# (Express/Hono/Fastify idiom). 'use' covers mounted middleware handlers.
_ROUTE_METHODS = frozenset({"get", "post", "put", "delete", "patch", "all",
                            "options", "head", "use"})


def language_for_path(path: str) -> str | None:
    return _EXT_LANG.get(Path(path).suffix)


class FileFacts:
    """One parse of one file; answers the three classifier questions."""

    def __init__(self, source: bytes, lang: str):
        self.lang = lang
        self.g = _GRAMMARS[lang]
        self.src = source
        self.tree = Parser(_LANGUAGES[lang]).parse(source)
        self.loc = source.count(b"\n") + 1
        # (start_line, start_char, end_line, end_char, name_pos)
        self.def_spans: list[tuple[int, int, int, int, Point]] = []
        # (heritage_start, heritage_end, class_name_pos)
        self.class_bases: list[tuple[Point, Point, Point]] = []
        # name_pos -> entry kind ('http' | 'cli') for decorated definitions
        self.entry_points: dict[Point, str] = {}
        # reference positions handed to route registrations (TS/JS):
        # app.get('/path', handler) -> handler's position marks an entry
        self.entry_ref_positions: set[Point] = set()
        # module-level entry: `if __name__ == "__main__"` guard
        self.has_main_guard = (lang == "python"
                               and b"__main__" in source
                               and b"__name__" in source)
        self._walk(self.tree.root_node)
        self.def_spans.sort(key=lambda s: (s[2] - s[0], s[3] - s[1]))

    # -- collection --------------------------------------------------------

    def _add_def(self, node: Node, name_node: Node | None) -> None:
        if name_node is not None:
            self.def_spans.append(
                (node.start_point[0], node.start_point[1],
                 node.end_point[0], node.end_point[1],
                 (name_node.start_point[0], name_node.start_point[1])))

    def _walk(self, node: Node) -> None:
        for child in node.children:
            if child.type in self.g.defs:
                name = child.child_by_field_name("name")
                self._add_def(child, name)
                if child.type in self.g.class_defs and name is not None:
                    self._collect_heritage(child, name)
                if (self.lang == "python" and name is not None
                        and node.type == "decorated_definition"):
                    self._check_entry_decorators(node, name)
            elif (self.lang != "python"
                    and child.type == "call_expression"):
                self._check_route_registration(child)
            elif self.g.fn_value_defs and child.type in (
                    "variable_declarator", "public_field_definition",
                    "field_definition"):
                value = child.child_by_field_name("value")
                if value is not None and value.type in (
                        "arrow_function", "function_expression"):
                    self._add_def(child, child.child_by_field_name("name"))
            self._walk(child)

    def _check_entry_decorators(self, decorated: Node, name: Node) -> None:
        for deco in decorated.children:
            if deco.type != "decorator":
                continue
            text = self.src[deco.start_byte:deco.end_byte].decode(
                "utf-8", "replace")
            head = text.lstrip("@").split("(", 1)[0].rsplit(".", 1)[-1]
            kind = ("http" if head in _HTTP_DECORATORS
                    else "cli" if head in _CLI_DECORATORS else None)
            if kind is not None:
                self.entry_points[
                    (name.start_point[0], name.start_point[1])] = kind
                return

    def _check_route_registration(self, call: Node) -> None:
        fn = call.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return
        prop = fn.child_by_field_name("property")
        if prop is None or prop.text is None \
                or prop.text.decode() not in _ROUTE_METHODS:
            return
        args = call.child_by_field_name("arguments")
        if args is None:
            return
        pos_args = [a for a in args.children if a.is_named]
        # first argument must look like a route path, so Map.get('key') and
        # option-object .get(...) calls don't produce phantom entry points
        if not pos_args or pos_args[0].type != "string" \
                or not pos_args[0].text.decode().strip("'\"`").startswith(
                    ("/", "*")):
            return
        for arg in pos_args[1:]:
            if arg.type == "identifier":
                self.entry_ref_positions.add(
                    (arg.start_point[0], arg.start_point[1]))

    def _collect_heritage(self, cls_node: Node, name_node: Node) -> None:
        name_pos = (name_node.start_point[0], name_node.start_point[1])
        if self.lang == "python":
            sup = cls_node.child_by_field_name("superclasses")
            if sup is not None:
                self.class_bases.append(
                    (sup.start_point, sup.end_point, name_pos))
        else:
            for child in cls_node.children:
                if child.type == "class_heritage":
                    self.class_bases.append(
                        (child.start_point, child.end_point, name_pos))

    # -- the three questions ----------------------------------------------

    def enclosing_def(self, line: int, char: int) -> Point | None:
        """Name position of the tightest definition containing (line, char)."""
        for sl, sc, el, ec, name_pos in self.def_spans:
            if (sl, sc) <= (line, char) <= (el, ec):
                return name_pos
        return None

    def is_call_position(self, line: int, char: int) -> bool:
        node = self.tree.root_node.descendant_for_point_range(
            (line, char), (line, char))
        if node is None or node.type not in self.g.idents:
            return False
        p = node.parent
        if p is None:
            return False
        if self.lang == "python":
            if p.type == "call" and p.child_by_field_name("function") == node:
                return True
            return (p.type == "attribute"
                    and p.child_by_field_name("attribute") == node
                    and p.parent is not None and p.parent.type == "call"
                    and p.parent.child_by_field_name("function") == p)
        if (p.type == "call_expression"
                and p.child_by_field_name("function") == node):
            return True
        if (p.type == "member_expression"
                and p.child_by_field_name("property") == node
                and p.parent is not None
                and p.parent.type == "call_expression"
                and p.parent.child_by_field_name("function") == p):
            return True
        return (p.type == "new_expression"
                and p.child_by_field_name("constructor") == node)

    def in_import_statement(self, line: int, char: int) -> bool:
        # scip-python 0.6.6 never sets the SCIP Import role (M0 finding);
        # imports are recovered syntactically for every language.
        node = self.tree.root_node.descendant_for_point_range(
            (line, char), (line, char))
        while node is not None:
            if node.type in self.g.imports:
                return True
            if (node.type == "export_statement"
                    and node.child_by_field_name("source") is not None):
                return True  # export ... from './x' re-exports
            node = node.parent
        return False

    def in_base_list(self, line: int, char: int) -> Point | None:
        """Class-name position if (line, char) is inside a heritage clause."""
        for start, end, cls_name_pos in self.class_bases:
            if start <= (line, char) < end:
                return cls_name_pos
        return None
