from paperconan._sheet import Sheet
from paperconan._audit import _block_evidence, scan_dir


def _grid(nr, nc):
    return Sheet.from_rows([[float(r * 1000 + c) for c in range(nc)] for r in range(nr)])


def test_small_block_untruncated():
    s = _grid(8, 5)
    ev = _block_evidence(s, 0, 8, 0, 5, ["a", "b", "c", "d", "e"], [1])
    assert "truncated" not in ev
    assert len(ev["rows"]) >= 8 and all(len(r["values"]) == 5 for r in ev["rows"])


def test_big_block_truncated_keeps_highlight():
    from paperconan._audit import _MAX_EV_COLS, _MAX_EV_ROWS

    s = _grid(300, 200)
    hi = [150, 151]
    ev = _block_evidence(s, 0, 300, 0, 200, [f"h{c}" for c in range(200)], hi)
    assert len(ev["rows"]) <= _MAX_EV_ROWS + 2        # window (+1 ctx each side)
    assert all(len(r["values"]) <= _MAX_EV_COLS for r in ev["rows"])
    # the highlighted columns are within the emitted window
    assert ev["col_offset"] <= 150 and ev["col_offset"] + len(ev["headers"]) > 151
    assert len(ev["headers"]) == len(ev["rows"][0]["values"])


def test_a_trimmed_evidence_window_says_how_much_it_left_out():
    """`truncated: True` cannot separate 12-of-13 from 12-of-5000.

    The window is what a reader checks against the paper. Told only that it was
    trimmed, they cannot tell whether they are looking at essentially the whole
    block or at a fifth of a percent of it -- and a window that does not state
    its own scale reads as the whole block.
    """
    ev = _block_evidence(_grid(300, 200), 0, 300, 0, 200,
                         [f"h{c}" for c in range(200)], [150, 151])

    cut = ev["truncated"]
    assert cut["rows_total"] == 300 and cut["cols_total"] == 200, cut
    assert cut["rows_shown"] == len(ev["rows"]), cut
    assert cut["cols_shown"] == len(ev["headers"]), cut
    assert cut["rows_shown"] < cut["rows_total"]


def test_an_untrimmed_window_claims_no_truncation():
    """The other direction: a whole block must not look trimmed."""
    ev = _block_evidence(_grid(6, 4), 0, 6, 0, 4, list("abcd"), [1])
    assert "truncated" not in ev


def test_write_json_false_skips_file(tmp_path):
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from build_fixture import build as build_tiny
    ind = tmp_path / "in"; out = tmp_path / "out"; ind.mkdir()
    build_tiny(str(ind))
    res = scan_dir(str(ind), str(out), write_md=False, write_html=False, write_json=False)
    assert res is not None and "relations_blocks" in res
    assert not (out / "scan.json").exists()
    # default writes it
    out2 = tmp_path / "out2"
    scan_dir(str(ind), str(out2), write_md=False, write_html=False)
    assert (out2 / "scan.json").exists()


# ---------- reading the trimmed window back from source ----------

def _panel(path, rows=80, cols=40):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ",".join(f"c{j}" for j in range(cols))
    lines = [header]
    for i in range(rows):
        vals = [round((i + 1) * (j + 1) * 1.017, 6) for j in range(cols)]
        vals[5] = vals[2]
        lines.append(",".join(str(v) for v in vals))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _a_trimmed_finding(scan):
    from paperconan._drill import drill, explain, overview

    for loc in overview(scan, max_locations=50)["locations"]:
        for group in drill(scan, loc["n"])["by_kind"]:
            for f in drill(scan, loc["n"], kind=group["kind"])["findings"]:
                ev = explain(scan, f["finding_id"]).get("evidence") or {}
                if isinstance(ev.get("truncated"), dict):
                    return f["finding_id"], ev["truncated"]
    return None, None


def test_explain_full_reads_the_whole_window_back_from_the_source(tmp_path):
    """A bounded stored window must not be the only way to see the cells.

    Storing 12 rows instead of 50 is most of a scan's bytes, but it is only
    acceptable because the block can be read again. Without that, trimming turns
    a size saving into a permanent loss of the evidence a reader checks against
    the paper.
    """
    from paperconan._drill import explain

    _panel(tmp_path / "d" / "p.csv")
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    fid, cut = _a_trimmed_finding(scan)
    assert fid, "fixture no longer produces a trimmed evidence window"
    assert cut["rows_shown"] < cut["rows_total"]

    full = explain(scan, fid, full=True)["evidence"]
    assert "unavailable" not in full, full
    assert len(full["rows"]) >= cut["rows_total"], (
        f"--full returned {len(full['rows'])} of {cut['rows_total']} rows"
    )
    assert len(full["rows"][0]["values"]) >= cut["cols_total"], (
        f"--full returned {len(full['rows'][0]['values'])} of {cut['cols_total']} "
        f"columns of values"
    )
    # Headers separately: the value width is set by the window, so a header row
    # carried over from the trimmed copy leaves a full-width table whose columns
    # are mostly unlabelled -- which is what reusing the stored headers did.
    assert len(full["headers"]) == len(full["rows"][0]["values"]), (
        f"{len(full['headers'])} headers for "
        f"{len(full['rows'][0]['values'])} columns; the header row was reused "
        f"from the trimmed window instead of read from the sheet"
    )
    assert "truncated" not in full, "a full window must not report itself trimmed"


