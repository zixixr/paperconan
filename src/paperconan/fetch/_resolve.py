"""Resolve a paper DOI/title into a search query and score candidate matches."""
from __future__ import annotations
import re
import urllib.parse

from . import _http

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


def normalize_query(text):
    s = (text or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    if _DOI_RE.match(s):
        return {"raw": text, "is_doi": True, "doi": s, "title": None}
    return {"raw": text, "is_doi": False, "doi": None, "title": s}


def enrich_via_crossref(doi):
    """Best-effort title/authors/year for a paper DOI. Returns None on any failure.

    Also carries `original_doi`: the article a retraction or correction concerns, when this
    DOI names one. It comes from the same record, so asking costs no extra request -- and a
    separate lookup would double the round-trips on every fetch.
    """
    try:
        m = _http.get_json(
            f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
        ).get("message", {})
    except Exception:
        return None
    title = (m.get("title") or [None])[0]
    authors = [f"{a.get('given','')} {a.get('family','')}".strip()
               for a in m.get("author", [])]
    year = None
    dp = m.get("issued", {}).get("date-parts", [[None]])
    if dp and dp[0]:
        year = str(dp[0][0])
    return {"doi": doi, "title": title, "authors": authors, "year": year,
            "original_doi": _original_from_record(m)}


# A retraction, correction or expression of concern is a page of its own, with its own DOI
# and no supplementary files. Searching one for source data finds nothing, and the failure
# looks exactly like a paper that published none. Crossref records what the notice is about,
# so the DOI of the article itself is recoverable.
_NOTICE_TITLE = re.compile(
    r"^\s*(retraction|retracted|withdrawn|author correction|publisher correction|correction"
    r"|corrigendum|erratum|editorial expression of concern|expression of concern"
    r"|editor'?s note|matters arising)\b",
    re.I,
)
# Where a publisher records the link. `update-to` is the standard place; some use `relation`
# instead, and a few use both.
_ORIGINAL_RELATIONS = ("is-correction-of", "is-retraction-of", "is-comment-on")


def _original_from_record(message):
    """The DOI of the article this Crossref record is a notice about, or None.

    None also covers "it is a notice but names nothing to follow" -- an expression of concern
    that lists no original is not an error, and returning the notice's own DOI would send the
    search straight back where it started.
    """
    title = (message.get("title") or [""])[0]
    if not _NOTICE_TITLE.match(title or ""):
        return None
    for update in (message.get("update-to") or []):
        if update.get("DOI"):
            return update["DOI"]
    relation = message.get("relation") or {}
    for key in _ORIGINAL_RELATIONS:
        for entry in relation.get(key, []):
            if entry.get("id-type") == "doi" and entry.get("id"):
                return entry["id"]
    return None


def original_article_doi(doi):
    """`_original_from_record` for a DOI, fetching the record. One request."""
    try:
        message = _http.get_json(
            f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
        ).get("message", {})
    except Exception:
        return None
    return _original_from_record(message)


# DOI registrant prefix -> publisher, for pointing users at the article page when the
# source data isn't in an open repository. (Source data for high-impact papers usually
# lives on the journal article page, not in Zenodo/Figshare/Dryad.)
_DOI_PUBLISHER = {
    "10.1038": "Springer Nature (Nature journals)",
    "10.1126": "AAAS (Science)",
    "10.1016": "Elsevier (ScienceDirect)",
    "10.1073": "PNAS",
    "10.1101": "Cold Spring Harbor",
    "10.1002": "Wiley",
    "10.1007": "Springer",
    "10.1186": "BioMed Central",
    "10.1371": "PLOS",
    "10.15252": "EMBO Press",
    "10.1172": "JCI",
    "10.1084": "Rockefeller University Press",
    "10.1093": "Oxford University Press",
    "10.1158": "AACR",
}


def journal_guidance(paper):
    """Human-readable next-step when no open-repo candidate was found.

    Points the user at where the source data most likely lives (the publisher's
    article page) using only DOI/metadata — paperconan never scrapes publisher
    pages or bypasses paywalls, so the actual download stays a manual step.
    """
    doi = (paper or {}).get("doi")
    if not doi:
        return ("No DOI given, so I can't link to the article page. If the paper has a "
                "DOI, re-run `paperconan fetch \"<DOI>\"`; otherwise open the journal "
                "article page yourself and download any .xlsx/.csv/.tsv source-data or "
                "supplementary files manually, then run `paperconan <dir>`.")
    prefix = doi.split("/", 1)[0]
    publisher = _DOI_PUBLISHER.get(prefix, "the publisher")
    url = f"https://doi.org/{doi}"
    lines = [
        f"Not found in Zenodo / Figshare / Dryad / Europe PMC. Source data for {doi} is",
        f"most likely hosted by {publisher} on the article page:",
        f"    {url}",
    ]
    if prefix == "10.1038":
        lines.append("There, open the 'Source data' links under the figures and the "
                     "'Supplementary information' section — files are usually named like "
                     "41XXX_..._MOESM<N>_ESM.xlsx.")
    else:
        lines.append("There, look for a 'Supplementary information' / 'Supporting "
                     "information' / 'Source data' section and download the "
                     ".xlsx / .csv / .tsv files.")
    lines.append("Save them into a folder, then run:  paperconan <folder>")
    lines.append("(paperconan does not bypass paywalls or scrape publisher pages — "
                 "this download is a manual step.)")
    return "\n".join(lines)


def is_confident_match(cand, min_title=0.5):
    """Is this candidate confidently the paper's own dataset?

    Repository full-text search (especially figshare/zenodo) routinely returns
    completely unrelated deposits. Only a DOI listed in the dataset's relations,
    or a strong title overlap, should count as "this is the paper's data" — file
    count and source are NOT evidence of a match. Used to stop ``fetch --auto`` /
    ``--download`` from silently fetching a stranger's data.
    """
    sig = cand.get("match_signals") or {}
    if sig.get("doi_in_related"):
        return True
    return (sig.get("title_overlap") or 0) >= min_title


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
