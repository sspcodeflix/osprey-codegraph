"""Fixture corpus harvested from code-graph-rag's test suite (MIT, © vitali87
and contributors - patterns adapted, not copied code). Each case is a
real-world shape their years of testing proved matters; here they pin the
classifier behaviors that Osprey's dead-code and CALLS-trust stories rest on.
"""

from osprey.classifier import FileFacts

# -- harvested: object-literal / bound-function handoffs (their
#    test_object_literal_callback_references.py) --------------------------

BIND = b"""\
import { handleError } from './utils'

export function AddItem() {
  return doMutation({ onError: handleError.bind(showToast) })
}
"""


class TestBoundFunctionHandoff:
    f = FileFacts(BIND, "typescript")

    def test_bound_function_is_not_a_call(self):
        # handleError in `handleError.bind(x)` must be REFERENCES - it is the
        # object of the member expression, not the callee. Misclassifying it
        # CALLS would be wrong; missing it entirely reports it dead.
        assert not self.f.is_call_position(3, 31)      # handleError

    def test_bind_itself_is_the_call(self):
        assert self.f.is_call_position(3, 43)          # .bind(


OBJECT_CALLBACKS = b"""\
export function AddUser() {
  const mutation = useMutation({
    mutationFn: (data) => save(data),
    onSuccess: () => { reset() },
    onError: handleError,
  })
  return mutation
}
"""


class TestObjectLiteralCallbacks:
    f = FileFacts(OBJECT_CALLBACKS, "typescript")

    def test_identifier_value_is_not_a_call(self):
        assert not self.f.is_call_position(4, 13)      # onError: handleError

    def test_call_inside_inline_arrow_is_a_call(self):
        assert self.f.is_call_position(2, 26)          # save(data)
        assert self.f.is_call_position(3, 23)          # reset()

    def test_inline_arrow_calls_attribute_to_named_enclosing_def(self):
        # inline arrows in object literals are anonymous; attribution walks
        # out to the nearest NAMED definition (AddUser), keeping the edge
        # connected instead of orphaned
        assert self.f.enclosing_def(2, 26) == (0, 16)
        assert self.f.enclosing_def(3, 23) == (0, 16)


# -- harvested: JSX component usage (their test_jsx_component_references.py)

JSX = b"""\
import { Header } from './header'

export function App() {
  return <Header title={getTitle()} />
}
"""


class TestJsxComponentReference:
    f = FileFacts(JSX, "tsx")

    def test_component_usage_is_not_a_call(self):
        # <Header /> hands the component to the JSX runtime - REFERENCES.
        # Components misread as never-called is the classic dead-code false
        # positive their suite guards against.
        assert not self.f.is_call_position(3, 10)      # <Header

    def test_expression_inside_jsx_attribute_is_a_call(self):
        assert self.f.is_call_position(3, 24)          # getTitle()


# -- harvested: returned cleanup closure ---------------------------------

CLEANUP = b"""\
export function useTimer() {
  const id = setInterval(tick, 1000)
  return () => { clearInterval(id) }
}
"""


class TestCleanupClosure:
    f = FileFacts(CLEANUP, "typescript")

    def test_callback_argument_is_not_a_call(self):
        assert not self.f.is_call_position(1, 25)      # tick (argument)

    def test_call_inside_returned_closure_attributes_to_named_def(self):
        assert self.f.is_call_position(2, 17)          # clearInterval(
        assert self.f.enclosing_def(2, 17) == (0, 16)  # useTimer


# -- harvested: Python import-fallback idiom (their qualified-name
#    collision handling exists because of this shape) ---------------------

IMPORT_FALLBACK = b"""\
try:
    import orjson as json
except ImportError:
    import json

def load(data):
    return json.loads(data)
"""


class TestImportFallback:
    f = FileFacts(IMPORT_FALLBACK, "python")

    def test_both_fallback_arms_recovered_as_imports(self):
        assert self.f.in_import_statement(1, 11)       # orjson
        assert self.f.in_import_statement(3, 11)       # json

    def test_use_site_is_a_call_not_an_import(self):
        assert not self.f.in_import_statement(6, 16)
        assert self.f.is_call_position(6, 16)          # json.loads(


# -- harvested: nested-function attribution ------------------------------

NESTED = b"""\
def outer():
    def inner():
        return helper()
    callbacks.append(inner)
    return inner
"""


class TestNestedAttribution:
    f = FileFacts(NESTED, "python")

    def test_call_in_closure_attributes_to_innermost_def(self):
        assert self.f.enclosing_def(2, 15) == (1, 8)   # helper() -> inner

    def test_closure_passed_as_value_is_not_a_call(self):
        assert not self.f.is_call_position(3, 21)      # append(inner)
        assert self.f.is_call_position(3, 14)          # .append(
