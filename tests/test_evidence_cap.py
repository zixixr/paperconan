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
    s = _grid(300, 200)
    hi = [150, 151]
    ev = _block_evidence(s, 0, 300, 0, 200, [f"h{c}" for c in range(200)], hi)
    assert ev.get("truncated") is True
    assert len(ev["rows"]) <= 52                      # <= _MAX_EV_ROWS (+1 ctx each side)
    assert all(len(r["values"]) <= 30 for r in ev["rows"])   # <= _MAX_EV_COLS
    # the highlighted columns are within the emitted window
    assert ev["col_offset"] <= 150 and ev["col_offset"] + len(ev["headers"]) > 151
    assert len(ev["headers"]) == len(ev["rows"][0]["values"])


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
