import io
import paperconan.fetch._http as _http


class _StubResponse:
    def __init__(self, body):
        self._stream = io.BytesIO(body.encode())
        self.headers = {}

    def read(self, size=-1):
        return self._stream.read(size)

    def geturl(self):
        return "https://example.org/a"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._stream.close()


def test_get_text_returns_decoded_body(monkeypatch):
    captured = {}
    def stub_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["ua"] = req.headers.get("User-agent")
        return _StubResponse("<html>hi</html>")
    monkeypatch.setattr(_http, "open_http", stub_urlopen)
    out = _http.get_text("https://example.org/a", params={"x": "1"})
    assert out == "<html>hi</html>"
    assert captured["url"] == "https://example.org/a?x=1"
    assert captured["ua"]  # a User-Agent was sent
