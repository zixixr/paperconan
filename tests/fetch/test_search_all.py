from paperconan import fetch


def test_search_all_merges_ranks_and_signals(monkeypatch):
    z = [{"cand_id": "zenodo:1", "source": "zenodo", "id": "1", "doi": "10.x/z",
          "title": "Platelets biomarker dataset", "authors": ["Doe, Jane"],
          "published": "2023", "tabular_files": [{"name": "a.xlsx", "ext": "xlsx",
          "size": 1, "download_url": "u"}], "all_files_count": 1,
          "related_dois": ["10.15761/JTS.1000455"], "match_signals": None}]
    fg = [{"cand_id": "figshare:2", "source": "figshare", "id": "2", "doi": None,
           "title": "Unrelated thing", "authors": [], "published": None,
           "tabular_files": [], "all_files_count": 3, "related_dois": [],
           "match_signals": None}]
    monkeypatch.setattr(fetch._sources, "search_zenodo", lambda q, size=5: z)
    monkeypatch.setattr(fetch._sources, "search_figshare", lambda q, size=5: fg)
    monkeypatch.setattr(fetch._sources, "search_dryad", lambda q, size=5: [])
    monkeypatch.setattr(fetch._sources, "search_europepmc", lambda q, size=5: [])
    monkeypatch.setattr(fetch._resolve, "enrich_via_crossref", lambda doi: None)

    cands = fetch.search_all("10.15761/JTS.1000455", per_source=5)
    # zenodo candidate (doi_in_related + has tabular) ranks above the unrelated figshare one
    assert cands[0]["cand_id"] == "zenodo:1"
    assert cands[0]["match_signals"]["doi_in_related"] is True
    assert all("match_signals" in c and c["match_signals"] is not None for c in cands)


def test_search_all_includes_europepmc_supplementary(monkeypatch):
    ep = [{"cand_id": "europepmc:PMC9", "source": "europepmc", "id": "PMC9",
           "doi": "10.1038/paper", "title": "OA paper", "authors": [], "published": None,
           "tabular_files": [], "all_files_count": 1, "related_dois": [],
           "supplementary_archive": {"url": "https://x/PMC9/supplementaryFiles",
                                     "name": "PMC9_supplementary.zip"},
           "match_signals": None}]
    monkeypatch.setattr(fetch._sources, "search_zenodo", lambda q, size=5: [])
    monkeypatch.setattr(fetch._sources, "search_figshare", lambda q, size=5: [])
    monkeypatch.setattr(fetch._sources, "search_dryad", lambda q, size=5: [])
    monkeypatch.setattr(fetch._sources, "search_europepmc", lambda q, size=5: ep)
    monkeypatch.setattr(fetch._resolve, "enrich_via_crossref", lambda doi: None)

    cands = fetch.search_all("10.1038/paper", per_source=5)
    assert any(c["source"] == "europepmc" and c.get("supplementary_archive") for c in cands)
