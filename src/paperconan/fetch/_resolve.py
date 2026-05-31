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
    """Best-effort title/authors/year for a paper DOI. Returns None on any failure."""
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
    return {"doi": doi, "title": title, "authors": authors, "year": year}


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
        f"Not found in Zenodo / Figshare / Dryad. Source data for {doi} is most likely",
        f"hosted by {publisher} on the article page:",
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
