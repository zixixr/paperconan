"""Thin stdlib HTTP helpers returning parsed JSON. No third-party deps."""
from __future__ import annotations
from decimal import Decimal
import http.client
import ipaddress
import json
import operator
import re
import string
import urllib.error
import urllib.parse
import urllib.request

_UA = "paperconan-fetch/0.6 (+https://github.com/zixixr/paperconan)"
_DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_JSON_RESPONSE_MAX_BYTES = _DEFAULT_MAX_RESPONSE_BYTES
_DEFAULT_RESPONSE_LIMIT = object()
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_SENSITIVE_REDIRECT_HEADERS = frozenset({
    "authorization",
    "cookie",
    "cookie2",
    "proxy-authorization",
})


class URLPolicyError(ValueError):
    """A terminal rejection of an HTTP request, redirect, or response URL."""


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


def _is_trusted_content_length(content_length):
    return (
        isinstance(content_length, str)
        and content_length.isascii()
        and content_length.isdigit()
    )


def _content_length_exceeds(content_length, max_bytes):
    if not _is_trusted_content_length(content_length):
        return False
    return Decimal(content_length) > max_bytes


def _response_headers(resp):
    headers = getattr(resp, "headers", None)
    if headers is not None:
        return headers
    info = getattr(resp, "info", None)
    return info() if callable(info) else {}


def _read_limited(resp, max_bytes, *, error_message):
    content_length = _response_headers(resp).get("Content-Length")
    trusted_content_length = _is_trusted_content_length(content_length)
    if _content_length_exceeds(content_length, max_bytes):
        raise ResponseTooLargeError(error_message)

    chunks = []
    total = 0
    while True:
        chunk = resp.read(min(65536, max_bytes - total + 1))
        if not chunk:
            body = b"".join(chunks)
            remaining = getattr(resp, "length", None)
            if trusted_content_length and remaining:
                raise http.client.IncompleteRead(body, remaining)
            return body
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(error_message)
        chunks.append(chunk)


def _has_disallowed_url_character(value):
    return any(
        character.isspace()
        or ord(character) < 32
        or ord(character) == 127
        for character in value
    )


def _valid_hostname(hostname):
    if (
        not hostname
        or _has_disallowed_url_character(hostname)
        or len(hostname) - len(hostname.rstrip(".")) > 1
    ):
        return False
    if ":" in hostname:
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError:
            return False
        return True
    try:
        ipaddress.IPv4Address(hostname)
    except ValueError:
        pass
    else:
        return True
    if hostname.replace(".", "").isdigit():
        return False
    try:
        ascii_hostname = hostname.rstrip(".").encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return False
    if not ascii_hostname or len(ascii_hostname) > 253:
        return False
    return all(
        _HOST_LABEL.fullmatch(label) is not None
        for label in ascii_hostname.split(".")
    )


def is_valid_http_url(url):
    if (
        not isinstance(url, str)
        or not url
        or _has_disallowed_url_character(url)
    ):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        username = parts.username
        password = parts.password
        port = parts.port
    except (AttributeError, TypeError, ValueError):
        return False
    if (
        scheme not in {"http", "https"}
        or not parts.netloc
        or username is not None
        or password is not None
        or not _valid_hostname(hostname)
        or parts.netloc.endswith(":")
    ):
        return False
    return port is None or 0 <= port <= 65535


def validate_http_url(url, message="HTTP URL is invalid"):
    if not is_valid_http_url(url):
        raise URLPolicyError(message)
    return url


def resolve_http_url(base_url, target, message="HTTP URL is invalid"):
    validate_http_url(base_url, message)
    if (
        not isinstance(target, str)
        or not target
        or _has_disallowed_url_character(target)
    ):
        raise URLPolicyError(message)
    try:
        raw_parts = urllib.parse.urlsplit(target)
        if (
            (raw_parts.scheme and not raw_parts.netloc)
            or (target.startswith("//") and not raw_parts.netloc)
        ):
            raise URLPolicyError(message)
        resolved = urllib.parse.urljoin(base_url, target)
    except (TypeError, ValueError):
        raise URLPolicyError(message) from None
    return validate_http_url(resolved, message)


