# tests/fetch/conftest.py
import json
import os
import pytest

_FX = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def fixture():
    return load_fixture


def make_stub_get_json(routes):
    """routes: list of (url_substring, fixture_object). First match wins."""
    def _stub(url, params=None, headers=None, timeout=15):
        for sub, obj in routes:
            if sub in url:
                return obj
        raise AssertionError(f"no stub route for GET {url}")
    return _stub


def make_stub_post_json(routes):
    def _stub(url, payload, headers=None, timeout=15):
        for sub, obj in routes:
            if sub in url:
                return obj
        raise AssertionError(f"no stub route for POST {url}")
    return _stub


@pytest.fixture
def stub_http():
    return {"get": make_stub_get_json, "post": make_stub_post_json}
