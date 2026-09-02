"""paperconan data-fetch: locate and download a paper's tabular source data
from open repositories (Zenodo / Figshare / Dryad)."""
from __future__ import annotations

from . import _sources, _resolve
from ._download import download_candidate  # noqa: F401


# A correction can itself be corrected, so the link is followed more than once -- but not
# indefinitely, and never back onto a DOI already visited.
_MAX_NOTICE_HOPS = 3


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


def search_all(query, per_source=5, *, with_resolution=False):
    """Ranked candidates for `query`.

    With `with_resolution=True`, returns `(candidates, resolution)` instead. The
    resolution has to travel BESIDE the list rather than on its members: a notice DOI has
    no supplementary files of its own, so "no candidates at all" is the ordinary result
    for one, and metadata carried on candidates is exactly the metadata a caller cannot
    read in the case this feature exists to serve. It reached review carried per-candidate
    and the empty list threw the whole substitution away in silence.
    """
    q = _resolve.normalize_query(query)
    doi, title, authors = q["doi"], q["title"], []
    ambiguous, ambiguous_doi = [], None
    if q["is_doi"]:
        enriched = _resolve.enrich_via_crossref(doi) or {}
        # A retraction, correction or expression of concern has its own DOI and no
        # supplementary files. Searching one finds nothing, and that failure is
        # indistinguishable from a paper that published none -- so follow it to the article
        # it concerns and search for THAT. The link rides on the record just fetched.
        #
        # Only when it names exactly ONE article. A bulk correction, or an expression of
        # concern covering several papers, names many; picking one would decide silently
        # which paper's data gets downloaded, and because the picked DOI then feeds
        # `match_signals`, the confidence check would go on to endorse a stranger's data as
        # this paper's. Better to search the notice, find nothing, and say why.
        seen = {doi}
        for _hop in range(_MAX_NOTICE_HOPS):
            originals = enriched.get("original_dois") or []
            if len(originals) > 1:
                # `doi`, not the query: after a hop it is an intermediate notice that
                # names several, and telling the user their own DOI named several would
                # be false as well as unactionable.
                ambiguous, ambiguous_doi = list(originals), doi
                break
            if not originals or originals[0] in seen:
                break
            doi = originals[0]
            seen.add(doi)
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
        c["resolved_doi"] = paper["doi"]
    cands.sort(key=_rank, reverse=True)
    if not with_resolution:
        return cands
    return cands, {
        "query_doi": q["doi"],
        "resolved_doi": paper["doi"],
        "followed_a_notice": bool(q["doi"]) and paper["doi"] != q["doi"],
        "ambiguous_notice_doi": ambiguous_doi,
        "notice_names_several_articles": ambiguous,
        "title": title,
    }
