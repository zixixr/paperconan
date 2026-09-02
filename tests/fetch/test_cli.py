import json
from pathlib import Path

from paperconan.fetch import _cli, _download, _http


def _minimal_xlsx_bytes():
    import io
    from openpyxl import Workbook

    payload = io.BytesIO()
    workbook = Workbook()
    workbook.active["A1"] = "synthetic"
    workbook.save(payload)
    workbook.close()
    return payload.getvalue()



def _stub_search(cands, **resolution):
    """Stand in for `search_all` exactly as `_cli` calls it -- resolution BESIDE the list.

    A stub returning a bare list raises TypeError against the keyword rather than quietly
    handing back something the CLI then misreads, which is the point: the defect this
    replaces was resolution carried ON the candidates, invisible whenever there were none.
    """
    base = {"query_doi": None, "resolved_doi": None, "followed_a_notice": False,
            "ambiguous_notice_doi": None, "notice_names_several_articles": [],
            "title": None}
    base.update(resolution)

    def _search(query, per_source=5, *, with_resolution=False):
        return (list(cands), dict(base)) if with_resolution else list(cands)

    return _search


def test_fetch_list_prints_candidates_json(monkeypatch, capsys):
    cands = [{"cand_id": "zenodo:1", "source": "zenodo", "doi": "10.x/z",
              "title": "T", "tabular_files": [{"name": "a.xlsx"}],
              "all_files_count": 1, "match_signals": {"doi_in_related": True,
              "title_overlap": None, "author_overlap": None}}]
    monkeypatch.setattr(_cli, "search_all", _stub_search(cands))
    rc = _cli.fetch_main(["10.15761/JTS.1000455", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["cand_id"] == "zenodo:1"


def test_fetch_download_selected_candidate(monkeypatch, tmp_path):
    cands = [{"cand_id": "zenodo:1", "source": "zenodo", "doi": "10.x/z", "title": "T",
              "tabular_files": [{"name": "a.csv", "ext": "csv", "size": 3,
              "download_url": "u"}], "all_files_count": 1,
              "match_signals": {"doi_in_related": True}}]
    monkeypatch.setattr(_cli, "search_all", _stub_search(cands))
    captured = {}
    monkeypatch.setattr(_cli, "download_candidate",
                        lambda c, out_dir, **kw: captured.update(cid=c["cand_id"], out=out_dir)
                        or {"downloaded": [out_dir + "/a.csv"], "skipped": []})
    rc = _cli.fetch_main(["10.x/paper", "--download", "zenodo:1", "--out", str(tmp_path)])
    assert rc == 0
    assert captured["cid"] == "zenodo:1"


def test_fetch_download_missing_candidate_returns_2(monkeypatch):
    monkeypatch.setattr(_cli, "search_all", _stub_search([]))
    rc = _cli.fetch_main(["10.x/paper", "--download", "zenodo:999"])
    assert rc == 2


def test_fetch_auto_empty_returns_1(monkeypatch):
    monkeypatch.setattr(_cli, "search_all", _stub_search([]))
    rc = _cli.fetch_main(["10.x/paper", "--auto", "--out", "/tmp/pc_auto_empty"])
    assert rc == 1


def test_fetch_auto_downloads_top_candidate(monkeypatch, tmp_path):
    cands = [{"cand_id": "zenodo:1", "source": "zenodo", "title": "T",
              "all_files_count": 1, "match_signals": {"doi_in_related": True},
              "tabular_files": [{"name": "a.csv", "ext": "csv", "size": 3,
              "download_url": "u"}]}]
    monkeypatch.setattr(_cli, "search_all", _stub_search(cands))
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
    monkeypatch.setattr(_cli, "search_all", _stub_search(cands))
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
    monkeypatch.setattr(_cli, "search_all", _stub_search(cands))
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
    monkeypatch.setattr(_cli, "search_all", _stub_search(cands))
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
    monkeypatch.setattr(_cli, "search_all", _stub_search(cands))
    rc = _cli.fetch_main(["10.x/paper"])
    assert rc == 0
    assert "no DOI/title match" in capsys.readouterr().out


def test_fetch_download_and_auto_mutually_exclusive():
    import pytest
    with pytest.raises(SystemExit):
        _cli.fetch_main(["10.x/paper", "--download", "zenodo:1", "--auto"])


def test_fetch_empty_prints_journal_guidance(monkeypatch, capsys):
    """No open-repo hit on a Nature DOI: point the user to the article's Source Data
    section instead of leaving them with a dead end."""
    monkeypatch.setattr(_cli, "search_all", _stub_search([]))
    rc = _cli.fetch_main(["10.1038/s41590-026-02471-0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "doi.org/10.1038/s41590-026-02471-0" in out
    assert "Source data" in out


def test_fetch_empty_json_mode_stays_clean(monkeypatch, capsys):
    """--json must remain machine-parseable (empty list), no guidance prose mixed in."""
    monkeypatch.setattr(_cli, "search_all", _stub_search([]))
    rc = _cli.fetch_main(["10.1038/x", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_fetch_images_passes_additive_option(monkeypatch, tmp_path):
    cands = [{
        "cand_id": "source:1",
        "source": "source",
        "title": "T",
        "all_files_count": 2,
        "match_signals": {"doi_in_related": True},
        "tabular_files": [{"name": "data.csv"}],
        "image_files": [{"name": "Fig1.png"}],
    }]
    monkeypatch.setattr(_cli, "search_all", _stub_search(cands))
    captured = {}

    def stub_download(candidate, out_dir, **kwargs):
        captured.update(kwargs)
        return {"downloaded": [str(tmp_path / "Fig1.png")], "skipped": []}

    monkeypatch.setattr(_cli, "download_candidate", stub_download)
    rc = _cli.fetch_main([
        "10.x/paper", "--auto", "--images", "--out", str(tmp_path),
    ])
    assert rc == 0
    assert captured["include_images"] is True


def test_fetch_auto_uses_jci_fallback_after_archive_failure(
    monkeypatch,
    tmp_path,
    capsys,
):
    candidate = {
        "cand_id": "europepmc:PMC1",
        "source": "europepmc",
        "doi": "10.1172/JCI123456",
        "title": "Synthetic JCI paper",
        "all_files_count": 1,
        "match_signals": {"doi_in_related": True},
        "tabular_files": [],
        "supplementary_archive": {
            "url": "https://example.test/supplementaryFiles",
            "name": "PMC1_supplementary.zip",
        },
    }
    monkeypatch.setattr(_cli, "search_all", _stub_search([candidate]))
    monkeypatch.setattr(
        _http,
        "get_text",
        lambda url, **kwargs: (
            '<a href="https://cdn.example.test/supporting/table.xlsx">'
            "source data</a>"
        ),
    )
    xlsx_bytes = _minimal_xlsx_bytes()

    def stub_download(url, destination, **kwargs):
        if url.endswith("/supplementaryFiles"):
            return {
                "ok": False,
                "path": str(destination),
                "skipped_reason": "HTTP 404: Not Found",
            }
        Path(destination).write_bytes(xlsx_bytes)
        return {
            "ok": True,
            "path": str(destination),
            "size": len(xlsx_bytes),
            "content_type": (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            "source_url": url,
        }

    monkeypatch.setattr(_download, "download_file", stub_download)

    rc = _cli.fetch_main([
        "10.1172/JCI123456",
        "--auto",
        "--out",
        str(tmp_path),
    ])

    assert rc == 0
    assert "downloaded 1 file(s)" in capsys.readouterr().out
    assert (tmp_path / "table.xlsx").exists()


def test_fetch_zero_candidates_still_points_at_the_resolved_article(monkeypatch, capsys):
    """The empty search IS the notice case, so it is the one the guidance must get right.

    A notice has no supplementary files of its own; searching it finds nothing. That is not
    an edge case to tidy up later, it is the ordinary outcome -- and it is where the
    substitution used to be thrown away, sending the reader back to the one-page notice
    they had just been moved off.
    """
    monkeypatch.setattr(_cli, "search_all", _stub_search(
        [], query_doi="10.1038/notice-9", resolved_doi="10.1038/orig-1",
        followed_a_notice=True))

    rc = _cli.fetch_main(["10.1038/notice-9"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "10.1038/orig-1" in out
    assert "names a retraction or correction" in out
    # The guidance link must be the article's, not the notice's.
    assert "https://doi.org/10.1038/orig-1" in out


def test_fetch_zero_candidates_still_lists_a_multi_article_notice(monkeypatch, capsys):
    """Naming the articles is the entire remedy offered when none can be followed."""
    monkeypatch.setattr(_cli, "search_all", _stub_search(
        [], query_doi="10.1038/bulk", resolved_doi="10.1038/bulk",
        ambiguous_notice_doi="10.1038/bulk",
        notice_names_several_articles=["10.1038/a-1", "10.1038/b-2", "10.1038/c-3"]))

    rc = _cli.fetch_main(["10.1038/bulk"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "naming 3 articles" in out
    for one in ("10.1038/a-1", "10.1038/b-2", "10.1038/c-3"):
        assert one in out


def test_fetch_json_stays_parseable_when_a_notice_resolves(monkeypatch, capsys):
    """`--json` promises machine-readable stdout, and a resolving notice must not break it."""
    cands = [{"cand_id": "zenodo:1", "source": "zenodo", "doi": "10.x/z", "title": "T",
              "tabular_files": [], "all_files_count": 1, "resolved_doi": "10.1038/orig-1",
              "match_signals": {"doi_in_related": True}}]
    monkeypatch.setattr(_cli, "search_all", _stub_search(
        cands, query_doi="10.1038/notice-9", resolved_doi="10.1038/orig-1",
        followed_a_notice=True))

    rc = _cli.fetch_main(["10.1038/notice-9", "--json"])

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    # The substitution is still legible to a program: it rides on the candidates.
    assert parsed[0]["resolved_doi"] == "10.1038/orig-1"


def test_fetch_names_the_notice_that_actually_lists_several(monkeypatch, capsys):
    """After a hop the ambiguity belongs to an intermediate notice, not the typed DOI."""
    monkeypatch.setattr(_cli, "search_all", _stub_search(
        [], query_doi="10.1038/notice-1", resolved_doi="10.1038/notice-2",
        followed_a_notice=True, ambiguous_notice_doi="10.1038/notice-2",
        notice_names_several_articles=["10.1038/a-1", "10.1038/b-2"]))

    _cli.fetch_main(["10.1038/notice-1"])

    out = capsys.readouterr().out
    assert "10.1038/notice-2 is a notice naming 2 articles" in out
    assert "10.1038/notice-1 is a notice naming" not in out
