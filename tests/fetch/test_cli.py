import json
from paperconan.fetch import _cli


def test_fetch_list_prints_candidates_json(monkeypatch, capsys):
    cands = [{"cand_id": "zenodo:1", "source": "zenodo", "doi": "10.x/z",
              "title": "T", "tabular_files": [{"name": "a.xlsx"}],
              "all_files_count": 1, "match_signals": {"doi_in_related": True,
              "title_overlap": None, "author_overlap": None}}]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    rc = _cli.fetch_main(["10.15761/JTS.1000455", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["cand_id"] == "zenodo:1"


def test_fetch_download_selected_candidate(monkeypatch, tmp_path):
    cands = [{"cand_id": "zenodo:1", "source": "zenodo", "doi": "10.x/z", "title": "T",
              "tabular_files": [{"name": "a.csv", "ext": "csv", "size": 3,
              "download_url": "u"}], "all_files_count": 1, "match_signals": None}]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    captured = {}
    monkeypatch.setattr(_cli, "download_candidate",
                        lambda c, out_dir, **kw: captured.update(cid=c["cand_id"], out=out_dir)
                        or {"downloaded": [out_dir + "/a.csv"], "skipped": []})
    rc = _cli.fetch_main(["10.x/paper", "--download", "zenodo:1", "--out", str(tmp_path)])
    assert rc == 0
    assert captured["cid"] == "zenodo:1"


def test_fetch_download_missing_candidate_returns_2(monkeypatch):
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: [])
    rc = _cli.fetch_main(["10.x/paper", "--download", "zenodo:999"])
    assert rc == 2


def test_fetch_auto_empty_returns_1(monkeypatch):
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: [])
    rc = _cli.fetch_main(["10.x/paper", "--auto", "--out", "/tmp/pc_auto_empty"])
    assert rc == 1


def test_fetch_auto_downloads_top_candidate(monkeypatch, tmp_path):
    cands = [{"cand_id": "zenodo:1", "source": "zenodo", "title": "T",
              "all_files_count": 1, "match_signals": None,
              "tabular_files": [{"name": "a.csv", "ext": "csv", "size": 3,
              "download_url": "u"}]}]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    captured = {}
    monkeypatch.setattr(_cli, "download_candidate",
                        lambda c, out_dir, **kw: captured.update(cid=c["cand_id"])
                        or {"downloaded": [out_dir + "/a.csv"], "skipped": []})
    rc = _cli.fetch_main(["10.x/paper", "--auto", "--out", str(tmp_path)])
    assert rc == 0
    assert captured["cid"] == "zenodo:1"


def test_fetch_download_and_auto_mutually_exclusive():
    import pytest
    with pytest.raises(SystemExit):
        _cli.fetch_main(["10.x/paper", "--download", "zenodo:1", "--auto"])


def test_fetch_empty_prints_journal_guidance(monkeypatch, capsys):
    """No open-repo hit on a Nature DOI: point the user to the article's Source Data
    section instead of leaving them with a dead end."""
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: [])
    rc = _cli.fetch_main(["10.1038/s41590-026-02471-0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "doi.org/10.1038/s41590-026-02471-0" in out
    assert "Source data" in out


def test_fetch_empty_json_mode_stays_clean(monkeypatch, capsys):
    """--json must remain machine-parseable (empty list), no guidance prose mixed in."""
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: [])
    rc = _cli.fetch_main(["10.1038/x", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []
