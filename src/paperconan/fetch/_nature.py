"""nature.com / Springer ESM source: a paper's own article page links its
supplementary / Source Data files on the open static-content.springer.com CDN —
reachable for both OA and paywalled articles without a login."""
from __future__ import annotations

import re
import urllib.parse

from . import _http
from ._files import make_fileref
from ._sources import _candidate

_ESM_HREF = re.compile(
    r'href="(https://static-content\.springer\.com/esm/[^"]+)"', re.I)


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


def search_nature_esm(query, size=5):
    """If `query` is a DOI, fetch its nature.com page and return one candidate
    carrying its ESM files. Non-DOI queries return [] (this source is DOI-keyed)."""
    doi = str(query).strip()
    if not doi.startswith("10.1038/"):
        return []
    suffix = doi[len("10.1038/"):]
    url = f"https://www.nature.com/articles/{suffix}"
    try:
        html = _http.get_text(url, timeout=60)
    except Exception:
        return []
    all_files = parse_nature_esm_links(html)
    if not all_files:
        return []
    c = _candidate("nature_esm", suffix, doi, None, [], None, all_files, [doi])
    c["match_signals"] = {"doi_in_related": True}
    return [c]
