import paperconan.fetch as F


def test_search_all_includes_nature_esm_and_ranks_it_first(monkeypatch):
    nat_cand = {"cand_id": "nature_esm:x", "source": "nature_esm",
                "tabular_files": [{"name": "sd.xlsx", "ext": "xlsx", "download_url": "u"}],
                "all_files": [], "match_signals": None}
    monkeypatch.setattr(F._sources, "search_nature_esm", lambda q, size=5: [dict(nat_cand)], raising=False)
    monkeypatch.setattr(F._sources, "search_zenodo", lambda q, size=5: [])
    monkeypatch.setattr(F._sources, "search_figshare", lambda q, size=5: [])
    monkeypatch.setattr(F._sources, "search_dryad", lambda q, size=5: [])
    monkeypatch.setattr(F._sources, "search_europepmc", lambda q, size=5: [])
    monkeypatch.setattr(F._resolve, "enrich_via_crossref", lambda doi: None)
    cands = F.search_all("10.1038/s41467-022-28338-0")
    assert cands and cands[0]["source"] == "nature_esm"
