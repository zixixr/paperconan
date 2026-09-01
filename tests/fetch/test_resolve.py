from paperconan.fetch import _resolve


def test_normalize_query_detects_doi():
    q = _resolve.normalize_query("10.1371/journal.pone.0173664")
    assert q["is_doi"] is True
    assert q["doi"] == "10.1371/journal.pone.0173664"


def test_normalize_query_treats_text_as_title():
    q = _resolve.normalize_query("Array programming with NumPy")
    assert q["is_doi"] is False
    assert q["title"] == "Array programming with NumPy"


def test_match_signals_doi_in_related():
    cand = {"related_dois": ["10.15761/JTS.1000455"], "title": "Platelets data",
            "authors": ["Doe, Jane"]}
    paper = {"doi": "10.15761/JTS.1000455", "title": None, "authors": []}
    sig = _resolve.match_signals(cand, paper)
    assert sig["doi_in_related"] is True
    assert sig["title_overlap"] is None


def test_match_signals_title_overlap():
    cand = {"related_dois": [], "title": "Platelets retrospective biomarker dataset",
            "authors": ["Doe, Jane"]}
    paper = {"doi": "x", "title": "Platelets biomarker study", "authors": ["Jane Doe"]}
    sig = _resolve.match_signals(cand, paper)
    assert sig["doi_in_related"] is False
    assert sig["title_overlap"] > 0.3
    assert sig["author_overlap"] > 0.0


def test_normalize_query_strips_doi_org_prefix():
    q = _resolve.normalize_query("https://doi.org/10.1371/journal.pone.0173664")
    assert q["is_doi"] is True
    assert q["doi"] == "10.1371/journal.pone.0173664"


def test_journal_guidance_springer_nature_points_to_article():
    """When the open repos miss, a Nature DOI must get a concrete pointer to the
    article's Source Data section — that's where MOESM xlsx files actually live."""
    g = _resolve.journal_guidance({"doi": "10.1038/s41590-026-02471-0"})
    assert "https://doi.org/10.1038/s41590-026-02471-0" in g
    assert "Source data" in g
    assert "MOESM" in g
    assert "paperconan" in g  # tells the user the next step


def test_journal_guidance_generic_publisher():
    g = _resolve.journal_guidance({"doi": "10.9999/unknown.123"})
    assert "https://doi.org/10.9999/unknown.123" in g
    assert "paperconan" in g


def test_journal_guidance_without_doi_does_not_crash():
    g = _resolve.journal_guidance({"doi": None, "title": "Some paper"})
    assert "DOI" in g


def test_journal_guidance_never_recommends_scraping():
    """Honesty rule: paperconan must not tell users (or imply) it scrapes publishers."""
    g = _resolve.journal_guidance({"doi": "10.1038/x"})
    assert "manual" in g.lower()


def test_is_confident_match_requires_doi_or_strong_title():
    """A repo full-text search can return totally unrelated deposits; only a DOI hit
    or a strong title overlap should count as 'this is the paper's data'."""
    assert _resolve.is_confident_match({"match_signals": {"doi_in_related": True}})
    assert _resolve.is_confident_match({"match_signals": {"title_overlap": 0.8}})
    # the failure mode we actually hit: many tabular files, but no real match
    assert not _resolve.is_confident_match(
        {"match_signals": {"doi_in_related": False, "title_overlap": 0.02}})
    assert not _resolve.is_confident_match({"match_signals": None})
    assert not _resolve.is_confident_match({})


# --- a DOI that names a notice, not the paper the notice is about ----------------------

def _crossref(monkeypatch, message):
    """Stand in for the Crossref lookup so these stay offline."""
    from paperconan.fetch import _http
    monkeypatch.setattr(_http, "get_json", lambda *a, **k: {"message": message})


def test_original_article_doi_reads_update_to(monkeypatch):
    """A retraction or correction carries no source data of its own.

    Its Crossref record points at the article it concerns, so the fetch should follow that
    rather than searching for supplementary files attached to a one-page notice.
    """
    _crossref(monkeypatch, {
        "title": ["Retraction Note: Something about cells"],
        "type": "journal-article",
        "update-to": [{"DOI": "10.1038/s41467-021-00000-1", "type": "retraction"}],
    })

    assert _resolve.original_article_doi("10.1038/s41467-026-00000-9") == \
        "10.1038/s41467-021-00000-1"


def test_original_article_doi_falls_back_to_relation(monkeypatch):
    """Some publishers record the link only under `relation`, not `update-to`."""
    _crossref(monkeypatch, {
        "title": ["Author Correction: Something about cells"],
        "relation": {"is-correction-of": [{"id": "10.1038/s41586-020-00000-2",
                                           "id-type": "doi"}]},
    })

    assert _resolve.original_article_doi("10.1038/s41586-026-00000-8") == \
        "10.1038/s41586-020-00000-2"


def test_original_article_doi_is_none_for_an_ordinary_paper(monkeypatch):
    """The common case must not be disturbed: a research article resolves to itself."""
    _crossref(monkeypatch, {"title": ["Structure of a membrane protein"],
                            "type": "journal-article"})

    assert _resolve.original_article_doi("10.1038/s41586-020-00000-2") is None


def test_original_article_doi_is_none_when_the_notice_names_no_original(monkeypatch):
    """A notice with nothing to follow is not an error, and must not return itself."""
    _crossref(monkeypatch, {"title": ["Editorial Expression of Concern"]})

    assert _resolve.original_article_doi("10.1126/science.0000000") is None


def test_original_article_doi_survives_a_lookup_failure(monkeypatch):
    from paperconan.fetch import _http

    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(_http, "get_json", boom)

    assert _resolve.original_article_doi("10.1038/s41467-026-00000-9") is None
