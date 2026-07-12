# tests/fetch/test_sources_figshare.py
from paperconan.fetch import _sources, _http


def test_search_figshare_fetches_article_files(monkeypatch, fixture, stub_http):
    monkeypatch.setattr(_http, "post_json",
                        stub_http["post"]([("api.figshare.com/v2/articles/search",
                                            fixture("figshare_search.json"))]))
    monkeypatch.setattr(_http, "get_json",
                        stub_http["get"]([("api.figshare.com/v2/articles/32340066",
                                           fixture("figshare_article.json"))]))

    cands = _sources.search_figshare("thrombocytopenia", size=5)
    assert len(cands) == 1
    c = cands[0]
    assert c["cand_id"] == "figshare:32340066"
    assert c["authors"] == ["Alice Smith"]
    assert c["all_files_count"] == 2
    assert [f["name"] for f in c["tabular_files"]] == ["Data Sheet 1.xlsx"]
    assert c["tabular_files"][0]["download_url"].startswith("https://ndownloader.figshare.com/")
