# tests/fetch/test_sources_dryad.py
from paperconan.fetch import _sources, _http


def test_dryad_candidate_follows_version_chain(monkeypatch, fixture, fake_http):
    routes = [
        ("/api/v2/datasets/doi%3A10.5061%2Fdryad.7rh4625", fixture("dryad_dataset.json")),
        ("/api/v2/versions/124910/files", fixture("dryad_files.json")),
    ]
    monkeypatch.setattr(_http, "get_json", fake_http["get"](routes))

    c = _sources._dryad_candidate("doi:10.5061/dryad.7rh4625")
    assert c["cand_id"] == "dryad:10.5061/dryad.7rh4625"
    assert c["authors"] == ["Sam Jones"]
    assert c["all_files_count"] == 2
    assert [f["name"] for f in c["tabular_files"]] == ["measurements.csv"]
    assert c["tabular_files"][0]["download_url"] == "https://datadryad.org/api/v2/files/9/download"
    assert "10.1098/rspb.2018.0123" in c["related_dois"]
