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
