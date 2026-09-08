"""Every sheet the scan read, listed -- including the ones carrying nothing.

`overview` shows where there is signal, so a sheet with no finding is invisible
there. Working from a claim about a particular figure, that invisibility is the
problem: it makes "we read this and found nothing" indistinguishable from "we
never had the data". Matching a figure to a sheet name is left to the reader; this
view only lays out what was read.
"""
import json

from paperconan._drill import sheets
from paperconan._drill_render import render_sheets


def _scan():
    return {
        "scan_stats": {"sheets": [
            {"file": "a.xlsx", "sheet": "Fig. 2j", "n_rows": 8, "n_cols": 4,
             "numeric_cells": 30, "n_blocks": 1},
            {"file": "a.xlsx", "sheet": "Fig. 2k", "n_rows": 5, "n_cols": 3,
             "numeric_cells": 12, "n_blocks": 1},
            {"file": "b.xlsx", "sheet": "ED_Fig.7b", "n_rows": 40, "n_cols": 9,
             "numeric_cells": 300, "n_blocks": 2},
        ]},
        # A real finding, not just the key: `has_findings` asks whether the sheet
        # carries signal, which an empty block entry does not answer.
        "relations_blocks": [{
            "file": "a.xlsx", "sheet": "Fig. 2j",
            "block": {"rows": "1-8", "cols": "1-4", "header": []},
            "relations": [{"kind": "identical_column", "rule": "col[1] == col[0]",
                           "severity": "high", "raw_severity": "high", "n": 8}],
        }],
    }


def test_a_sheet_with_no_finding_is_still_listed():
    view = sheets(_scan())
    named = {r["sheet"]: r for r in view["sheets"]}
    assert set(named) == {"Fig. 2j", "Fig. 2k", "ED_Fig.7b"}
    assert named["Fig. 2k"]["has_findings"] is False, "the silent sheet is the point"
    assert named["Fig. 2j"]["has_findings"] is True


def test_counts_separate_read_from_reported():
    view = sheets(_scan())
    assert view["n_sheets"] == 3
    assert view["n_files"] == 2
    assert view["n_with_findings"] == 1


def test_a_sheet_name_repeated_across_files_stays_distinct():
    """Two files may both hold a sheet called "Fig. 1"; they are different sheets,
    and only one of them may carry a finding."""
    scan = _scan()
    scan["scan_stats"]["sheets"].append(
        {"file": "b.xlsx", "sheet": "Fig. 2j", "n_rows": 3, "n_cols": 2,
         "numeric_cells": 6, "n_blocks": 1})
    # b.xlsx's sheet of the same name carries nothing; a.xlsx's does.
    view = sheets(scan)
    same = [r for r in view["sheets"] if r["sheet"] == "Fig. 2j"]
    assert len(same) == 2
    assert {r["file"]: r["has_findings"] for r in same} == {"a.xlsx": True, "b.xlsx": False}


def test_an_empty_scan_renders_without_failing():
    view = sheets({})
    assert view["n_sheets"] == 0
    render_sheets(view)


def test_the_text_names_every_sheet_and_says_matching_is_the_reader_s():
    text = render_sheets(sheets(_scan()))
    for name in ("Fig. 2j", "Fig. 2k", "ED_Fig.7b"):
        assert name in text
    assert "judgement" in text, "the view must not imply it matched anything itself"


def test_a_sheet_whose_only_finding_is_cross_sheet_is_not_shown_as_silent():
    """`relations_blocks` holds only the per-block families. A sheet whose sole
    finding is a cross-sheet duplicate -- among the most serious reported -- read as
    carrying nothing, which is worse than the gap this view exists to close."""
    scan = {
        "scan_stats": {"sheets": [
            {"file": "a.xlsx", "sheet": "Fig. 1", "n_rows": 9, "n_cols": 4,
             "numeric_cells": 30, "n_blocks": 1}]},
        "relations_blocks": [],
        "cross_sheet_findings": [{
            "kind": "cross_sheet_position_identical", "severity": "high",
            "raw_severity": "high", "file": "a.xlsx + b.xlsx",
            "file_a": "a.xlsx", "file_b": "b.xlsx",
            "sheet_a": "Fig. 1", "sheet_b": "Fig. 9",
            "rule": "38/40 values identical at the same position", "n": 38}],
    }
    named = {r["sheet"]: r for r in sheets(scan)["sheets"]}
    assert named["Fig. 1"]["has_findings"] is True


def test_a_file_that_yielded_no_sheet_is_named():
    """An unreadable or oversized file contributes no sheet at all. Listing what
    was read without saying that lets "we could not read it" read as "not there"."""
    scan = {"scan_stats": {
        "sheets": [{"file": "a.xlsx", "sheet": "Fig. 1", "n_rows": 3, "n_cols": 2,
                    "numeric_cells": 6, "n_blocks": 1}],
        "files": [{"file": "a.xlsx"}, {"file": "huge.xlsx"}]}}
    view = sheets(scan)
    assert view["files_with_no_sheet_read"] == ["huge.xlsx"]
    assert "huge.xlsx" in render_sheets(view)


def test_a_sheet_past_the_size_cap_is_not_rendered_as_read_and_clean():
    scan = {"scan_stats": {"sheets": [
        {"file": "big.xlsx", "sheet": "Fig. 2j", "oversized": True},
        {"file": "big.xlsx", "sheet": "Fig. 2k", "n_rows": 10, "n_cols": 3,
         "numeric_cells": 20, "n_blocks": 1}]}}
    view = sheets(scan)
    named = {r["sheet"]: r for r in view["sheets"]}
    assert named["Fig. 2j"]["oversized"] is True
    assert named["Fig. 2j"]["has_findings"] is False
    text = render_sheets(view)
    assert "not read" in text, "an unread sheet must not look like a clean one"


def test_an_incomplete_scan_says_so():
    scan = {"scan_stats": {"sheets": [{"file": "a.xlsx", "sheet": "S", "n_rows": 2,
                                       "n_cols": 2, "numeric_cells": 4, "n_blocks": 1}]},
            "scan_status": "partial"}
    assert "partial" in render_sheets(sheets(scan))


def test_the_text_does_not_let_a_blank_signal_column_read_as_clean():
    """The families this layer routes are not all of them. A sheet whose only
    finding is an FDR-significant digit distribution shows a blank signal column,
    and the reader has to be told that is not a clean bill of health -- this view
    exists to stop a false all-clear, and would otherwise licence one of its own.
    """
    scan = {"scan_stats": {"sheets": [{"file": "a.xlsx", "sheet": "Fig. 2j", "n_rows": 8,
                                       "n_cols": 4, "numeric_cells": 30, "n_blocks": 1}]},
            "digit_distribution": [{"label": "a.xlsx :: Fig. 2j", "p_adj": 0.002,
                                    "fdr_significant": True, "n": 30}]}
    view = sheets(scan)
    assert view["sheets"][0]["has_findings"] is False, "the routed families found nothing"
    text = render_sheets(view).lower()
    # The whole caution, not a fragment of it: a reader has to learn both that a
    # blank column is not a clean bill of health AND which families are missing,
    # or the sentence does not do its job.
    assert "blank signal column" in text
    assert "not that the sheet is clean" in text
    for family in ("digit", "decimal", "image"):
        assert family in text, f"{family} findings are unrouted and must be named"
