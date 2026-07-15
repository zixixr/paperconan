import http.client
import io
import json
import math
import urllib.error
import warnings

import numpy as np
import pytest

from paperconan.fetch import _http


class _StubResp(io.BytesIO):
    def __init__(self, body, content_length=None):
        super().__init__(body)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size < 0 or size > 65536:
            raise AssertionError("HTTP response reads must be bounded")
        return super().read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _HTTPResponseSocket:
    def __init__(self, wire_bytes):
        self._stream = io.BytesIO(wire_bytes)

    def makefile(self, _mode, _buffering=None):
        return self._stream


def _real_http_response(body, content_length):
    wire_bytes = (
        b"HTTP/1.1 200 OK\r\n"
        b"Connection: close\r\n"
        b"Content-Length: "
        + content_length.encode("ascii")
        + b"\r\n\r\n"
        + body
    )
    response = http.client.HTTPResponse(_HTTPResponseSocket(wire_bytes))
    response.begin()
    return response


class _IndexOnlyLimit:
    def __init__(self, value):
        self._value = value

    def __index__(self):
        return self._value


_HELPER_CASES = [
    (
        "get_json",
        lambda max_bytes: _http.get_json(
            "https://api.example.org/data", max_bytes=max_bytes
        ),
        b'{"ok": true}',
        {"ok": True},
    ),
    (
        "get_text",
        lambda max_bytes: _http.get_text(
            "https://api.example.org/page", max_bytes=max_bytes
        ),
        b"plain text",
        "plain text",
    ),
    (
        "post_json",
        lambda max_bytes: _http.post_json(
            "https://api.example.org/search",
            {"query": "x"},
            max_bytes=max_bytes,
        ),
        b'[{"id": 1}]',
        [{"id": 1}],
    ),
]

_DEFAULT_HELPER_CASES = [
    (
        "get_json",
        lambda: _http.get_json("https://api.example.org/data"),
    ),
    (
        "get_text",
        lambda: _http.get_text("https://api.example.org/page"),
    ),
    (
        "post_json",
        lambda: _http.post_json(
            "https://api.example.org/search", {"query": "x"}
        ),
    ),
]

_MALFORMED_NUMERIC_CONTENT_LENGTHS = [
    pytest.param("9_000_000", id="underscore"),
    pytest.param("+9000000", id="leading-plus"),
    pytest.param(" 9000000", id="leading-whitespace"),
    pytest.param("9000000 ", id="trailing-whitespace"),
    pytest.param("９００００００", id="non-ascii-decimal"),
]

_VERY_LARGE_CAP = 10**5000
_VERY_LARGE_CAP_DECIMAL = "1" + ("0" * 5000)
_VERY_LARGE_CAP_PLUS_ONE_DECIMAL = "1" + ("0" * 4999) + "1"


