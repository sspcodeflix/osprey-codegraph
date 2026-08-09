"""Classifier regression tests. Every case here was discovered against real
code in M0 (m0/REPORT.md) - these are the cases that decide whether CALLS
edges can be trusted.
"""

from osprey.classifier import FileFacts, language_for_path

PY = b"""\
import json
from utils import helper

def process(data):
    return json.loads(data)

def run():
    result = process("x")            # call position -> CALLS
    handlers = {"csv": process}      # value position -> REFERENCES
    obj.method(1)                    # attribute call -> CALLS
    cb = obj.method                  # attribute value -> REFERENCES
    return handlers, cb

class Child(Base):
    def go(self):
        return process("y")
"""


def facts(src: bytes, lang: str) -> FileFacts:
    return FileFacts(src, lang)


class TestPython:
    f = facts(PY, "python")

    def test_call_position(self):
        assert self.f.is_call_position(7, 13)          # process("x")

    def test_value_position_is_not_call(self):
        assert not self.f.is_call_position(8, 23)      # {"csv": process}

    def test_attribute_call(self):
        assert self.f.is_call_position(9, 8)           # obj.method(1)

    def test_attribute_value_is_not_call(self):
        assert not self.f.is_call_position(10, 13)     # cb = obj.method

    def test_import_recovery(self):
        # scip-python never sets the Import role; syntax must recover it
        assert self.f.in_import_statement(0, 7)        # import json
        assert self.f.in_import_statement(1, 17)       # from utils import
        assert not self.f.in_import_statement(7, 13)

    def test_enclosing_def_attribution(self):
        assert self.f.enclosing_def(7, 13) == (6, 4)   # inside run()
        assert self.f.enclosing_def(0, 7) is None      # module level

    def test_base_list(self):
        assert self.f.in_base_list(13, 12) == (13, 6)  # Base in Child(Base)
        assert self.f.in_base_list(7, 13) is None


TS = b"""\
import { getPath } from './utils/url'

const getPathNoStrict = (req: Request) => {
  return getPath(req)
}

class Hono {
  getPath: GetPath
  #dispatch(req: Request) { return null }
  constructor(options?: { getPath?: GetPath }) {
    this.getPath = options?.getPath ?? getPath
  }
  fetch(req: Request) {
    return this.#dispatch(req)
  }
  make() { return new Router() }
}

export { getPathNoStrict } from './other'
"""


class TestTypeScript:
    f = facts(TS, "typescript")

    def test_call_position(self):
        assert self.f.is_call_position(3, 9)           # getPath(req)

    def test_fallback_value_is_not_call(self):
        # this.getPath = options?.getPath ?? getPath   (M0 hono case)
        assert not self.f.is_call_position(10, 39)

    def test_private_method_call(self):
        # this.#dispatch(req): private_property_identifier (M0 finding -
        # missing this node type silently degraded CALLS to REFERENCES)
        assert self.f.is_call_position(13, 16)

    def test_new_expression(self):
        assert self.f.is_call_position(15, 26)         # new Router()

    def test_import_and_reexport(self):
        assert self.f.in_import_statement(0, 9)        # import { getPath }
        assert self.f.in_import_statement(18, 9)       # export ... from
        assert not self.f.in_import_statement(3, 9)

    def test_arrow_const_is_a_definition_span(self):
        # const getPathNoStrict = (...) => {...} must attribute its body
        assert self.f.enclosing_def(3, 9) == (2, 6)

    def test_language_detection(self):
        assert language_for_path("a/b.ts") == "typescript"
        assert language_for_path("a/b.tsx") == "tsx"
        assert language_for_path("a/b.py") == "python"
        assert language_for_path("a/b.rs") is None


ENTRY = b"""\
from fastapi import FastAPI
import click

app = FastAPI()

@app.get("/users")
def list_users():
    return []

@app.route("/legacy", methods=["GET"])
def legacy():
    return []

@click.command()
def sync():
    pass

@functools.cache
def helper():
    return 1

if __name__ == "__main__":
    sync()
"""


class TestEntryPoints:
    f = facts(ENTRY, "python")

    def test_http_decorators(self):
        assert self.f.entry_points.get((6, 4)) == "http"    # list_users
        assert self.f.entry_points.get((10, 4)) == "http"   # legacy

    def test_cli_decorator(self):
        assert self.f.entry_points.get((14, 4)) == "cli"    # sync

    def test_plain_decorator_is_not_entry(self):
        assert (18, 4) not in self.f.entry_points           # helper

    def test_main_guard(self):
        assert self.f.has_main_guard
        assert not facts(PY, "python").has_main_guard


TS_ROUTES = b"""\
import { listUsers } from './handlers'

app.get('/users', listUsers)
app.post('/users', auth, createUser)
router.use('/admin', adminOnly)
cache.get('users')
config.get('key', fallback)
"""


class TestTsRouteEntryPoints:
    f = facts(TS_ROUTES, "typescript")

    def test_handler_identifiers_marked(self):
        assert (2, 18) in self.f.entry_ref_positions   # listUsers
        assert (3, 19) in self.f.entry_ref_positions   # auth (middleware)
        assert (3, 25) in self.f.entry_ref_positions   # createUser
        assert (4, 21) in self.f.entry_ref_positions   # adminOnly

    def test_non_route_get_calls_ignored(self):
        # Map.get / config.get: first arg is not a route path
        assert (6, 18) not in self.f.entry_ref_positions   # fallback

    def test_route_string_itself_not_marked(self):
        assert (2, 8) not in self.f.entry_ref_positions
