from paperconan._sheet import Sheet
from paperconan._audit import _block_evidence


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
