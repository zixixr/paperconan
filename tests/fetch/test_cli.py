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
              "download_url": "u"}], "all_files_count": 1,
              "match_signals": {"doi_in_related": True}}]
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
              "all_files_count": 1, "match_signals": {"doi_in_related": True},
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


def test_fetch_auto_refuses_unmatched_candidate(monkeypatch, capsys):
    """--auto must NOT silently download a candidate that doesn't match the paper
    (figshare full-text search returns unrelated deposits). It should refuse and
    fall back to journal guidance instead of auditing a stranger's data."""
    cands = [{"cand_id": "figshare:999", "source": "figshare", "title": "Unrelated dataset",
              "all_files_count": 147, "match_signals": {"doi_in_related": False,
              "title_overlap": 0.02, "author_overlap": 0.0},
              "tabular_files": [{"name": "x.csv", "ext": "csv", "size": 3, "download_url": "u"}]}]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    called = {"n": 0}
    monkeypatch.setattr(_cli, "download_candidate",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
    rc = _cli.fetch_main(["10.1038/s41467-026-70472-6", "--auto"])
    assert rc == 1
    assert called["n"] == 0                       # never downloaded the unmatched data
    out = capsys.readouterr().out
    assert "doi.org/10.1038/s41467-026-70472-6" in out   # fell back to guidance


def test_fetch_download_unmatched_requires_force(monkeypatch, capsys):
    """--download of a candidate with no DOI/title match must refuse unless --force,
    so a user can't accidentally audit the wrong paper's data."""
    cands = [{"cand_id": "figshare:999", "source": "figshare", "title": "Unrelated",
              "all_files_count": 1, "match_signals": {"doi_in_related": False,
              "title_overlap": 0.02}, "tabular_files": [{"name": "x.csv", "ext": "csv",
              "size": 3, "download_url": "u"}]}]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    called = {"n": 0}
    monkeypatch.setattr(_cli, "download_candidate",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
    rc = _cli.fetch_main(["10.x/paper", "--download", "figshare:999"])
    assert rc == 2
    assert called["n"] == 0
    assert "--force" in capsys.readouterr().err


def test_fetch_download_unmatched_with_force_proceeds(monkeypatch, tmp_path):
    cands = [{"cand_id": "figshare:999", "source": "figshare", "title": "Unrelated",
              "all_files_count": 1, "match_signals": {"doi_in_related": False,
              "title_overlap": 0.02}, "tabular_files": [{"name": "x.csv", "ext": "csv",
              "size": 3, "download_url": "u"}]}]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    captured = {}
    monkeypatch.setattr(_cli, "download_candidate",
                        lambda c, out_dir, **kw: captured.update(cid=c["cand_id"])
                        or {"downloaded": [out_dir + "/x.csv"], "skipped": []})
    rc = _cli.fetch_main(["10.x/paper", "--download", "figshare:999",
                          "--force", "--out", str(tmp_path)])
    assert rc == 0
    assert captured["cid"] == "figshare:999"


def test_fetch_list_flags_unmatched_candidate(monkeypatch, capsys):
    """The plain listing must visibly flag candidates that don't match the paper."""
    cands = [{"cand_id": "figshare:999", "source": "figshare", "title": "Unrelated dataset",
              "all_files_count": 5, "match_signals": {"doi_in_related": False,
              "title_overlap": 0.02, "author_overlap": 0.0},
              "tabular_files": [{"name": "x.csv"}]}]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)
    rc = _cli.fetch_main(["10.x/paper"])
    assert rc == 0
    assert "no DOI/title match" in capsys.readouterr().out


def test_fetch_list_labels_scanner_supported_inputs(monkeypatch, capsys):
    cands = [{
        "cand_id": "zenodo:1",
        "source": "zenodo",
        "title": "Dataset",
        "all_files_count": 2,
        "match_signals": {"doi_in_related": True},
        "tabular_files": [{"name": "supplement.pdf"}],
    }]
    monkeypatch.setattr(_cli, "search_all", lambda q, per_source=5: cands)

    assert _cli.fetch_main(["10.x/paper"]) == 0
    out = capsys.readouterr().out
    assert "inputs=1/2" in out
    assert "tabular=" not in out


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
