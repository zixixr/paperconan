"""Thin stdlib HTTP helpers returning parsed JSON. No third-party deps."""
from __future__ import annotations
import json
import operator
import urllib.error
import urllib.parse
import urllib.request

_UA = "paperconan-fetch/0.6 (+https://github.com/zixixr/paperconan)"
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    """Raised when an HTTP response exceeds its configured byte limit."""


def _validate_max_bytes(max_bytes):
    try:
        max_bytes = operator.index(max_bytes)
    except TypeError:
        raise ResponseTooLargeError(
            "max_bytes must be a non-negative integer"
        ) from None
    if max_bytes < 0:
        raise ResponseTooLargeError("max_bytes must be non-negative")
    return max_bytes


def _content_length_exceeds(content_length, max_bytes):
    if not (
        isinstance(content_length, str)
        and content_length.isascii()
        and content_length.isdigit()
    ):
        return False

    significant = content_length.lstrip("0") or "0"
    limit = str(max_bytes)
    return len(significant) > len(limit) or (
        len(significant) == len(limit) and significant > limit
    )


def _read_limited(resp, max_bytes):
    content_length = resp.headers.get("Content-Length")
    if _content_length_exceeds(content_length, max_bytes):
        raise ResponseTooLargeError(
            f"response exceeds max_bytes ({max_bytes})"
        )

    chunks = []
    total = 0
    while True:
        chunk = resp.read(min(65536, max_bytes - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(
                f"response exceeds max_bytes ({max_bytes})"
            )
        chunks.append(chunk)


def _open_url(req, timeout):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        try:
            error.close()
        except Exception:
            pass
        raise


def get_json(
    url,
    params=None,
    headers=None,
    timeout=15,
    max_bytes=_DEFAULT_MAX_RESPONSE_BYTES,
):
    max_bytes = _validate_max_bytes(max_bytes)
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    h = {"Accept": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    with _open_url(req, timeout) as resp:
        return json.loads(
            _read_limited(resp, max_bytes).decode("utf-8", "replace")
        )


def get_text(
    url,
    params=None,
    headers=None,
    timeout=30,
    max_bytes=_DEFAULT_MAX_RESPONSE_BYTES,
):
    """GET a text resource (HTML/XML) and return the decoded body as str."""
    max_bytes = _validate_max_bytes(max_bytes)
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    h = {"Accept": "text/html,application/xml;q=0.9,*/*;q=0.8", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    with _open_url(req, timeout) as resp:
        return _read_limited(resp, max_bytes).decode("utf-8", "replace")


def post_json(
    url,
    payload,
    headers=None,
    timeout=15,
    max_bytes=_DEFAULT_MAX_RESPONSE_BYTES,
):
    max_bytes = _validate_max_bytes(max_bytes)
    body = json.dumps(payload).encode("utf-8")
    h = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with _open_url(req, timeout) as resp:
        return json.loads(
            _read_limited(resp, max_bytes).decode("utf-8", "replace")
        )
