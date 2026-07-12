"""Tests for the PDF/Word table-extraction input path (_extract).

The pure normalization (`tables_to_sheets`) carries all the real logic and is
tested without any third-party dependency. The pdfplumber / python-docx adapters
are exercised end-to-end through `scan_dir`, skipped when the optional extra is
not installed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import paperconan._audit as audit
import paperconan._extract as extract
from paperconan._extract import tables_to_sheets
from paperconan._input import ExtractedTableResult, InputLimitation
from paperconan._sheet import Sheet

_POLICY_TOKEN_HEX_BY_ID = (
    ("T1", "6672617564"),
    ("T2", "6661627269636174"),
    ("T3", "66616b65"),
    ("T4", "6d6973636f6e64756374"),
    ("T5", "6775696c7479"),
    ("T6", "61636375736174696f6e"),
    ("T7", "72656420666c6167"),
    ("T8", "73757370696369"),
    ("T9", "6d616e6970756c6174"),
    ("T10", "646563657074696f6e"),
    ("T11", "646973686f6e657374"),
    ("T12", "6368656174"),
    ("T13", "63756c706162"),
)


def _policy_token_ids(source):
    folded = source.casefold()
    return [
        token_id
        for token_id, encoded in _POLICY_TOKEN_HEX_BY_ID
        if bytes.fromhex(encoded).decode("utf-8").casefold() in folded
    ]


def _enforce_neutral_language(module_name, source):
    token_ids = _policy_token_ids(source)
    if token_ids:
        pytest.fail(
            "neutral-language policy mismatch: "
            f"module={module_name} count={len(token_ids)} "
            f"token_ids={','.join(token_ids)}",
            pytrace=False,
        )


# --- pure normalization (no optional deps) ---------------------------------

def test_tables_to_sheets_coerces_numbers_keeps_text_and_names_sheets():
    raw = [("p1_t1", [["sample", "x"], ["s1", "1.5"], ["s2", "2.5"]])]
    sheets = tables_to_sheets("supp", raw)
    assert list(sheets) == ["supp!p1_t1"]
    rows = sheets["supp!p1_t1"]
    assert rows[0] == ["sample", "x"]
    assert rows[1] == ["s1", 1.5]   # numeric string -> float
    assert rows[2] == ["s2", 2.5]


def test_tables_to_sheets_pads_ragged_rows():
    raw = [("t1", [["a", "b", "c"], ["1"], ["2", "3"]])]
    rows = tables_to_sheets("d", raw)["d!t1"]
    assert all(len(r) == 3 for r in rows), "rows should be padded to the widest"
    assert rows[1] == [1, None, None]


def test_tables_to_sheets_handles_none_cells():
    raw = [("t1", [["a", "b"], ["1.5", None]])]
    rows = tables_to_sheets("d", raw)["d!t1"]
    assert rows[1] == [1.5, None]


def test_tables_to_sheets_drops_fully_empty_tables():
    raw = [("t1", [["", ""], [None, None]]), ("t2", [["v"], ["1.5"]])]
    sheets = tables_to_sheets("d", raw)
    assert list(sheets) == ["d!t2"], "a table with no content should be dropped"


def test_default_drops_standalone_over_budget_empty_table():
    raw = [("t1", [["", "", ""], [None, None, None]])]

    assert tables_to_sheets("d", raw, max_cells=2) == {}


def test_metadata_drops_standalone_over_budget_empty_table():
    raw = [("t1", [["", "", ""], [None, None, None]])]

    result = tables_to_sheets(
        "d", raw, max_cells=2, with_metadata=True
    )

    assert result.tables == {}
    assert result.limitations == []


def test_default_drops_over_budget_empty_table_after_budget_is_used():
    raw = [
        ("used", [["value", "1"]]),
        ("empty", [["", "", ""], [None, None, None]]),
    ]

    assert tables_to_sheets("d", raw, max_cells=2) == {
        "d!used": [["value", 1]]
    }


def test_metadata_drops_over_budget_empty_table_after_budget_is_used():
    raw = [
        ("used", [["value", "1"]]),
        ("empty", [["", "", ""], [None, None, None]]),
    ]

    result = tables_to_sheets(
        "d", raw, max_cells=2, with_metadata=True
    )

    assert result.tables == {"d!used": [["value", 1]]}
    assert result.limitations == []


def test_wide_row_stops_normalizing_when_dense_budget_is_crossed(
    monkeypatch,
):
    yielded = []
    normalized = []
    original_coerce = extract._coerce_cell

    def counting_coerce(value):
        normalized.append(value)
        return original_coerce(value)

    def wide_row():
        for index in range(100):
            yielded.append(index)
            yield "value" if index == 0 else str(index)

    monkeypatch.setattr(extract, "_coerce_cell", counting_coerce)

    result = tables_to_sheets(
        "d",
        [("t1", [wide_row()])],
        max_cells=2,
        with_metadata=True,
    )

    assert result.tables["d!t1"] is None
    assert yielded == [0, 1, 2]
    assert normalized == ["value", "1"]


def test_over_budget_empty_prefix_with_late_content_is_rejected():
    raw = [("t1", [["", ""], [None, "value"]])]

    result = tables_to_sheets(
        "d", raw, max_cells=1, with_metadata=True
    )

    assert result.tables["d!t1"] is None
    assert result.limitations[0].reason == "cell_limit"


def test_rejected_table_closes_generators_before_sibling_processing():
    events = []

    def row_cells():
        try:
            yield from ("value", "1", "2", "3")
        finally:
            events.append("row_closed")

    def first_table():
        try:
            yield row_cells()
            yield ["not reached"]
        finally:
            events.append("table_closed")

    first = first_table()

    def sibling_table():
        events.append("sibling_started")
        yield ["ok"]

    result = tables_to_sheets(
        "d",
        [("t1", first), ("t2", sibling_table())],
        max_cells=2,
        with_metadata=True,
    )

    assert events == ["row_closed", "table_closed", "sibling_started"]
    assert result.tables["d!t1"] is None
    assert result.tables["d!t2"] == [["ok"]]


def test_owned_production_modules_use_neutral_language():
    for module in (extract, audit):
        source = Path(module.__file__).read_text(encoding="utf-8").lower()
        _enforce_neutral_language(module.__name__, source)


def test_neutral_language_failure_reports_only_token_ids(
    tmp_path, monkeypatch
):
    encoded_token = "6672617564"
    decoded_token = bytes.fromhex(encoded_token).decode("utf-8")
    source_path = tmp_path / "module.py"
    source_path.write_text(decoded_token, encoding="utf-8")
    monkeypatch.setattr(extract, "__file__", str(source_path))

    with pytest.raises(pytest.fail.Exception) as exc_info:
        test_owned_production_modules_use_neutral_language()

    message = str(exc_info.value)
    if decoded_token in message:
        pytest.fail(
            "neutral-language gate exposed decoded policy vocabulary",
            pytrace=False,
        )
    assert message == (
        "neutral-language policy mismatch: "
        "module=paperconan._extract count=1 token_ids=T1"
    )


def test_extracted_tables_share_one_dense_cell_budget():
    raw = [
        ("t1", [["a", "b"], ["1", "2"]]),
        ("t2", [["a", "b"], ["3", "4"]]),
    ]
    result = tables_to_sheets(
        "d", raw, max_cells=6, with_metadata=True
    )
    assert result.tables["d!t1"] is not None
    assert result.tables["d!t2"] is None
    assert result.limitations[0].reason == "cell_limit"


def test_ragged_extracted_table_uses_dense_geometry():
    raw = [("t1", [["1"], ["2"], ["3", "4", "5", "6"]])]
    result = tables_to_sheets(
        "d", raw, max_cells=10, with_metadata=True
    )
    assert result.tables["d!t1"] is None


def test_docx_merged_cells_emit_text_once(tmp_path):
    docx = pytest.importorskip("docx")
    from paperconan._extract import load_docx_tables

    path = tmp_path / "merged.docx"
    doc = docx.Document()
    table = doc.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "merged"
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(1, 0).text = "vertical"
    table.cell(1, 0).merge(table.cell(2, 0))
    table.cell(1, 1).text = "same"
    table.cell(2, 1).text = "same"
    doc.save(path)

    rows = load_docx_tables(str(path))["merged!t1"]
    assert rows[0][:2] == ["merged", None]
    assert rows[1][0] == "vertical"
    assert rows[2][0] is None
    assert rows[1][1] == rows[2][1] == "same"


@pytest.mark.parametrize(
    ("suffix", "loader_name", "sheet_name"),
    [
        (".pdf", "load_pdf_tables", "tables!p1_t1"),
        (".docx", "load_docx_tables", "tables!t1"),
    ],
)
def test_load_table_result_calls_extractor_once_with_metadata(
    tmp_path, monkeypatch, suffix, loader_name, sheet_name
):
    path = tmp_path / f"tables{suffix}"
    path.write_bytes(b"placeholder")
    calls = []

    def stub_loader(called_path, *, max_cells, with_metadata):
        calls.append((called_path, max_cells, with_metadata))
        return ExtractedTableResult(
            tables={sheet_name: [["value"], [1]]}
        )

    monkeypatch.setattr(audit, "_MAX_CELLS", 7)
    monkeypatch.setattr(extract, loader_name, stub_loader)

    result = audit.load_table_result(str(path))

    assert calls == [(str(path), 7, True)]
    assert result.limitations == []
    assert isinstance(result.sheets[sheet_name], Sheet)
    assert result.sheets[sheet_name].cell(1, 0) == 1


def test_scan_counts_extracted_cell_limit_once(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "tables.pdf"
    path.write_bytes(b"placeholder")
    calls = []

    def stub_loader(called_path, *, max_cells, with_metadata):
        calls.append((called_path, max_cells, with_metadata))
        return ExtractedTableResult(
            tables={
                "tables!p1_t1": [["value"], [1], [2], [3]],
                "tables!p1_t2": None,
            },
            limitations=[
                InputLimitation(
                    scope="sheet",
                    reason="cell_limit",
                    sheet="tables!p1_t2",
                    details={"cells": 8, "max_cells": max_cells},
                )
            ],
        )

    monkeypatch.setattr(audit, "_MAX_CELLS", 6)
    monkeypatch.setattr(extract, "load_pdf_tables", stub_loader)

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert calls == [(str(path), 6, True)]
    assert scan["coverage"]["sheets_succeeded"] == 1
    assert scan["coverage"]["sheets_skipped"] == 1
    assert scan["coverage"]["limitations"] == [{
        "scope": "sheet",
        "reason": "cell_limit",
        "file": "tables.pdf",
        "sheet": "tables!p1_t2",
        "cells": 8,
        "max_cells": 6,
    }]


# --- adapters end-to-end through scan_dir (need optional extras) -----------

def _block_kinds(res):
    kinds = set()
    for blk in res.get("relations_blocks") or []:
        for group in ("relations", "progressions", "equal_pairs",
                      "within_col", "identical_after_rounding"):
            for f in blk.get(group, []) or []:
                kinds.add(f["kind"])
    return kinds


def test_docx_table_is_scanned_and_trips_detector(tmp_path):
    docx = pytest.importorskip("docx")
    from paperconan import scan_dir

    doc = docx.Document()
    table = doc.add_table(rows=7, cols=4)
    header = ["sample", "mass", "mass_copy", "note"]
    for c, h in enumerate(header):
        table.rows[0].cells[c].text = h
    for i in range(6):
        v = round(1.1 + i * 0.7, 4)
        cells = table.rows[i + 1].cells
        cells[0].text = f"s{i}"
        cells[1].text = str(v)
        cells[2].text = str(v)   # identical to mass -> identical_column
        cells[3].text = "ok"
    data = tmp_path / "data"
    data.mkdir()
    doc.save(str(data / "supplement.docx"))

    res = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert res["n_files"] == 1, "the .docx should be discovered and scanned"
    assert "identical_column" in _block_kinds(res), \
        "two identical numeric columns in a Word table should trip identical_column"


def test_pdf_table_is_scanned_and_trips_detector(tmp_path):
    pytest.importorskip("pdfplumber")
    from paperconan import scan_dir

    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "supp_table.pdf")
    assert os.path.exists(fixture), "run tests/build_pdf_fixture.py to (re)generate"

    data = tmp_path / "data"
    data.mkdir()
    import shutil
    shutil.copy(fixture, data / "supp_table.pdf")

    res = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert res["n_files"] == 1, "the .pdf should be discovered and scanned"
    assert "identical_column" in _block_kinds(res), \
        "two identical numeric columns in a PDF table should trip identical_column"


def test_pdf_sheet_names_carry_page_and_table_index():
    pytest.importorskip("pdfplumber")
    from paperconan._extract import load_pdf_tables

    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "supp_table.pdf")
    sheets = load_pdf_tables(fixture)
    assert sheets, "fixture should yield at least one table"
    assert all(name.startswith("supp_table!p") for name in sheets), \
        f"sheet names should be traceable to page/table, got {list(sheets)}"
