from paperconan import fetch


def test_search_all_merges_ranks_and_signals(monkeypatch):
    z = [{"cand_id": "zenodo:1", "source": "zenodo", "id": "1", "doi": "10.x/z",
          "title": "Platelets biomarker dataset", "authors": ["Doe, Jane"],
          "published": "2023", "tabular_files": [{"name": "a.xlsx", "ext": "xlsx",
          "size": 1, "download_url": "u"}], "all_files_count": 1,
          "related_dois": ["10.15761/JTS.1000455"], "match_signals": None}]
    fg = [{"cand_id": "figshare:2", "source": "figshare", "id": "2", "doi": None,
           "title": "Unrelated thing", "authors": [], "published": None,
           "tabular_files": [], "all_files_count": 3, "related_dois": [],
           "match_signals": None}]
    monkeypatch.setattr(fetch._sources, "search_zenodo", lambda q, size=5: z)
    monkeypatch.setattr(fetch._sources, "search_figshare", lambda q, size=5: fg)
    monkeypatch.setattr(fetch._sources, "search_dryad", lambda q, size=5: [])
    monkeypatch.setattr(fetch._sources, "search_europepmc", lambda q, size=5: [])
    monkeypatch.setattr(fetch._resolve, "enrich_via_crossref", lambda doi: None)

    cands = fetch.search_all("10.15761/JTS.1000455", per_source=5)
    # zenodo candidate (doi_in_related + has tabular) ranks above the unrelated figshare one
    assert cands[0]["cand_id"] == "zenodo:1"
    assert cands[0]["match_signals"]["doi_in_related"] is True
    assert all("match_signals" in c and c["match_signals"] is not None for c in cands)


def test_search_all_includes_europepmc_supplementary(monkeypatch):
    ep = [{"cand_id": "europepmc:PMC9", "source": "europepmc", "id": "PMC9",
           "doi": "10.1038/paper", "title": "OA paper", "authors": [], "published": None,
           "tabular_files": [], "all_files_count": 1, "related_dois": [],
           "supplementary_archive": {"url": "https://x/PMC9/supplementaryFiles",
                                     "name": "PMC9_supplementary.zip"},
           "match_signals": None}]
    monkeypatch.setattr(fetch._sources, "search_zenodo", lambda q, size=5: [])
    monkeypatch.setattr(fetch._sources, "search_figshare", lambda q, size=5: [])
    monkeypatch.setattr(fetch._sources, "search_dryad", lambda q, size=5: [])
    monkeypatch.setattr(fetch._sources, "search_europepmc", lambda q, size=5: ep)
    monkeypatch.setattr(fetch._resolve, "enrich_via_crossref", lambda doi: None)

    cands = fetch.search_all("10.1038/paper", per_source=5)
    assert any(c["source"] == "europepmc" and c.get("supplementary_archive") for c in cands)


def _no_sources(monkeypatch, seen):
    """Record the term each source is searched with; return nothing."""
    def spy(q, size=5):
        seen.append(q)
        return []
    for name in ("search_nature_esm", "search_zenodo", "search_figshare",
                 "search_dryad", "search_europepmc"):
        monkeypatch.setattr(fetch._sources, name, spy)


def test_search_all_follows_a_notice_to_the_article_it_concerns(monkeypatch):
    """A retraction or correction has its own DOI and no supplementary files of its own.

    Searching one finds nothing, and that failure is indistinguishable from a paper that
    published no source data -- so the DOI is followed to the article first.
    """
    seen = []
    _no_sources(monkeypatch, seen)
    records = {"10.1038/notice-9": {"title": "Retraction Note: x",
                                    "original_doi": "10.1038/original-1"},
               "10.1038/original-1": {"title": "x", "original_doi": None}}
    monkeypatch.setattr(fetch._resolve, "enrich_via_crossref", records.get)

    fetch.search_all("10.1038/notice-9", per_source=5)

    assert seen and set(seen) == {"10.1038/original-1"}


def test_search_all_leaves_an_ordinary_paper_alone(monkeypatch):
    """The common path must not change: a research article is searched as itself."""
    seen = []
    _no_sources(monkeypatch, seen)
    calls = []

    def enrich(doi):
        calls.append(doi)
        return {"title": "A research article", "original_doi": None}

    monkeypatch.setattr(fetch._resolve, "enrich_via_crossref", enrich)

    fetch.search_all("10.1038/paper-2", per_source=5)

    assert seen and set(seen) == {"10.1038/paper-2"}
    # and it costs no extra round-trip: the notice link rides on the record already fetched
    assert calls == ["10.1038/paper-2"]


def test_search_all_does_not_follow_a_notice_for_a_title_query(monkeypatch):
    """Only a DOI can be a notice. A title query has nothing to resolve."""
    seen = []
    _no_sources(monkeypatch, seen)
    called = []

    def enrich(doi):
        called.append(doi)
        return None

    monkeypatch.setattr(fetch._resolve, "enrich_via_crossref", enrich)

    fetch.search_all("Structure of a membrane protein", per_source=5)

    assert not called
