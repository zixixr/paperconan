"""Dedup removes a repeat, and only a repeat.

The whole-sheet block re-reports what its sub-blocks already found, so one fact
reaches the reader as two ranked rows. Removing that repeat is easy to get wrong
in a way that is invisible: for every column-pair kind the `rule` names only
column indices, so two disjoint row-groups relating the same two columns carry
the same rule text while being different facts about different cells. Keying on
the rule alone deletes the second of them.
"""
import pytest

from paperconan._audit import _dedup_overlapping_blocks


def _block(rows, *findings, cols="1-9"):
    return {"block": {"rows": rows, "cols": cols},
            "relations": [dict(kind="identical_column", rule=r, n=n) for r, n in findings]}


def _extent(block):
    """The 0-based half-open extent matching a block's displayed 1-based range."""
    rows = (block.get("block") or {}).get("rows")
    cols = (block.get("block") or {}).get("cols")
    try:
        r0, r1 = rows.split("-")
        c0, c1 = cols.split("-")
        return int(r0) - 1, int(r1), int(c0) - 1, int(c1)
    except (ValueError, AttributeError):
        return None


def test_a_containing_block_loses_the_repeat():
    blocks = [_block("1-5", ("col[1] == col[0]", 5)),
              _block("1-11", ("col[1] == col[0]", 10))]
    _dedup_overlapping_blocks(blocks, 0, _extent(blocks[-1]))
    assert len(blocks[0]["relations"]) == 1, "the narrower block keeps its finding"
    assert blocks[1]["relations"] == [], "the containing block drops the repeat"


def test_two_disjoint_groups_relating_the_same_columns_both_survive():
    """The case a rule-only key deletes: same rule text, different cells."""
    blocks = [_block("1-5", ("col[1] == col[0]", 5)),
              _block("7-11", ("col[1] == col[0]", 5)),
              _block("1-11", ("col[1] == col[0]", 10))]
    _dedup_overlapping_blocks(blocks, 0, _extent(blocks[-1]))
    assert len(blocks[0]["relations"]) == 1
    assert len(blocks[1]["relations"]) == 1, "a disjoint group is not a duplicate"
    assert blocks[2]["relations"] == [], "the block containing both still drops its repeat"


def test_a_finding_only_the_wider_block_sees_is_kept():
    """The point of the wider block: a relation ACROSS sub-panels that no
    sub-block could have found. Nothing may remove it."""
    blocks = [_block("1-5", ("col[1] == col[0]", 5)),
              _block("1-11", ("col[9] == col[2]", 10))]
    _dedup_overlapping_blocks(blocks, 0, _extent(blocks[-1]))
    assert len(blocks[1]["relations"]) == 1, "a cross-panel relation is not a repeat"


def test_a_finding_without_a_rule_is_never_dropped():
    blocks = [{"block": {"rows": "1-5"}, "relations": [{"kind": "x", "rule": None}]},
              {"block": {"rows": "1-11"}, "relations": [{"kind": "x", "rule": None}]}]
    _dedup_overlapping_blocks(blocks, 0, _extent(blocks[-1]))
    assert len(blocks[1]["relations"]) == 1, "no rule means nothing to compare"


@pytest.mark.parametrize("rows", ["", "not-rows", None])
def test_an_unparseable_row_extent_is_left_alone(rows):
    blocks = [_block("1-5", ("col[1] == col[0]", 5)),
              {"block": {"rows": rows}, "relations": [dict(kind="identical_column",
                                                           rule="col[1] == col[0]", n=9)]}]
    _dedup_overlapping_blocks(blocks, 0, _extent(blocks[-1]))
    assert len(blocks[1]["relations"]) == 1, "an extent that cannot be read is not a container"


