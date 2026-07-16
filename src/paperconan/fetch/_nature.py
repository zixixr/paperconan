"""nature.com / Springer ESM source: a paper's own article page links its
supplementary / Source Data files on the open static-content.springer.com CDN —
reachable for both OA and paywalled articles without a login."""
from __future__ import annotations

import html as html_lib
import re
import urllib.parse

from . import _http
from ._files import make_fileref
from ._sources import _candidate

_ESM_HREF = re.compile(
    r'href="(https://static-content\.springer\.com/esm/[^"]+)"', re.I)
_HREF = re.compile(r"""\bhref\s*=\s*(["'])(.*?)\1""", re.I | re.S)
_FULL_IMAGE_SRC = re.compile(
    r'(https://media\.springernature\.com/full/[^"\']+\.(?:png|jpe?g|tiff?))',
    re.I,
)
_NATURE_ORIGIN = "https://www.nature.com"
# One article may lead to at most this many bounded figure-page requests.
_MAX_FIGURE_PAGES = 100
# Article and figure HTML bodies share this deterministic per-response ceiling.
_MAX_NATURE_HTML_BYTES = 5 * 1024 * 1024


def parse_nature_esm_links(html: str) -> list[dict]:
    """Extract ESM file refs from a Nature article page. Returns make_fileref dicts,
    deduped by URL, with ext derived from the URL path."""
    seen, refs = set(), []
    for url in _ESM_HREF.findall(html or ""):
        url = url.replace("&amp;", "&")
        if url in seen:
            continue
        seen.add(url)
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        refs.append(make_fileref(name, None, url))
    return refs


def parse_nature_figure_links(html: str, article_url: str) -> list[str]:
    article = urllib.parse.urlsplit(article_url)
    if (
        article.scheme.lower() != "https"
        or article.hostname != "www.nature.com"
        or article.username is not None
        or article.password is not None
        or article.query
        or article.fragment
    ):
        return []
    try:
        article_port = article.port
    except ValueError:
        return []
    article_path = article.path.rstrip("/")
    if article_port not in (None, 443) or not re.fullmatch(
        r"/articles/[^/]+",
        article_path,
    ):
        return []

    pages = set()
    for _, raw_href in _HREF.findall(html or ""):
        href = html_lib.unescape(raw_href).strip()
        base = (
            article_url.rstrip("/") + "/"
            if href.startswith("figures/")
            else article_url
        )
        candidate = urllib.parse.urlsplit(urllib.parse.urljoin(base, href))
        try:
            port = candidate.port
        except ValueError:
            continue
        if (
            candidate.scheme.lower() != "https"
            or candidate.hostname != "www.nature.com"
            or candidate.username is not None
            or candidate.password is not None
            or port not in (None, 443)
            or candidate.query
            or candidate.fragment
        ):
            continue
        match = re.fullmatch(
            re.escape(article_path) + r"/figures/([1-9]\d*)",
            candidate.path,
        )
        if match is not None:
            pages.add(int(match.group(1)))
    return [
        f"{_NATURE_ORIGIN}{article_path}/figures/{number}"
        for number in sorted(pages)[:_MAX_FIGURE_PAGES]
    ]


def parse_nature_full_image(html: str) -> dict | None:
    match = _FULL_IMAGE_SRC.search(html or "")
    if not match:
        return None
    url = match.group(1).replace("&amp;", "&")
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    return make_fileref(name, None, url)


def search_nature_esm(query, size=5):
    """If `query` is a DOI, fetch its nature.com page and return one candidate
    carrying its ESM files. Non-DOI queries return [] (this source is DOI-keyed)."""
    doi = str(query).strip()
    if not doi.startswith("10.1038/"):
        return []
    suffix = doi[len("10.1038/"):]
    url = f"https://www.nature.com/articles/{suffix}"
    try:
        html = _http.get_text(
            url,
            timeout=60,
            max_bytes=_MAX_NATURE_HTML_BYTES,
            allowed_origins={_NATURE_ORIGIN},
        )
    except Exception:
        return []
    all_files = parse_nature_esm_links(html)
    for figure_url in parse_nature_figure_links(html, url):
        try:
            figure_html = _http.get_text(
                figure_url,
                timeout=60,
                max_bytes=_MAX_NATURE_HTML_BYTES,
                allowed_origins={_NATURE_ORIGIN},
            )
            ref = parse_nature_full_image(figure_html)
        except Exception:
            ref = None
        if ref is not None:
            all_files.append(ref)
    if not all_files:
        return []
    c = _candidate("nature_esm", suffix, doi, None, [], None, all_files, [doi])
    c["match_signals"] = {"doi_in_related": True}
    return [c]
