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
