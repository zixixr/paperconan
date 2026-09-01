"""paperconan data-fetch: locate and download a paper's tabular source data
from open repositories (Zenodo / Figshare / Dryad)."""
from __future__ import annotations

from . import _sources, _resolve
from ._download import download_candidate  # noqa: F401


def _rank(cand):
    sig = cand.get("match_signals") or {}
    score = 0.0
    if sig.get("doi_in_related"):
        score += 100
    score += (sig.get("title_overlap") or 0) * 10
    score += (sig.get("author_overlap") or 0) * 5
    if cand.get("tabular_files"):
        score += 2
    if cand.get("supplementary_archive"):
        score += 2
    if cand.get("oa_package"):
        score += 3
    return score


def search_all(query, per_source=5):
    q = _resolve.normalize_query(query)
    doi, title, authors = q["doi"], q["title"], []
    if q["is_doi"]:
        enriched = _resolve.enrich_via_crossref(doi) or {}
        # A retraction, correction or expression of concern has its own DOI and no
        # supplementary files. Searching one finds nothing, and that failure is
        # indistinguishable from a paper that published none -- so follow it to the article
        # it concerns and search for THAT. The link rides on the record just fetched.
        original = enriched.get("original_doi")
        if original:
            doi = original
            enriched = _resolve.enrich_via_crossref(doi) or {}
        title = title or enriched.get("title")
        authors = enriched.get("authors") or []
    paper = {"doi": doi, "title": title, "authors": authors}
    search_term = doi or q["title"] or query

    cands = []
    for fn in (_sources.search_nature_esm, _sources.search_zenodo, _sources.search_figshare,
               _sources.search_dryad, _sources.search_europepmc):
        try:
            cands.extend(fn(search_term, size=per_source))
        except Exception:
            continue
    for c in cands:
        c["match_signals"] = _resolve.match_signals(c, paper)
    cands.sort(key=_rank, reverse=True)
    return cands
