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
    paper = {"doi": q["doi"], "title": q["title"], "authors": []}
    if q["is_doi"]:
        enriched = _resolve.enrich_via_crossref(q["doi"])
        if enriched:
            paper["title"] = paper["title"] or enriched.get("title")
            paper["authors"] = enriched.get("authors") or []
    search_term = q["doi"] or q["title"] or query

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