def test_explain_full_says_why_when_the_source_is_gone(tmp_path):
    """Silence here would hand back the trimmed window as if it were everything.

    A reader who asks for the whole block and receives a subset with no notice
    takes the subset for the block -- the same failure as a scan that reports
    itself complete after stopping early.
    """
    from paperconan._drill import explain

    _panel(tmp_path / "d" / "p.csv")
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)
    fid, _cut = _a_trimmed_finding(scan)
    assert fid

    (tmp_path / "d" / "p.csv").unlink()
    full = explain(scan, fid, full=True)["evidence"]

    assert "unavailable" in full, (
        f"--full returned a window with the source deleted: {list(full)}"
    )
    assert "rows" not in full, "a partial window must not ride alongside the reason"


def test_explain_full_refuses_when_the_source_no_longer_matches(tmp_path):
    """Wrong cells are worse than missing ones.

    The scan keeps no hash or mtime for its inputs, so a source edited or
    replaced since the scan re-reads clean and is presented as this finding's
    block -- a reader checking values against the paper would be comparing a
    different table. The stored window records the block's true extent, so a
    shape that no longer matches is proof the source moved on.
    """
    from paperconan._drill import explain

    _panel(tmp_path / "d" / "p.csv", rows=80, cols=40)
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)
    fid, cut = _a_trimmed_finding(scan)
    assert fid and cut["rows_total"] > 3

    _panel(tmp_path / "d" / "p.csv", rows=3, cols=40)      # same name, different data
    full = explain(scan, fid, full=True)["evidence"]

    assert "unavailable" in full, (
        f"--full returned a window from a source that no longer matches: {list(full)}"
    )
    assert "rows" not in full


def test_explain_full_stays_inside_a_finite_cell_budget(tmp_path, monkeypatch):
    """--full lifts the stored bound; it does not remove it.

    CLAUDE.md requires new code paths to respect the memory caps -- a genomics
    block is millions of cells, and materialising one as JSON is the OOM the
    caps exist to prevent.
    """
    import paperconan._audit as audit
    from paperconan._drill import explain

    monkeypatch.setattr(audit, "_FULL_EV_CELLS", 200)

    _panel(tmp_path / "d" / "p.csv", rows=80, cols=40)
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)
    fid, cut = _a_trimmed_finding(scan)
    assert fid

    full = explain(scan, fid, full=True)["evidence"]
    assert "unavailable" not in full, full
    cells = len(full["rows"]) * len(full["rows"][0]["values"])
    assert cells <= 200 * 2, f"--full materialised {cells} cells against a 200 budget"
    assert full.get("truncated"), "a budget-bounded window must still say it is one"


def test_the_html_evidence_table_discloses_a_trimmed_window():
    """The adjudicated report is the deliverable, and it renders this table.

    A trimmed window with no notice reads as the whole block to whoever the
    report reaches -- who may not be the person who ran the scan.
    """
    from paperconan._html import _render_evidence_table

    ev = {"headers": ["a", "b"], "col_offset": 0, "highlight_cols": [0],
          "highlight_rows": [], "rows": [{"row_idx": 1, "is_context": False,
                                          "values": [1.0, 2.0]}],
          "truncated": {"rows_shown": 20, "rows_total": 300,
                        "cols_shown": 30, "cols_total": 200}}

    html = _render_evidence_table(ev)
    assert "300" in html and "200" in html, f"the block's true size is absent: {html}"

    whole = dict(ev)
    whole.pop("truncated")
    assert "<caption" not in _render_evidence_table(whole), (
        "an untrimmed table must not claim to be a window"
    )


def test_the_text_view_does_not_re_trim_a_full_window(tmp_path):
    """--full reads the block, then the page cap would show one page of it.

    SKILL.md tells an agent to use --full when judging whether a pattern runs
    the whole column. A renderer that caps at a page silently undoes the
    re-read the agent just paid for.
    """
    from paperconan._drill import explain
    from paperconan._drill_render import render_explain

    _panel(tmp_path / "d" / "p.csv", rows=80, cols=40)
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)
    fid, cut = _a_trimmed_finding(scan)
    assert fid and cut["rows_total"] > 25

    text = render_explain(explain(scan, fid, full=True))
    body = [ln for ln in text.splitlines() if "│" in ln]
    assert len(body) > 25, (
        f"--full rendered {len(body)} rows of a {cut['rows_total']}-row block"
    )
