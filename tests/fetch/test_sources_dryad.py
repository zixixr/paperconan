# tests/fetch/test_sources_dryad.py
from paperconan.fetch import _sources, _http


def test_dryad_candidate_follows_version_chain(monkeypatch, fixture, stub_http):
    routes = [
        ("/api/v2/datasets/doi%3A10.5061%2Fdryad.7rh4625", fixture("dryad_dataset.json")),
        ("/api/v2/versions/124910/files", fixture("dryad_files.json")),
    ]
    monkeypatch.setattr(_http, "get_json", stub_http["get"](routes))

    c = _sources._dryad_candidate("doi:10.5061/dryad.7rh4625")
    assert c["cand_id"] == "dryad:10.5061/dryad.7rh4625"
    assert c["authors"] == ["Sam Jones"]
    assert c["all_files_count"] == 2
    assert [f["name"] for f in c["tabular_files"]] == ["measurements.csv"]
    assert c["tabular_files"][0]["download_url"] == "https://datadryad.org/api/v2/files/9/download"
    assert "10.1098/rspb.2018.0123" in c["related_dois"]


def test_dryad_candidate_omits_files_without_valid_download_links(
    monkeypatch,
    fixture,
    stub_http,
):
    files = {
        "_embedded": {
            "stash:files": [
                {
                    "path": "valid.csv",
                    "size": 1,
                    "_links": {
                        "stash:download": {
                            "href": "//cdn.example.net/files/valid.csv",
                        },
                    },
                },
                {"path": "missing.csv", "size": 2, "_links": {}},
                {
                    "path": "empty.csv",
                    "size": 3,
                    "_links": {"stash:download": {"href": "  "}},
                },
                {
                    "path": "ftp.csv",
                    "size": 4,
                    "_links": {
                        "stash:download": {
                            "href": "ftp://files.example.net/ftp.csv",
                        },
                    },
                },
                {
                    "path": "credentials.csv",
                    "size": 5,
                    "_links": {
                        "stash:download": {
                            "href": "https://user:secret@files.example.net/data.csv",
                        },
                    },
                },
                {
                    "path": "scheme-relative-credentials.csv",
                    "size": 5,
                    "_links": {
                        "stash:download": {
                            "href": "//user:secret@files.example.net/data.csv",
                        },
                    },
                },
                {
                    "path": "malformed.csv",
                    "size": 6,
                    "_links": {
                        "stash:download": {
                            "href": "https://files.example.net:not-a-port/data.csv",
                        },
                    },
                },
                {
                    "path": "missing-host.csv",
                    "size": 7,
                    "_links": {
                        "stash:download": {
                            "href": "https:///files/missing-host.csv",
                        },
                    },
                },
            ],
        },
    }
    routes = [
        (
            "/api/v2/datasets/doi%3A10.5061%2Fdryad.7rh4625",
            fixture("dryad_dataset.json"),
        ),
        ("/api/v2/versions/124910/files", files),
    ]
    monkeypatch.setattr(_http, "get_json", stub_http["get"](routes))

    candidate = _sources._dryad_candidate("doi:10.5061/dryad.7rh4625")

    assert candidate["all_files_count"] == 1
    assert candidate["all_files"] == [{
        "name": "valid.csv",
        "ext": "csv",
        "size": 1,
        "download_url": "https://cdn.example.net/files/valid.csv",
    }]
