"""Thin stdlib HTTP helpers returning parsed JSON. No third-party deps."""
from __future__ import annotations
import json
import urllib.error
import urllib.parse
import urllib.request

_UA = "paperconan-fetch/0.6 (+https://github.com/zixixr/paperconan)"


def _open_url(req, timeout):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        try:
            error.close()
        except Exception:
            pass
        raise


def get_json(url, params=None, headers=None, timeout=15):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    h = {"Accept": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    with _open_url(req, timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def get_text(url, params=None, headers=None, timeout=30):
    """GET a text resource (HTML/XML) and return the decoded body as str."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    h = {"Accept": "text/html,application/xml;q=0.9,*/*;q=0.8", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    with _open_url(req, timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def post_json(url, payload, headers=None, timeout=15):
    body = json.dumps(payload).encode("utf-8")
    h = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": _UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with _open_url(req, timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))