def _normalize_redirect_location(location):
    if (
        not isinstance(location, str)
        or not location
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in location
        )
    ):
        raise URLPolicyError("HTTP redirect URL is invalid")
    try:
        return urllib.parse.quote(
            location,
            encoding="iso-8859-1",
            safe=string.punctuation,
        )
    except (TypeError, UnicodeError):
        raise URLPolicyError("HTTP redirect URL is invalid") from None


def _http_origin(url):
    try:
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        hostname = parts.hostname.casefold()
        port = parts.port
    except (AttributeError, TypeError, ValueError):
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, port


def _strip_cross_origin_sensitive_headers(request, source_url, target_url):
    if _http_origin(source_url) == _http_origin(target_url):
        return
    for headers in (request.headers, request.unredirected_hdrs):
        for name in list(headers):
            if name.casefold() in _SENSITIVE_REDIRECT_HEADERS:
                del headers[name]


def _close_http_response(response):
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class ValidatedHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = resolve_http_url(
            req.full_url,
            newurl,
            "HTTP redirect URL is invalid",
        )
        parts = urllib.parse.urlsplit(target)
        if not parts.path and parts.netloc:
            target = urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, "/", parts.query, parts.fragment)
            )
        method = req.get_method()
        if code == 308 and method in {"GET", "HEAD"}:
            redirected = urllib.request.Request(
                target,
                method=method,
                headers={
                    name: value
                    for name, value in req.headers.items()
                    if name.lower() not in {"content-length", "content-type"}
                },
                origin_req_host=req.origin_req_host,
                unverifiable=True,
            )
        else:
            redirected = super().redirect_request(
                req,
                fp,
                code,
                msg,
                headers,
                target,
            )
        if redirected is not None:
            _strip_cross_origin_sensitive_headers(
                redirected,
                req.full_url,
                target,
            )
        return redirected

    def http_error_302(self, req, fp, code, msg, headers):
        location = headers.get("location") or headers.get("uri")
        if location is None:
            _close_http_response(fp)
            return None
        try:
            new = self.redirect_request(
                req,
                fp,
                code,
                msg,
                headers,
                _normalize_redirect_location(location),
            )
        except URLPolicyError:
            try:
                fp.close()
            except Exception:
                pass
            raise
        if new is None:
            return None
        target = new.full_url
        if hasattr(req, "redirect_dict"):
            visited = new.redirect_dict = req.redirect_dict
            if (
                visited.get(target, 0) >= self.max_repeats
                or len(visited) >= self.max_redirections
            ):
                error = urllib.error.HTTPError(
                    req.full_url,
                    code,
                    self.inf_msg + msg,
                    headers,
                    fp,
                )
                _close_http_response(fp)
                raise error
        else:
            visited = new.redirect_dict = req.redirect_dict = {}
        visited[target] = visited.get(target, 0) + 1
        fp.close()
        return self.parent.open(new, timeout=req.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = (
        http_error_302
    )


def open_http(req, timeout):
    validate_http_url(req.full_url, "HTTP request URL is invalid")
    opener = urllib.request.build_opener(ValidatedHTTPRedirectHandler())
    try:
        return opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        _close_http_response(exc)
        raise


def validated_response_url(resp):
    geturl = getattr(resp, "geturl", None)
    if not callable(geturl):
        raise URLPolicyError("HTTP response URL is invalid")
    try:
        final_url = geturl()
    except (TypeError, ValueError):
        raise URLPolicyError("HTTP response URL is invalid") from None
    return validate_http_url(final_url, "HTTP response URL is invalid")


def _read_json_response(resp, max_bytes):
    body = _read_limited(
        resp,
        max_bytes,
        error_message="JSON response exceeds byte limit",
    )
    return json.loads(body.decode("utf-8", "replace"))


def _origin(url):
    try:
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        username = parts.username
        password = parts.password
        port = parts.port
    except (AttributeError, TypeError, ValueError):
        return None
    if (
        scheme not in {"http", "https"}
        or not hostname
        or username is not None
        or password is not None
    ):
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return (scheme, hostname.lower(), port)


def _allowed_origin_keys(allowed_origins):
    if allowed_origins is None:
        return None
    values = (
        [allowed_origins]
        if isinstance(allowed_origins, str)
        else allowed_origins
    )
    keys = {_origin(value) for value in values}
    if None in keys:
        raise ValueError("text response origin configuration is invalid")
    return keys


def _require_allowed_origin(url, allowed_origin_keys):
    if allowed_origin_keys is not None and _origin(url) not in allowed_origin_keys:
        raise ValueError("text response origin is not allowed")


class _AllowedOriginRedirectHandler(ValidatedHTTPRedirectHandler):
    def __init__(self, allowed_origin_keys):
        super().__init__()
        self.allowed_origin_keys = allowed_origin_keys

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target = urllib.parse.urljoin(req.full_url, newurl)
        except ValueError:
            raise ValueError("text response origin is not allowed") from None
        _require_allowed_origin(target, self.allowed_origin_keys)
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            target,
        )

    def http_error_302(self, req, fp, code, msg, headers):
        try:
            return super().http_error_302(req, fp, code, msg, headers)
        except ValueError:
            _close_http_response(fp)
            raise

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = (
        http_error_302
    )


