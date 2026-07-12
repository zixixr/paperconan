import io
import paperconan.fetch._http as _http


class _StubResp:
    def __init__(self, body): self._b = body.encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_get_text_returns_decoded_body(monkeypatch):
    captured = {}
    def stub_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["ua"] = req.headers.get("User-agent")
        return _StubResp("<html>hi</html>")
    monkeypatch.setattr(_http.urllib.request, "urlopen", stub_urlopen)
    out = _http.get_text("https://example.org/a", params={"x": "1"})
    assert out == "<html>hi</html>"
    assert captured["url"] == "https://example.org/a?x=1"
    assert captured["ua"]  # a User-Agent was sent
