import io
import json
import urllib.error
import warnings

import pytest

from paperconan.fetch import _http


class _StubResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def test_get_json_builds_query_and_parses(monkeypatch):
    seen = {}

    def stub_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _StubResp(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(_http.urllib.request, "urlopen", stub_urlopen)
    out = _http.get_json("https://api.example.org/x", params={"q": "a b", "size": 3})
    assert out == {"ok": True}
    assert seen["url"].startswith("https://api.example.org/x?")
    assert "q=a+b" in seen["url"] and "size=3" in seen["url"]
    assert seen["headers"].get("accept") == "application/json"


def test_post_json_sends_body(monkeypatch):
    seen = {}

    def stub_urlopen(req, timeout=None):
        seen["data"] = req.data
        seen["method"] = req.get_method()
        return _StubResp(json.dumps([{"id": 1}]).encode())

    monkeypatch.setattr(_http.urllib.request, "urlopen", stub_urlopen)
    out = _http.post_json("https://api.example.org/search", {"search_for": "x"})
    assert out == [{"id": 1}]
    assert seen["method"] == "POST"
    assert json.loads(seen["data"]) == {"search_for": "x"}


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: _http.get_json("https://api.example.org/data"),
        lambda: _http.get_text("https://api.example.org/page"),
        lambda: _http.post_json(
            "https://api.example.org/search", {"query": "x"}
        ),
    ],
)
def test_http_helpers_close_and_reraise_identical_http_error(
    monkeypatch, invoke
):
    error = urllib.error.HTTPError(
        "https://api.example.org/error",
        503,
        "Unavailable",
        {},
        io.BytesIO(b"temporary failure"),
    )

    def fail(_req, timeout=None):
        raise error

    monkeypatch.setattr(_http.urllib.request, "urlopen", fail)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        with pytest.raises(urllib.error.HTTPError) as caught:
            invoke()

    assert caught.value is error
    assert error.closed
