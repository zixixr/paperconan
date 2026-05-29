# tests/fetch/test_sources_zenodo.py
from paperconan.fetch import _sources, _http


def test_search_zenodo_normalizes_candidate(monkeypatch, fixture, fake_http):
    routes = [("zenodo.org/api/records", fixture("zenodo_search.json"))]
    monkeypatch.setattr(_http, "get_json", fake_http["get"](routes))

    cands = _sources.search_zenodo("10.15761/JTS.1000455", size=5)
    assert len(cands) == 1
    c = cands[0]
    assert c["source"] == "zenodo"
    assert c["cand_id"] == "zenodo:10277693"
    assert c["doi"] == "10.5281/zenodo.10277693"
    assert c["authors"] == ["Doe, Jane", "Roe, Richard"]
    assert c["published"] == "2023-12-07"
    assert c["all_files_count"] == 2
    assert [f["name"] for f in c["tabular_files"]] == ["BASE_INFO.xlsx"]
    assert c["tabular_files"][0]["download_url"].endswith("/content")
    assert "10.15761/JTS.1000455" in c["related_dois"]