def test_only_the_extent_that_was_appended_is_treated_as_a_container():
    """Two ordinary sub-blocks may nest, and nesting is not re-reporting.

    `find_numeric_blocks` can emit overlapping sub-blocks, and several kinds carry
    no location in their rule at all -- `identical_after_rounding` says only "N
    cells share rounded value V", which two unrelated regions produce alike. So the
    container cannot be inferred from the blocks; it is the extent the caller
    appended, passed in.
    """
    iar = {"kind": "identical_after_rounding",
           "rule": "4 cells share rounded value 2.7 but have 4 distinct precise values"}
    blocks = [{"block": {"rows": "6-11", "cols": "1-9"}, "relations": [dict(iar)]},
              {"block": {"rows": "5-11", "cols": "1-9"}, "relations": [dict(iar)]},
              {"block": {"rows": "5-20", "cols": "1-9"}, "relations": [dict(iar)]}]
    removed = _dedup_overlapping_blocks(blocks, 0, (4, 20, 0, 9))
    assert len(blocks[0]["relations"]) == 1, "a nested sub-block keeps its own finding"
    assert len(blocks[1]["relations"]) == 1, "so does the sub-block nesting it"
    assert blocks[2]["relations"] == [], "only the appended extent drops the repeat"
    assert removed == 1, "the caller is told how much budget to give back"


def test_nothing_is_dropped_when_the_sheet_was_not_analysed_whole():
    """A sheet over the size limits gets no whole-sheet block, so nothing here is a
    re-report -- even if one ordinary block happens to span every row. Inferring the
    container from the blocks instead deleted real findings on exactly those sheets.
    """
    iar = {"kind": "identical_after_rounding",
           "rule": "4 cells share rounded value 2.7 but have 4 distinct precise values"}
    blocks = [{"block": {"rows": "11-20", "cols": "1-3"}, "relations": [dict(iar)]},
              {"block": {"rows": "1-350", "cols": "6-7"}, "relations": [dict(iar)]}]
    removed = _dedup_overlapping_blocks(blocks, 0, None)
    assert len(blocks[0]["relations"]) == 1
    assert len(blocks[1]["relations"]) == 1, "an ordinary wide block is not the appended extent"
    assert removed == 0


def _panel_sheet():
    """Two sub-panels of one figure, side by side, separated by a blank column --
    the layout `find_numeric_blocks` cuts at and the reason this second pass
    exists. The right panel repeats the left one's second column exactly."""
    left = [[1.5, 2.5], [3.25, 4.75], [5.125, 6.875], [7.5, 8.25]]
    return [[a, b, None, a * 10, b] for a, b in left]


def test_a_repeat_across_two_panels_is_found():
    """The point of the whole-sheet pass. Without it the two panels are separate
    blocks and no detector ever compares a column of one against the other."""
    from paperconan._audit import (Sheet, detect_relations, find_numeric_blocks,
                                   header_for)
    sheet = Sheet.from_rows(_panel_sheet())
    found = [f for b in find_numeric_blocks(sheet)
             for f in detect_relations(sheet, *b, header_for(sheet, b[0], b[2], b[3]))
             if f["kind"] == "identical_column"]
    assert found, "the repeat between the two panels is not reported"
    assert any(f["rule"] == "col[4] == col[1]" for f in found), \
        f"reported something else: {[f['rule'] for f in found]}"


def test_the_repeat_is_reported_once_not_twice():
    """A count assertion on purpose. The existing suite checks only that a kind is
    present, which cannot see a finding arriving twice -- and the whole-sheet block
    re-reports everything its sub-blocks found."""
    from paperconan._audit import (Sheet, _whole_sheet_extent, detect_relations,
                                   find_numeric_blocks, header_for)
    sheet = Sheet.from_rows(_panel_sheet())
    blocks = find_numeric_blocks(sheet)
    report = []
    for b in blocks:
        rel = [f for f in detect_relations(sheet, *b, header_for(sheet, b[0], b[2], b[3]))]
        report.append({"block": {"rows": f"{b[0]+1}-{b[1]}",
                                 "cols": f"{b[2]+1}-{b[3]}"}, "relations": rel})
    _dedup_overlapping_blocks(report, 0, _whole_sheet_extent(sheet))
    rules = [f["rule"] for blk in report for f in blk["relations"]
             if f["kind"] == "identical_column"]
    assert rules.count("col[4] == col[1]") == 1, f"reported {rules.count('col[4] == col[1]')} times"
