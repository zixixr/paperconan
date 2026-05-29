"""Resolve a paper DOI/title into a search query and score candidate matches."""
from __future__ import annotations
import re

from . import _http

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


def normalize_query(text):
    s = (text or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    if _DOI_RE.match(s):
        return {"raw": text, "is_doi": True, "doi": s, "title": None}
    return {"raw": text, "is_doi": False, "doi": None, "title": s}


def enrich_via_crossref(doi):
    """Best-effort title/authors/year for a paper DOI. Returns None on any failure."""
    try:
        m = _http.get_json(f"https://api.crossref.org/works/{doi}").get("message", {})
    except Exception:
        return None
    title = (m.get("title") or [None])[0]
    authors = [f"{a.get('given','')} {a.get('family','')}".strip()
               for a in m.get("author", [])]
    year = None
    dp = m.get("issued", {}).get("date-parts", [[None]])
    if dp and dp[0]:
        year = str(dp[0][0])
    return {"doi": doi, "title": title, "authors": authors, "year": year}


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_signals(cand, paper):
    related = set(cand.get("related_dois") or [])
    doi_in_related = bool(paper.get("doi") and paper["doi"] in related)
    title_overlap = None
    if paper.get("title"):
        title_overlap = round(_jaccard(_tokens(paper["title"]), _tokens(cand.get("title"))), 3)
    author_overlap = None
    if paper.get("authors"):
        pa = _tokens(" ".join(paper["authors"]))
        ca = _tokens(" ".join(cand.get("authors") or []))
        author_overlap = round(_jaccard(pa, ca), 3)
    return {"doi_in_related": doi_in_related,
            "title_overlap": title_overlap, "author_overlap": author_overlap}
