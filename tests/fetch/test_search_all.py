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


def _records(monkeypatch, table):
    monkeypatch.setattr(fetch._resolve, "enrich_via_crossref", table.get)


def test_search_all_follows_a_notice_to_the_article_it_concerns(monkeypatch):
    """A retraction or correction has its own DOI and no supplementary files of its own.

    Searching one finds nothing, and that failure is indistinguishable from a paper that
    published no source data -- so the DOI is followed to the article first.
    """
    seen = []
    _no_sources(monkeypatch, seen)
    _records(monkeypatch, {
        "10.1038/notice-9": {"title": "Retraction Note: x", "original_dois": ["10.1038/orig-1"]},
        "10.1038/orig-1": {"title": "x", "original_dois": []},
    })

    fetch.search_all("10.1038/notice-9", per_source=5)

    assert seen and set(seen) == {"10.1038/orig-1"}


def test_search_all_follows_a_correction_of_a_correction(monkeypatch):
    """Corrections get corrected. Stopping after one hop lands on another bare notice --
    the original bug, one link further down."""
    seen = []
    _no_sources(monkeypatch, seen)
    _records(monkeypatch, {
        "10.1038/notice-a": {"title": "Author Correction: x", "original_dois": ["10.1038/notice-b"]},
        "10.1038/notice-b": {"title": "Author Correction: x", "original_dois": ["10.1038/article-c"]},
        "10.1038/article-c": {"title": "x", "original_dois": []},
    })

    fetch.search_all("10.1038/notice-a", per_source=5)

    assert seen and set(seen) == {"10.1038/article-c"}


def test_search_all_stops_rather_than_looping_on_a_cycle(monkeypatch):
    """A record that points back at something already visited must end the walk, not spin."""
    seen = []
    _no_sources(monkeypatch, seen)
    _records(monkeypatch, {
        "10.1038/notice-a": {"title": "Correction: x", "original_dois": ["10.1038/notice-b"]},
        "10.1038/notice-b": {"title": "Correction: x", "original_dois": ["10.1038/notice-a"]},
    })

    fetch.search_all("10.1038/notice-a", per_source=5)

    assert seen and set(seen) == {"10.1038/notice-b"}


def test_search_all_will_not_pick_between_the_articles_a_notice_names(monkeypatch):
    """A bulk notice names several papers. Following one would decide silently which paper's
    data is downloaded -- and because the chosen DOI feeds `match_signals`, the confidence
    check would then endorse a stranger's data as this paper's. It searches the notice
    instead, and says what it declined to choose between."""
    seen = []
    # EVERY source returns nothing, which is what searching a notice actually does. An
    # earlier version of this test invented one candidate so that `cands[0]` would exist
    # to read the answer off -- that invented candidate was the only reason it passed, and
    # the zero-candidate case it could not express was the one that was broken.
    for name in ("search_nature_esm", "search_zenodo", "search_figshare",
                 "search_dryad", "search_europepmc"):
        monkeypatch.setattr(fetch._sources, name, lambda q, size=5: seen.append(q) or [])
    _records(monkeypatch, {
        "10.1038/bulk-notice": {"title": "Expression of Concern: three articles",
                                "original_dois": ["10.1038/a-1", "10.1038/b-2", "10.1038/c-3"]},
    })

    cands, resolution = fetch.search_all("10.1038/bulk-notice", per_source=5,
                                         with_resolution=True)

    assert set(seen) == {"10.1038/bulk-notice"}, "must not follow any one of them"
    assert cands == []
    assert resolution["notice_names_several_articles"] == \
        ["10.1038/a-1", "10.1038/b-2", "10.1038/c-3"]
    assert resolution["ambiguous_notice_doi"] == "10.1038/bulk-notice"
    assert resolution["resolved_doi"] == "10.1038/bulk-notice"
    assert resolution["followed_a_notice"] is False


def test_search_all_leaves_an_ordinary_paper_alone(monkeypatch):
    """The common path must not change: a research article is searched as itself."""
    seen = []
    _no_sources(monkeypatch, seen)
    calls = []

    def enrich(doi):
        calls.append(doi)
        return {"title": "A research article", "original_dois": []}

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
