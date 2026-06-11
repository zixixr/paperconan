import paperconan.fetch._sources as S

OA_XML = ('<OA><records returned-count="1"><record id="PMC8844425" license="CC BY">'
          '<link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/f7/45/PMC8844425.tar.gz"/>'
          '</record></records></OA>')


def test_resolve_oa_package_returns_https_tgz(monkeypatch):
    monkeypatch.setattr(S._http, "get_text", lambda url, **k: OA_XML)
    pkg = S.resolve_pmc_oa_package("PMC8844425")
    assert pkg is not None
    # ftp:// rewritten to https:// (urllib/httpx can't do ftp)
    assert pkg["url"] == "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/f7/45/PMC8844425.tar.gz"
    assert pkg["name"] == "PMC8844425.tar.gz"


def test_resolve_oa_package_none_when_not_in_oa_subset(monkeypatch):
    monkeypatch.setattr(S._http, "get_text",
                        lambda url, **k: '<OA><records returned-count="0"></records></OA>')
    assert S.resolve_pmc_oa_package("PMC0000000") is None


def test_europepmc_attaches_oa_package(monkeypatch):
    search_json = {"resultList": {"result": [
        {"pmcid": "PMC8844425", "doi": "10.1038/s41467-022-28338-0",
         "title": "T", "authorString": "A B", "firstPublicationDate": "2022-02-14",
         "hasSuppl": "Y"}]}}
    monkeypatch.setattr(S._http, "get_json", lambda url, **k: search_json)
    monkeypatch.setattr(S._http, "get_text", lambda url, **k: OA_XML)
    cands = S.search_europepmc("10.1038/s41467-022-28338-0")
    assert len(cands) == 1
    assert cands[0]["oa_package"]["url"].endswith("PMC8844425.tar.gz")
    # still keeps the legacy zip as fallback
    assert cands[0]["supplementary_archive"]["url"].endswith("/supplementaryFiles")
