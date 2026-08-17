"""A short-run finding has to say WHERE it is, not only what it is called.

These findings carried row labels and no row indices. Where a table's rows are unnamed
-- routine in a matrix export -- nothing downstream could place the finding: a location
matcher could not bind it to a panel, a reviewer opening the report could not find the
cells, and a measurement over the corpus could not locate 82% of them. The row index is
already in hand when the finding is built; it was simply not recorded.
"""
from __future__ import annotations

from paperconan._audit import detect_short_row_reuse
from paperconan._sheet import Sheet


_RUN = [47.8213, 3.6621, 128.4407, 19.0558, 86.2934, 7.7145]
_RUN_AT = (1, 6)          # the two sheet rows _RUN is written to, in order


def _sheet_with_a_repeated_row() -> Sheet:
    """Two blocks separated by a blank row, the second repeating a row of the first.

    Two things the fixture has to get right or it exercises nothing: the separator,
    because the short-run path pairs candidates across blocks; and irregular values,
    because a row that reads as an arithmetic progression is filtered out as patterned
    before any pairing happens.
    """
    rows = [["", "c1", "c2", "c3", "c4", "c5", "c6"]]
    rows.append([None, *_RUN])
    rows.append([None, 11.4372, 92.8815, 5.2043, 73.1926, 28.6604, 140.3357])
    rows.append([None, 99.8877, 8.7766, 177.6655, 36.5544, 55.4433, 4.3322])
    rows.append([None] * 7)
    rows.append([None, 12.9876, 64.8765, 3.7654, 148.6543, 21.5432, 93.4321])
    rows.append([None, *_RUN])
    rows.append([None, 71.2468, 2.3579, 113.4680, 14.5791, 45.6802, 6.7913])
    return Sheet.from_rows(rows)


def _short_run_findings():
    grid = {("book.xlsx", "Sheet1"): _sheet_with_a_repeated_row()}
    return [f for f in detect_short_row_reuse(grid) if f.get("short_run")]


def test_a_short_run_finding_carries_both_row_indices() -> None:
    findings = _short_run_findings()

    assert findings, "fixture produced no short-run finding"
    for finding in findings:
        assert isinstance(finding.get("row_a_idx"), int), finding
        assert isinstance(finding.get("row_b_idx"), int), finding


def test_the_indices_point_at_the_rows_that_actually_repeat() -> None:
    """Pinned against the fixture's own coordinates, not against each other.

    Reading both indices and comparing the two rows is `x == y` only when the indices
    differ: given `row_b_idx = row_a_idx` -- the copy-paste this commit exists to
    prevent -- it degenerates into comparing a row with itself and holds for any index
    at all, including one pointing at the header.
    """
    sheet = _sheet_with_a_repeated_row()
    finding = _short_run_findings()[0]
    first, second = finding["row_a_idx"], finding["row_b_idx"]

    assert (first, second) == _RUN_AT
    assert [sheet.cell(first, c) for c in range(1, sheet.ncols)] == _RUN
    assert [sheet.cell(second, c) for c in range(1, sheet.ncols)] == _RUN


def test_indices_are_reported_even_when_the_rows_are_unnamed() -> None:
    """The case that motivated this: labels absent, so labels cannot locate anything."""
    findings = _short_run_findings()

    assert findings, "fixture produced no short-run finding"
    assert all(f.get("all_rows_unnamed") for f in findings)
    # The point of the case: with no label to fall back on, the indices are the only
    # thing that can place the finding, so they have to be the real coordinates.
    assert {(f["row_a_idx"], f["row_b_idx"]) for f in findings} == {_RUN_AT}