def get_json(
    url,
    params=None,
    headers=None,
    timeout=15,
    max_bytes=_DEFAULT_RESPONSE_LIMIT,
):
    if max_bytes is _DEFAULT_RESPONSE_LIMIT:
        max_bytes = _JSON_RESPONSE_MAX_BYTES
    max_bytes = _validate_max_bytes(max_bytes)
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    validate_http_url(url, "HTTP request URL is invalid")
    h = {"Accept": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    with open_http(req, timeout=timeout) as resp:
        validated_response_url(resp)
        return _read_json_response(resp, max_bytes)


def get_text(
    url,
    params=None,
    headers=None,
    timeout=30,
    max_bytes=_DEFAULT_RESPONSE_LIMIT,
    *,
    allowed_origins=None,
):
    """GET a text resource (HTML/XML) and return the decoded body as str."""
    if max_bytes is _DEFAULT_RESPONSE_LIMIT:
        max_bytes = _DEFAULT_MAX_RESPONSE_BYTES
    max_bytes = _validate_max_bytes(max_bytes)
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    validate_http_url(url, "HTTP request URL is invalid")
    allowed_origin_keys = _allowed_origin_keys(allowed_origins)
    _require_allowed_origin(url, allowed_origin_keys)
    h = {"Accept": "text/html,application/xml;q=0.9,*/*;q=0.8", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    if allowed_origin_keys is None:
        response = open_http(req, timeout=timeout)
    else:
        opener = urllib.request.build_opener(
            _AllowedOriginRedirectHandler(allowed_origin_keys)
        )
        try:
            response = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            _close_http_response(exc)
            raise
    with response as resp:
        final_url = validated_response_url(resp)
        if allowed_origin_keys is not None:
            _require_allowed_origin(final_url, allowed_origin_keys)
        body = _read_limited(
            resp,
            max_bytes,
            error_message="text response exceeds byte limit",
        )
        return body.decode("utf-8", "replace")


def post_json(
    url,
    payload,
    headers=None,
    timeout=15,
    max_bytes=_DEFAULT_RESPONSE_LIMIT,
):
    if max_bytes is _DEFAULT_RESPONSE_LIMIT:
        max_bytes = _JSON_RESPONSE_MAX_BYTES
    max_bytes = _validate_max_bytes(max_bytes)
    validate_http_url(url, "HTTP request URL is invalid")
    body = json.dumps(payload).encode("utf-8")
    h = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with open_http(req, timeout=timeout) as resp:
        validated_response_url(resp)
        return _read_json_response(resp, max_bytes)
