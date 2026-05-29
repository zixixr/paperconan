"""Per-repository search adapters. Each returns normalized Candidate dicts
(see the plan's shared contracts). Network calls go through _http so tests can
monkeypatch them."""
from __future__ import annotations

from . import _http
from ._files import TABULAR_EXTS, make_fileref


def _candidate(source, cid, doi, title, authors, published, all_files, related):
    tabular = [f for f in all_files if f["ext"] in TABULAR_EXTS]
    return {"cand_id": f"{source}:{cid}", "source": source, "id": str(cid),
            "doi": doi, "title": title or "", "authors": authors or [],
            "published": published, "tabular_files": tabular,
            "all_files": all_files, "all_files_count": len(all_files),
            "related_dois": related or [], "match_signals": None}


def search_zenodo(query, size=5):
    data = _http.get_json("https://zenodo.org/api/records",
                          params={"q": query, "size": size})
    out = []
    for h in data.get("hits", {}).get("hits", []):
        md = h.get("metadata", {})
        all_files = [make_fileref(f.get("key"), f.get("size"),
                                  f.get("links", {}).get("self"))
                     for f in h.get("files", [])]
        related = [r.get("identifier") for r in md.get("related_identifiers", [])
                   if r.get("identifier")]
        out.append(_candidate(
            "zenodo", h.get("id"), h.get("doi"), md.get("title"),
            [c.get("name") for c in md.get("creators", []) if c.get("name")],
            md.get("publication_date"), all_files, related))
    return out


def search_figshare(query, size=5):
    arts = _http.post_json("https://api.figshare.com/v2/articles/search",
                           {"search_for": query, "page_size": size})
    out = []
    for a in arts[:size]:
        aid = a.get("id")
        if aid is None:
            continue
        full = _http.get_json(f"https://api.figshare.com/v2/articles/{aid}")
        all_files = [make_fileref(f.get("name"), f.get("size"), f.get("download_url"))
                     for f in full.get("files", [])]
        authors = [au.get("full_name") for au in full.get("authors", []) if au.get("full_name")]
        out.append(_candidate(
            "figshare", aid, full.get("doi") or None, full.get("title"),
            authors, full.get("published_date"), all_files, []))
    return out


import urllib.parse as _urlparse

_DRYAD = "https://datadryad.org"


def _dryad_candidate(doi):
    enc = _urlparse.quote(doi, safe="")
    ds = _http.get_json(f"{_DRYAD}/api/v2/datasets/{enc}")
    vhref = ds.get("_links", {}).get("stash:version", {}).get("href")
    all_files = []
    if vhref:
        files = _http.get_json(f"{_DRYAD}{vhref}/files")
        for f in files.get("_embedded", {}).get("stash:files", []):
            dl = f.get("_links", {}).get("stash:download", {}).get("href")
            all_files.append(make_fileref(f.get("path"), f.get("size"),
                                          f"{_DRYAD}{dl}" if dl else None))
    authors = [f"{a.get('firstName','')} {a.get('lastName','')}".strip()
               for a in ds.get("authors", [])]
    related = [w.get("identifier") for w in ds.get("relatedWorks", []) if w.get("identifier")]
    bare = doi[4:] if doi.startswith("doi:") else doi
    return _candidate("dryad", bare, bare, ds.get("title"), authors,
                      ds.get("publicationDate"), all_files, related)


def search_dryad(query, size=5):
    data = _http.get_json(f"{_DRYAD}/api/v2/search", params={"q": query, "per_page": size})
    out = []
    for ds in data.get("_embedded", {}).get("stash:datasets", [])[:size]:
        ident = ds.get("identifier")
        if not ident:
            continue
        try:
            out.append(_dryad_candidate(ident))
        except Exception:
            continue
    return out
