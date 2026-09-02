"""nature.com / Springer ESM source: a paper's own article page links its
supplementary / Source Data files on the open static-content.springer.com and
media.springernature.com CDNs — reachable for both OA and paywalled articles
without a login."""
from __future__ import annotations

import html as html_lib
import re
import urllib.parse

from . import _http
from ._files import make_fileref
from ._sources import _candidate

# Older articles link ESM from static-content.springer.com/esm/...; newer ones
# from media.springernature.com/<view>/springer-static/esm/... — accept both.
_ESM_HREF = re.compile(
    r'href="(https://(?:static-content\.springer\.com/esm'
    r'|media\.springernature\.com/[a-z0-9_-]+/springer-static/esm)'
    r'/[^"]+)"',
    re.I,
)
# The same link WITH the anchor it sits in, so the text the page put on it survives. That
# text is what says which figure a file holds -- the filename is an accession string and
# says nothing. Only the anchor's own text is taken: a label guessed from nearby markup
# could attach a figure number to a file that does not hold it, and a wrong label is worse
# than none here, because it would let a finding be credited to a figure it never came from.
_ESM_ANCHOR = re.compile(
    r'<a\b[^>]*\bhref="(https://(?:static-content\.springer\.com/esm'
    r'|media\.springernature\.com/[a-z0-9_-]+/springer-static/esm)'
    # The text may not run across another `<a`: a page can nest anchors or leave one
    # unclosed, and a plain `.*?` then pairs this URL with the NEXT link's caption while
    # swallowing that link whole, so it loses its own. Refusing to cross the boundary lets
    # each anchor match on its own.
    r'/[^"]+)"[^>]*>((?:(?!<a\b).)*?)</a\s*>',
    re.I | re.S,
)
_TAGS = re.compile(r"<[^>]*>")
_HREF = re.compile(r"""\bhref\s*=\s*(["'])(.*?)\1""", re.I | re.S)
_FULL_IMAGE_SRC = re.compile(
    r'(https://media\.springernature\.com/full/[^"\']+\.(?:png|jpe?g|tiff?))',
    re.I,
)
_NATURE_ORIGIN = "https://www.nature.com"
# nature.com bounces anonymous readers through its cookie-consent gateway
# (303 → idp.nature.com/authorize → /transit → back to the article), so the
# origin allowlist must include it or the article fetch fails mid-redirect.
_IDP_ORIGIN = "https://idp.nature.com"
_ALLOWED_ORIGINS = {_NATURE_ORIGIN, _IDP_ORIGIN}
# One article may lead to at most this many bounded figure-page requests.
_MAX_FIGURE_PAGES = 100
# Article and figure HTML bodies share this deterministic per-response ceiling.
_MAX_NATURE_HTML_BYTES = 5 * 1024 * 1024


def _anchor_labels(html: str) -> dict:
    """{esm url: the anchor's own visible text}, for anchors that have any.

    First occurrence wins, matching the URL dedupe in `parse_nature_esm_links` -- a page that
    links one file twice is described by whichever mention comes first, which is a policy
    rather than a judgement about which text is better.
    """
    out = {}
    for url, inner in _ESM_ANCHOR.findall(html or ""):
        url = url.replace("&amp;", "&")
        text = html_lib.unescape(_TAGS.sub(" ", inner))
        text = " ".join(text.split())
        if text and url not in out:
            out[url] = text
    return out


def parse_nature_esm_links(html: str) -> list[dict]:
    """Extract ESM file refs from a Nature article page. Returns make_fileref dicts,
    deduped by URL, with ext derived from the URL path and `label` carrying the anchor's
    own text where it has one -- see `_ESM_ANCHOR` for why only the anchor's."""
    labels = _anchor_labels(html)
    seen, refs = set(), []
    for url in _ESM_HREF.findall(html or ""):
        url = url.replace("&amp;", "&")
        if url in seen:
            continue
        seen.add(url)
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        refs.append(make_fileref(name, None, url, label=labels.get(url)))
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
            allowed_origins=_ALLOWED_ORIGINS,
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
                allowed_origins=_ALLOWED_ORIGINS,
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
