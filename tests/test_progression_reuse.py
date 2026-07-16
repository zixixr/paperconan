"""M2-2: a perfect arithmetic progression that is REUSED — the identical (step, n, first)
appears in >=2 numeric blocks/sheets — is an independent-variable axis re-plotted across panels
(magnetic-field / 2-theta / time / dose sweep), not a data inconsistency. Real measured data is never
a perfect progression, so a reused one is an axis and must not flood the high-severity output.
A ONE-OFF perfect progression keeps its severity (that is the genuinely-suspicious linear-fill)."""
from paperconan._audit import _demote_reused_progressions, benign_reason


def _prog(step, n, first, sev="high", col_idx=2):
    return {"kind": "arithmetic_progression", "step": step, "n": n, "first": first,
            "severity": sev, "col_idx": col_idx}


def _block(sheet, progs):
    return {"file": "S.xlsx", "sheet": sheet, "progressions": progs}


def test_reused_axis_progression_demoted_out_of_high():
    # the same field-axis progression re-plotted across 3 figures → axis → demoted
    blocks = [_block(f"Fig {i}", [_prog(0.0140281, 500, 0.0)]) for i in range(3)]
    _demote_reused_progressions(blocks)
    for b in blocks:
        f = b["progressions"][0]
        assert f["severity"] == "low", f
        assert f.get("reused_progression") is True
        assert f.get("prefilter") == "drop"
    # and the benign note explains it as an axis re-plot
    assert "axis" in (benign_reason(blocks[0]["progressions"][0]) or "")


def test_one_off_non_integer_progression_keeps_high():
    # a single non-integer progression (possible linear-fill inconsistency) stays HIGH
    blocks = [_block("Fig 1", [_prog(2.5, 6, 2.5, sev="high")])]
    _demote_reused_progressions(blocks)
    f = blocks[0]["progressions"][0]
    assert f["severity"] == "high"
    assert not f.get("reused_progression")


def test_different_progressions_are_not_reuse():
    # two DISTINCT progressions (different step) are not a reused axis — both keep severity
    blocks = [_block("A", [_prog(2.5, 6, 2.5)]), _block("B", [_prog(3.5, 6, 2.5)])]
    _demote_reused_progressions(blocks)
    assert all(b["progressions"][0]["severity"] == "high" for b in blocks)


def test_reuse_needs_matching_first_value_too():
    # same step+n but DIFFERENT starting value are different series, not a reused axis
    blocks = [_block("A", [_prog(0.1, 500, 100.0)]), _block("B", [_prog(0.1, 500, 0.0)])]
    _demote_reused_progressions(blocks)
    assert all(b["progressions"][0]["severity"] == "high" for b in blocks)
