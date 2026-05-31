"""Europe PMC source adapter: for open-access papers (many NIH/Wellcome-funded
Nature papers), supplementary data is reachable via a documented REST API, with
no key and no publisher scraping. This closes the biggest gap — the open repos
don't carry journal-hosted source data."""
from paperconan.fetch import _sources, _http


def test_search_europepmc_builds_supplementary_candidate(monkeypatch, fixture, fake_http):
    routes = [("europepmc/webservices/rest/search", fixture("europepmc_search.json"))]
    monkeypatch.setattr(_http, "get_json", fake_http["get"](routes))

    cands = _sources.search_europepmc("10.1038/s41467-021-22125-z", size=5)
    assert len(cands) == 1
    c = cands[0]
    assert c["source"] == "europepmc"
    assert c["cand_id"] == "europepmc:PMC7985210"
    assert c["doi"] == "10.1038/s41467-021-22125-z"
    arch = c["supplementary_archive"]
    assert arch["url"].endswith("/PMC7985210/supplementaryFiles")
    assert arch["name"].endswith(".zip")


def test_search_europepmc_skips_when_no_supplementary(monkeypatch, fixture, fake_http):
    data = fixture("europepmc_search.json")
    data["resultList"]["result"][0]["hasSuppl"] = "N"
    routes = [("europepmc/webservices/rest/search", data)]
    monkeypatch.setattr(_http, "get_json", fake_http["get"](routes))
    assert _sources.search_europepmc("10.1038/x", size=5) == []


def test_search_europepmc_skips_when_no_pmcid(monkeypatch, fixture, fake_http):
    data = fixture("europepmc_search.json")
    data["resultList"]["result"][0].pop("pmcid")
    routes = [("europepmc/webservices/rest/search", data)]
    monkeypatch.setattr(_http, "get_json", fake_http["get"](routes))
    assert _sources.search_europepmc("10.1038/x", size=5) == []
