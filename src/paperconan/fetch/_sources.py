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
