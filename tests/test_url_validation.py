"""Paste-a-URL validation: allowlist, ref extraction, injection guards."""

import pytest
from fastapi import HTTPException

from osprey.api.main import _validate_git_url


def ok(url, ref=None):
    return _validate_git_url(url, None, ref)


class TestUrlForms:
    def test_plain_repo(self):
        assert ok("https://github.com/pallets/itsdangerous") == (
            "https://github.com/pallets/itsdangerous.git", "itsdangerous",
            "HEAD")

    def test_dot_git_and_trailing_slash(self):
        assert ok("https://github.com/a/b.git/")[0] == \
            "https://github.com/a/b.git"

    def test_release_tag_url(self):
        _, _, ref = ok("https://github.com/pallets/itsdangerous/releases/tag/2.2.0")
        assert ref == "2.2.0"

    def test_tree_branch_url(self):
        assert ok("https://github.com/honojs/hono/tree/main")[2] == "main"
        assert ok("https://github.com/honojs/hono/tree/feat/x-y")[2] == "feat/x-y"

    def test_explicit_ref_beats_url_ref(self):
        assert ok("https://github.com/a/b/tree/main", ref="v9")[2] == "v9"


class TestGuards:
    def test_host_allowlist(self):
        with pytest.raises(HTTPException) as e:
            ok("https://evil.internal/a/b")
        assert "not allowed" in e.value.detail

    def test_scheme_and_shape(self):
        for bad in ("file:///etc/passwd", "ssh://github.com/a/b",
                    "https://github.com/onlyowner"):
            with pytest.raises(HTTPException):
                ok(bad)

    def test_ref_option_injection_blocked(self):
        # a ref starting with '-' could reach `git fetch` as an option
        with pytest.raises(HTTPException) as e:
            ok("https://github.com/a/b", ref="--upload-pack=/bin/sh")
        assert "invalid ref" in e.value.detail

    def test_ref_charset(self):
        with pytest.raises(HTTPException):
            ok("https://github.com/a/b", ref="v1;rm -rf")
