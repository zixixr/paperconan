"""Numbers a workbook stored as text reach the detectors.

A spreadsheet cell can hold `"21.5"` as text — most often when a panel was pasted
in from elsewhere — and the workbook readers used to keep it in the text side of
the Sheet, where no detector looks. The CSV and PDF/DOCX paths already parsed
such cells, so the SAME table was analysable as .csv and invisible as .xlsx.

The parse is deliberately narrow, and the tests below pin both halves of that:
what must now become a number, and what must still not.
"""
import json
import math

import numpy as np
import pytest

from paperconan._audit import (
    _coerce_cell,
    _fill_sheet_from_rows,
    find_numeric_blocks,
    load_table,
)
from paperconan._sheet import Sheet


def _sheet(rows):
    sheet, _cells = _fill_sheet_from_rows(iter(rows), len(rows),
                                          max((len(r) for r in rows), default=0), 0)
    return sheet


def test_a_number_stored_as_text_becomes_a_number():
    sheet = _sheet([["21.5", "26"], ["24.5", "29"]])
    assert sheet.cell(0, 0) == 21.5
    assert not math.isnan(sheet.numeric[0, 0])
    # "26" is an integer numeral: int/float fidelity has to survive the parse, or
    # evidence output would print 26.0 where the source says 26.
    assert sheet.cell(0, 1) == 26
    assert isinstance(sheet.cell(0, 1), int)


def test_a_panel_of_text_numbers_now_forms_a_block():
    # Text numerals used to leave a column's numeric density at zero, so the
    # panel never entered a block and no detector ever saw it.
    rows = [["t", "a", "b"]] + [[str(i), str(i * 2), str(i * 3)] for i in range(1, 7)]
    assert find_numeric_blocks(_sheet(rows)), "a panel of text numerals forms no block"


def test_text_that_is_not_a_number_stays_text():
    sheet = _sheet([["TP53", "<0.001", "1,000", "50%", "v1.2.3", ""]])
    for c, expected in enumerate(["TP53", "<0.001", "1,000", "50%", "v1.2.3"]):
        assert sheet.cell(0, c) == expected, f"column {c} was parsed and should not be"


@pytest.mark.parametrize("text", ["NaN", "nan", "Infinity", "-Infinity", "inf", "1e999"])
def test_a_non_finite_numeral_keeps_its_text(text):
    """`float()` accepts these, but a Sheet must not hold nan/inf as a VALUE.

    `cell()` promises the source value back, `numeric` uses NaN to mean "no number
    here", and json.dumps would emit a bare NaN/Infinity literal that is not JSON.
    Parsing has to reject what it cannot represent, not pass it through.
    """
    sheet = _sheet([[text]])
    assert sheet.cell(0, 0) == text
    assert math.isnan(sheet.numeric[0, 0]), "a non-finite numeral must not occupy a cell"
    json.dumps({"v": sheet.cell(0, 0)})          # would raise/emit NaN if it were a float


def test_the_csv_path_also_refuses_a_non_finite_numeral(tmp_path):
    """The same guard, at the other end. `_coerce_cell` ran BEFORE the Sheet was
    built here, so a text 'NaN' arrived already converted and was stored as a float
    in the text side -- `cell()` returned nan where the file said "NaN"."""
    p = tmp_path / "t.csv"
    p.write_text("a,b\nNaN,1.5\n")
    sheet = next(iter(load_table(str(p)).values()))
    assert sheet.cell(1, 0) == "NaN"
    assert sheet.cell(1, 1) == 1.5
    json.dumps({"v": sheet.cell(1, 0)})


def test_coerce_cell_itself_never_returns_a_non_finite_float():
    for text in ("NaN", "Infinity", "-inf", "1e999"):
        assert isinstance(_coerce_cell(text), str), f"{text!r} parsed to a non-finite float"


def test_the_two_readers_still_agree_on_text_numerals():
    """`_fill_sheet_from_rows` and `Sheet.from_rows` are separate implementations of
    the same split. The loaders' parity tests compare them, so the parse has to land
    in both or that comparison silently stops meaning anything."""
    rows = [["21.5", "x"], ["NaN", "3"]]
    a, b = _sheet(rows), Sheet.from_rows(rows)
    assert np.array_equal(a.numeric, b.numeric, equal_nan=True)
    assert [a.cell(r, c) for r in range(2) for c in range(2)] == \
           [b.cell(r, c) for r in range(2) for c in range(2)]
