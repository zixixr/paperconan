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
        "relations_blocks": [{"file": "a.xlsx", "sheet": "Fig. 2j"}],
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
