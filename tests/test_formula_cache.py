from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET

import openpyxl
import pytest

from paperconan._audit import scan_dir
from paperconan._input import inspect_ooxml_formula_cache


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _write_formula_book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stats"
    ws["A1"] = 2
    ws["A2"] = 3
    ws["A3"] = "=SUM(A1:A2)"
    wb.save(path)


def _rewrite_zip_member(path, member_name, transform):
    with zipfile.ZipFile(path) as src:
        members = [
            (info, src.read(info.filename))
            for info in src.infolist()
        ]
    with zipfile.ZipFile(path, "w") as dst:
        for info, data in members:
            if info.filename == member_name:
                data = transform(data)
            dst.writestr(info, data)


def _set_formula_cache(path, value):
    def transform(data):
        xml = ET.fromstring(data)
        cell = xml.find(f".//{{{_MAIN_NS}}}c[@r='A3']")
        assert cell is not None
        cached = cell.find(f"{{{_MAIN_NS}}}v")
        if cached is None:
            cached = ET.SubElement(cell, f"{{{_MAIN_NS}}}v")
        cached.text = value
        return ET.tostring(xml, encoding="utf-8", xml_declaration=True)

    _rewrite_zip_member(path, "xl/worksheets/sheet1.xml", transform)


def _set_first_worksheet_target(path, target):
    def transform(data):
        xml = ET.fromstring(data)
        relationship = xml.find(f"{{{_PKG_REL_NS}}}Relationship")
        assert relationship is not None
        relationship.attrib["Target"] = target
        return ET.tostring(xml, encoding="utf-8", xml_declaration=True)

    _rewrite_zip_member(path, "xl/_rels/workbook.xml.rels", transform)


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_formula_without_cached_value_is_reported(tmp_path, suffix):
    path = tmp_path / f"formula{suffix}"
    _write_formula_book(path)

    gaps = inspect_ooxml_formula_cache(str(path))

    assert gaps == {"Stats": {"count": 1, "cells": ["A3"]}}


def test_formula_gap_marks_scan_partial_without_counting_failures(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_formula_book(data / "formula.xlsx")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["files_succeeded"] == 1
    assert scan["coverage"]["files_failed"] == 0
    assert scan["coverage"]["sheets_succeeded"] == 1
    assert scan["coverage"]["sheets_skipped"] == 0
    assert scan["coverage"]["limitations"] == [{
        "scope": "sheet",
        "reason": "formula_cache_missing",
        "file": "formula.xlsx",
        "sheet": "Stats",
        "count": 1,
        "cells": ["A3"],
    }]


def test_present_formula_cache_is_not_reported(tmp_path):
    path = tmp_path / "cached.xlsx"
    _write_formula_book(path)
    _set_formula_cache(path, "5")

    assert inspect_ooxml_formula_cache(str(path)) == {}


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_empty_formula_cache_is_reported(tmp_path, value):
    path = tmp_path / "empty.xlsx"
    _write_formula_book(path)
    _set_formula_cache(path, value)

    assert inspect_ooxml_formula_cache(str(path)) == {
        "Stats": {"count": 1, "cells": ["A3"]}
    }


def test_zero_formula_cache_is_present(tmp_path):
    path = tmp_path / "zero.xlsx"
    _write_formula_book(path)
    _set_formula_cache(path, "0")

    assert inspect_ooxml_formula_cache(str(path)) == {}


def test_formula_gap_examples_are_bounded(tmp_path):
    path = tmp_path / "many.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stats"
    for row in range(1, 7):
        ws.cell(row, 1, f"={row}+1")
    wb.save(path)

    gaps = inspect_ooxml_formula_cache(str(path), max_examples=3)

    assert gaps == {
        "Stats": {
            "count": 6,
            "cells": ["A1", "A2", "A3"],
        }
    }


def test_formula_gap_examples_can_be_disabled(tmp_path):
    path = tmp_path / "many.xlsx"
    _write_formula_book(path)

    gaps = inspect_ooxml_formula_cache(str(path), max_examples=0)

    assert gaps == {"Stats": {"count": 1, "cells": []}}


def test_formula_gap_order_follows_workbook_and_cell_order(tmp_path):
    path = tmp_path / "ordered.xlsx"
    wb = openpyxl.Workbook()
    first = wb.active
    first.title = "First"
    first["B2"] = "=2+2"
    first["A1"] = "=1+1"
    second = wb.create_sheet("Second")
    second["C3"] = "=3+3"
    wb.save(path)

    gaps = inspect_ooxml_formula_cache(str(path))

    assert list(gaps) == ["First", "Second"]
    assert gaps["First"]["cells"] == ["A1", "B2"]
    assert gaps["Second"]["cells"] == ["C3"]


@pytest.mark.parametrize(
    "target",
    [
        "/xl/worksheets/sheet1.xml",
        r"worksheets\sheet1.xml",
        "worksheets/../worksheets/sheet1.xml",
    ],
)
def test_worksheet_relationship_targets_are_normalized(tmp_path, target):
    path = tmp_path / "normalized.xlsx"
    _write_formula_book(path)
    _set_first_worksheet_target(path, target)

    assert inspect_ooxml_formula_cache(str(path)) == {
        "Stats": {"count": 1, "cells": ["A3"]}
    }


def test_worksheet_relationship_cannot_leave_package(tmp_path):
    path = tmp_path / "traversal.xlsx"
    _write_formula_book(path)
    _set_first_worksheet_target(path, "../../outside.xml")

    with pytest.raises(ValueError, match="worksheet target leaves package"):
        inspect_ooxml_formula_cache(str(path))


def test_non_ooxml_input_has_no_formula_cache_gaps(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("a\n1\n", encoding="utf-8")

    assert inspect_ooxml_formula_cache(Path(path)) == {}