def test_get_json_builds_query_and_parses(monkeypatch):
    seen = {}

    def stub_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["timeout"] = timeout
        return _StubResp(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(_http.urllib.request, "urlopen", stub_urlopen)
    out = _http.get_json(
        "https://api.example.org/x",
        params={"q": "a b", "size": 3},
        headers={"X-Test": "present"},
        timeout=9,
    )
    assert out == {"ok": True}
    assert seen["url"].startswith("https://api.example.org/x?")
    assert "q=a+b" in seen["url"] and "size=3" in seen["url"]
    assert seen["headers"].get("accept") == "application/json"
    assert seen["headers"].get("x-test") == "present"
    assert seen["timeout"] == 9


def test_post_json_sends_body(monkeypatch):
    seen = {}

    def stub_urlopen(req, timeout=None):
        seen["data"] = req.data
        seen["method"] = req.get_method()
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _StubResp(json.dumps([{"id": 1}]).encode())

    monkeypatch.setattr(_http.urllib.request, "urlopen", stub_urlopen)
    out = _http.post_json("https://api.example.org/search", {"search_for": "x"})
    assert out == [{"id": 1}]
    assert seen["method"] == "POST"
    assert json.loads(seen["data"]) == {"search_for": "x"}
    assert seen["headers"]["content-type"] == "application/json"


@pytest.mark.parametrize(
    "_name,invoke,body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_raise_incomplete_read_for_truncated_real_http_response(
    monkeypatch, _name, invoke, body, _expected
):
    response = _real_http_response(body, "100")
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(http.client.IncompleteRead) as caught:
        invoke(100)

    assert type(caught.value) is http.client.IncompleteRead
    assert caught.value.partial == body
    assert caught.value.expected == 100 - len(body)
    assert body.decode("ascii") not in str(caught.value)
    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_use_eof_for_untrusted_real_http_response(
    monkeypatch, _name, invoke, body, expected
):
    response = _real_http_response(body, "+100")
    assert response.length == 100
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    assert invoke(len(body)) == expected

    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_enforce_cap_for_untrusted_real_http_response(
    monkeypatch, _name, invoke, body, _expected
):
    response = _real_http_response(body + b"x", "+100")
    assert response.length == 100
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(_http.ResponseTooLargeError):
        invoke(len(body))

    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_accept_exact_cap_and_close_response(
    monkeypatch, _name, invoke, body, expected
):
    response = _StubResp(body, content_length=str(len(body)))
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    assert invoke(len(body)) == expected

    assert response.closed
    assert response.read_sizes[-1] == 1
    assert all(0 <= size <= 65536 for size in response.read_sizes)


@pytest.mark.parametrize(
    "content_length",
    [None, "not-a-number"],
    ids=["absent", "malformed"],
)
@pytest.mark.parametrize(
    "_name,invoke,body,expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_accept_exact_cap_with_untrusted_content_length(
    monkeypatch, content_length, _name, invoke, body, expected
):
    response = _StubResp(body, content_length=content_length)
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    assert invoke(len(body)) == expected

    assert response.read_sizes[-1] == 1
    assert response.closed


@pytest.mark.parametrize(
    "content_length",
    _MALFORMED_NUMERIC_CONTENT_LENGTHS,
)
@pytest.mark.parametrize(
    "_name,invoke,body,expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_stream_small_body_for_malformed_numeric_length(
    monkeypatch, content_length, _name, invoke, body, expected
):
    response = _StubResp(body, content_length=content_length)
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    assert invoke(len(body)) == expected

    assert response.read_sizes[-1] == 1
    assert response.closed


@pytest.mark.parametrize(
    "content_length",
    _MALFORMED_NUMERIC_CONTENT_LENGTHS,
)
@pytest.mark.parametrize(
    "_name,invoke,body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_stream_reject_oversize_for_malformed_numeric_length(
    monkeypatch, content_length, _name, invoke, body, _expected
):
    response = _StubResp(body + b"x", content_length=content_length)
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(_http.ResponseTooLargeError):
        invoke(len(body))

    assert response.read_sizes
    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_do_not_trust_equal_content_length_as_eof(
    monkeypatch, _name, invoke, body, _expected
):
    response = _StubResp(body + b"x", content_length=str(len(body)))
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(_http.ResponseTooLargeError):
        invoke(len(body))

    assert response.read_sizes
    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_reject_advertised_oversize_before_read_and_close(
    monkeypatch, _name, invoke, body, _expected
):
    response = _StubResp(body, content_length=str(len(body) + 1))
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(_http.ResponseTooLargeError):
        invoke(len(body))

    assert response.read_sizes == []
    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_reject_very_long_overcap_content_length_before_read(
    monkeypatch, _name, invoke, body, _expected
):
    response = _StubResp(body, content_length="9" * 5000)
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(_http.ResponseTooLargeError) as caught:
        invoke(len(body))

    assert type(caught.value) is _http.ResponseTooLargeError
    assert response.read_sizes == []
    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_accept_very_long_zero_padded_equal_content_length(
    monkeypatch, _name, invoke, body, expected
):
    content_length = ("0" * 5000) + str(len(body))
    response = _StubResp(body, content_length=content_length)
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    assert invoke(len(body)) == expected

    assert response.read_sizes[-1] == 1
    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_accept_small_response_with_very_large_cap(
    monkeypatch, _name, invoke, body, expected
):
    response = _StubResp(body, content_length=str(len(body)))
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    assert invoke(_VERY_LARGE_CAP) == expected

    assert len(response.read_sizes) == 2
    assert all(0 < size <= 65536 for size in response.read_sizes)
    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_stream_when_very_long_content_length_equals_very_large_cap(
    monkeypatch, _name, invoke, body, expected
):
    response = _StubResp(body, content_length=_VERY_LARGE_CAP_DECIMAL)
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    assert invoke(_VERY_LARGE_CAP) == expected

    assert len(response.read_sizes) == 2
    assert all(0 < size <= 65536 for size in response.read_sizes)
    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke,body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_reject_very_long_content_length_above_very_large_cap(
    monkeypatch, _name, invoke, body, _expected
):
    response = _StubResp(
        body,
        content_length=_VERY_LARGE_CAP_PLUS_ONE_DECIMAL,
    )
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(_http.ResponseTooLargeError) as caught:
        invoke(_VERY_LARGE_CAP)

    assert type(caught.value) is _http.ResponseTooLargeError
    assert len(str(caught.value)) < 200
    assert response.read_sizes == []
    assert response.closed


@pytest.mark.parametrize(
    "content_length",
    [None, "not-a-number", "-1", "1"],
    ids=["absent", "malformed", "negative", "understated"],
)
@pytest.mark.parametrize(
    "_name,invoke,body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_enforce_streamed_cap_with_untrusted_content_length(
    monkeypatch, content_length, _name, invoke, body, _expected
):
    response = _StubResp(body + b"x", content_length=content_length)
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(_http.ResponseTooLargeError):
        invoke(len(body))

    assert response.closed
    assert response.read_sizes
    assert all(0 <= size <= 65536 for size in response.read_sizes)


@pytest.mark.parametrize(
    "_name,invoke,_body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_reject_negative_cap_before_network(
    monkeypatch, _name, invoke, _body, _expected
):
    calls = []

    def unexpected_request(*args, **kwargs):
        calls.append(("request", args, kwargs))
        raise AssertionError("request construction was attempted")

    def unexpected_urlopen(_req, timeout=None):
        calls.append(("urlopen", timeout))
        raise AssertionError("network access was attempted")

    monkeypatch.setattr(_http.urllib.request, "Request", unexpected_request)
    monkeypatch.setattr(_http.urllib.request, "urlopen", unexpected_urlopen)

    with pytest.raises(_http.ResponseTooLargeError):
        invoke(-1)

    assert calls == []


@pytest.mark.parametrize(
    "max_bytes",
    [1.5, math.nan, math.inf, "limit-value-sentinel"],
    ids=["finite-float", "nan", "positive-infinity", "string"],
)
@pytest.mark.parametrize(
    "_name,invoke,_body,_expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_reject_non_integer_cap_before_network(
    monkeypatch, max_bytes, _name, invoke, _body, _expected
):
    calls = []

    def unexpected_request(*args, **kwargs):
        calls.append(("request", args, kwargs))
        raise AssertionError("request construction was attempted")

    def unexpected_urlopen(_req, timeout=None):
        calls.append(("urlopen", timeout))
        raise AssertionError("network access was attempted")

    monkeypatch.setattr(_http.urllib.request, "Request", unexpected_request)
    monkeypatch.setattr(_http.urllib.request, "urlopen", unexpected_urlopen)

    with pytest.raises(_http.ResponseTooLargeError) as caught:
        invoke(max_bytes)

    assert calls == []
    assert "limit-value-sentinel" not in str(caught.value)
    assert len(str(caught.value)) < 200


@pytest.mark.parametrize(
    "limit_factory",
    [np.int64, _IndexOnlyLimit],
    ids=["numpy-integer", "index-only"],
)
@pytest.mark.parametrize(
    "_name,invoke,body,expected",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_http_helpers_accept_integer_protocol_limits(
    monkeypatch, limit_factory, _name, invoke, body, expected
):
    response = _StubResp(body)
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    assert invoke(limit_factory(len(body))) == expected

    assert response.read_sizes[-1] == 1
    assert response.closed


@pytest.mark.parametrize(
    "_name,invoke",
    _DEFAULT_HELPER_CASES,
    ids=[case[0] for case in _DEFAULT_HELPER_CASES],
)
def test_http_helpers_default_cap_rejects_advertised_oversize_before_read(
    monkeypatch, _name, invoke
):
    response = _StubResp(
        b"",
        content_length=str(_http._DEFAULT_MAX_RESPONSE_BYTES + 1),
    )
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(_http.ResponseTooLargeError):
        invoke()

    assert response.read_sizes == []
    assert response.closed


def test_get_text_accepts_empty_response_at_zero_cap(monkeypatch):
    response = _StubResp(b"", content_length="0")
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    assert _http.get_text(
        "https://api.example.org/empty", max_bytes=0
    ) == ""

    assert response.read_sizes == [1]
    assert response.closed


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: _http.get_json(
            "https://api.example.org/empty", max_bytes=0
        ),
        lambda: _http.post_json(
            "https://api.example.org/empty", {}, max_bytes=0
        ),
    ],
)
def test_json_helpers_close_empty_zero_cap_response_on_parse_failure(
    monkeypatch, invoke
):
    response = _StubResp(b"", content_length="0")
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(json.JSONDecodeError):
        invoke()

    assert response.read_sizes == [1]
    assert response.closed


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: _http.get_json(
            "https://api.example.org/data", max_bytes=1
        ),
        lambda: _http.post_json(
            "https://api.example.org/search", {}, max_bytes=1
        ),
    ],
)
def test_json_helpers_close_response_on_parse_failure(monkeypatch, invoke):
    response = _StubResp(b"{", content_length="1")
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(json.JSONDecodeError):
        invoke()

    assert response.closed


def test_size_error_does_not_expose_url_or_response_body(monkeypatch):
    credential_sentinel = "credential-sentinel"
    path_sentinel = "private-path-sentinel"
    body_sentinel = "response-body-sentinel"
    url = (
        f"https://user:{credential_sentinel}@example.org/"
        f"{path_sentinel}"
    )
    body = body_sentinel.encode()
    response = _StubResp(body)
    monkeypatch.setattr(
        _http.urllib.request, "urlopen", lambda _req, timeout=None: response
    )

    with pytest.raises(_http.ResponseTooLargeError) as caught:
        _http.get_text(url, max_bytes=len(body) - 1)

    message = str(caught.value)
    assert credential_sentinel not in message
    assert path_sentinel not in message
    assert body_sentinel not in message
    assert response.closed


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
