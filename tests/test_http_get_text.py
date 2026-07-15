import io
import paperconan.fetch._http as _http


class _StubResp:
    def __init__(self, body):
        if isinstance(body, str):
            body = body.encode()
        self._stream = io.BytesIO(body)
        self.headers = {}
        self.closed = False

    def read(self, size=-1):
        if size < 0 or size > 65536:
            raise AssertionError("HTTP response reads must be bounded")
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        self._stream.close()


def test_get_text_returns_decoded_body(monkeypatch):
    captured = {}
    response = _StubResp("<html>hi</html>")

    def stub_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["ua"] = req.headers.get("User-agent")
        return response

    monkeypatch.setattr(_http.urllib.request, "urlopen", stub_urlopen)
    out = _http.get_text("https://example.org/a", params={"x": "1"})
    assert out == "<html>hi</html>"
    assert captured["url"] == "https://example.org/a?x=1"
    assert captured["ua"]  # a User-Agent was sent
    assert response.closed


def test_get_text_preserves_utf8_replacement_decoding(monkeypatch):
    response = _StubResp(b"\xff")
    monkeypatch.setattr(
        _http.urllib.request,
        "urlopen",
        lambda _req, timeout=None: response,
    )

    assert _http.get_text("https://example.org/text") == "\ufffd"
    assert response.closed
