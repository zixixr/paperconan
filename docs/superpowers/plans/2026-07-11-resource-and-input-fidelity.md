# Resource and Input Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the declared cell budgets on every input path, disclose
formula-cache gaps, normalize merged Word cells, and keep complete `Sheet`
objects scoped to one source file.

**Architecture:** Add a small input-result contract for loader limitations and
a compact cross-sheet summary layer. `scan_dir` delegates each file to a helper
that runs sheet-local work immediately, returns only bounded summaries, and
releases complete sheets before the next file is loaded.

**Tech Stack:** Python 3.10+, NumPy, openpyxl, python-calamine, zipfile,
ElementTree, python-docx, pdfplumber, pytest.

## Global Constraints

- Use only neutral statistical-signal and data-inconsistency language.
- Preserve the existing `load_table(path) -> dict[str, Sheet | None]`
  compatibility interface.
- Do not evaluate spreadsheet formulas.
- Apply `_MAX_CELLS` cumulatively per source file before dense allocation.
- A complete `Sheet` must not survive beyond processing its source file.
- Cross-sheet state contains bounded grids, sparse text context, fingerprints,
  and recurrence aggregates only.
- Every production change follows a verified red-green cycle.
- Do not modify `recheck/` or `batches/`.

---

### Task 1: Define Loader Results and Formula-Cache Inspection

**Files:**
- Create: `src/paperconan/_input.py`
- Modify: `src/paperconan/_audit.py`
- Create: `tests/test_input_result.py`
- Create: `tests/test_formula_cache.py`

**Interfaces:**
- `InputLimitation(scope: str, reason: str, sheet: str | None = None,
  details: dict[str, Any] = field(default_factory=dict))`
- `TableLoadResult(sheets: dict[str, Sheet | None],
  limitations: list[InputLimitation])`
- `ExtractedTableResult(tables: dict[str, list[list[Any]] | None],
  limitations: list[InputLimitation])`
- `inspect_ooxml_formula_cache(path, max_examples=20)
  -> dict[str, dict[str, object]]`
- `load_table_result(path) -> TableLoadResult`
- Existing `load_table(path) -> dict[str, Sheet | None]` remains.

- [ ] **Step 1: Write failing result-contract tests**

Create `tests/test_input_result.py`:

```python
from paperconan._audit import load_table, load_table_result
from paperconan._input import InputLimitation, TableLoadResult
from paperconan._sheet import Sheet


def test_table_load_result_keeps_compatibility_dict(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    result = load_table_result(str(path))
    assert isinstance(result, TableLoadResult)
    assert isinstance(result.sheets["d"], Sheet)
    assert result.limitations == []
    assert load_table(str(path)).keys() == result.sheets.keys()


def test_input_limitation_serializes_deterministically():
    item = InputLimitation(
        scope="sheet",
        reason="cell_limit",
        sheet="S",
        details={"max_cells": 10, "cells": 12},
    )
    assert item.to_dict() == {
        "scope": "sheet",
        "reason": "cell_limit",
        "sheet": "S",
        "cells": 12,
        "max_cells": 10,
    }
```

- [ ] **Step 2: Write failing OOXML formula-cache tests**

Create `tests/test_formula_cache.py`:

```python
import zipfile
from xml.etree import ElementTree as ET

import openpyxl
import pytest

from paperconan._audit import scan_dir
from paperconan._input import inspect_ooxml_formula_cache


def _write_formula_book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stats"
    ws["A1"] = 2
    ws["A2"] = 3
    ws["A3"] = "=SUM(A1:A2)"
    wb.save(path)


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm"])
def test_formula_without_cached_value_is_reported(tmp_path, suffix):
    path = tmp_path / f"formula{suffix}"
    _write_formula_book(path)
    gaps = inspect_ooxml_formula_cache(str(path))
    assert gaps == {"Stats": {"count": 1, "cells": ["A3"]}}


def test_formula_gap_marks_scan_partial_without_evaluating(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_formula_book(data / "formula.xlsx")
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["scan_status"] == "partial"
    limits = scan["coverage"]["limitations"]
    assert any(
        item["reason"] == "formula_cache_missing"
        and item["sheet"] == "Stats"
        and item["count"] == 1
        for item in limits
    )


def test_present_formula_cache_is_not_reported(tmp_path):
    path = tmp_path / "cached.xlsx"
    _write_formula_book(path)
    with zipfile.ZipFile(path, "a") as zf:
        xml = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        cell = xml.find(".//m:c[@r='A3']", ns)
        value = cell.find("m:v", ns)
        if value is None:
            value = ET.SubElement(
                cell,
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v",
            )
        value.text = "5"
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            ET.tostring(xml, encoding="utf-8", xml_declaration=True),
        )
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
```

- [ ] **Step 3: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/test_input_result.py \
  tests/test_formula_cache.py -q
```

Expected: imports fail because `_input.py`, `TableLoadResult`, and
`load_table_result` do not exist.

- [ ] **Step 4: Implement the input-result types**

Create `src/paperconan/_input.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET
import posixpath
import zipfile

if TYPE_CHECKING:
    from ._sheet import Sheet


@dataclass(frozen=True)
class InputLimitation:
    scope: str
    reason: str
    sheet: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"scope": self.scope, "reason": self.reason}
        if self.sheet is not None:
            out["sheet"] = self.sheet
        for key in sorted(self.details):
            out[key] = self.details[key]
        return out


@dataclass
class TableLoadResult:
    sheets: dict[str, Sheet | None]
    limitations: list[InputLimitation] = field(default_factory=list)


@dataclass
class ExtractedTableResult:
    tables: dict[str, list[list[Any]] | None]
    limitations: list[InputLimitation] = field(default_factory=list)
```

- [ ] **Step 5: Implement streaming OOXML inspection**

In `src/paperconan/_input.py`, add:

```python
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _worksheet_paths(zf):
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall(f"{{{_PKG_REL_NS}}}Relationship")
    }
    out = []
    sheets = workbook.find(f"{{{_MAIN_NS}}}sheets")
    for sheet in list(sheets or []):
        rel_id = sheet.attrib[f"{{{_REL_NS}}}id"]
        target = targets[rel_id].replace("\\", "/")
        if target.startswith("/"):
            member = posixpath.normpath(target.lstrip("/"))
        else:
            member = posixpath.normpath(posixpath.join("xl", target))
        if member == ".." or member.startswith("../"):
            raise ValueError(f"worksheet target leaves package: {target!r}")
        out.append((sheet.attrib["name"], member))
    return out


def inspect_ooxml_formula_cache(path, *, max_examples=20):
    if not str(path).lower().endswith((".xlsx", ".xlsm")):
        return {}
    gaps = {}
    with zipfile.ZipFile(path) as zf:
        for sheet_name, member in _worksheet_paths(zf):
            count = 0
            cells = []
            with zf.open(member) as stream:
                for _event, elem in ET.iterparse(stream, events=("end",)):
                    if elem.tag != f"{{{_MAIN_NS}}}c":
                        continue
                    formula = elem.find(f"{{{_MAIN_NS}}}f")
                    value = elem.find(f"{{{_MAIN_NS}}}v")
                    if formula is not None and (
                        value is None or value.text in (None, "")
                    ):
                        count += 1
                        if len(cells) < max_examples:
                            cells.append(elem.attrib.get("r", "?"))
                    elem.clear()
            if count:
                gaps[sheet_name] = {"count": count, "cells": cells}
    return gaps
```

- [ ] **Step 6: Add the compatibility loader wrapper**

In `_audit.py`, add:

```python
from ._input import InputLimitation, TableLoadResult, inspect_ooxml_formula_cache


def load_table_result(path):
    sheets = _load_table_sheets(path)
    limitations = []
    for sheet, gap in inspect_ooxml_formula_cache(path).items():
        limitations.append(InputLimitation(
            scope="sheet",
            reason="formula_cache_missing",
            sheet=sheet,
            details={"count": gap["count"], "cells": gap["cells"]},
        ))
    return TableLoadResult(sheets=sheets, limitations=limitations)


def load_table(path):
    return load_table_result(path).sheets
```

Rename the current dispatcher body to `_load_table_sheets`. In `scan_dir`, use
`load_table_result`, add each limitation to `ScanCoverage`, and continue
scanning cached/source values.

- [ ] **Step 7: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/test_input_result.py \
  tests/test_formula_cache.py \
  tests/test_columnar_loader.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/paperconan/_input.py src/paperconan/_audit.py \
  tests/test_input_result.py tests/test_formula_cache.py
git commit -m "feat: disclose spreadsheet input limitations"
```

---

### Task 2: Enforce Dense Geometry Cell Budgets

**Files:**
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_columnar_loader.py`
- Modify: `tests/test_cell_guard.py`

**Interfaces:**
- `_dense_cells(row_count, max_width) -> int`
- `_fill_sheet_from_rows` applies `loaded + row_count * max_width`.
- Calamine uses `iter_rows()` and never calls `to_python()`.

- [ ] **Step 1: Add ragged CSV and cumulative workbook tests**

Append to `tests/test_columnar_loader.py`:

```python
def test_ragged_csv_budget_uses_dense_geometry(tmp_path, monkeypatch):
    import paperconan._audit as audit

    monkeypatch.setattr(audit, "_MAX_CELLS", 12)
    path = tmp_path / "ragged.csv"
    path.write_text(
        "a\nb\nc\n" + ",".join(str(i) for i in range(5)) + "\n",
        encoding="utf-8",
    )
    assert audit.load_csv_rows(str(path), ",")["ragged"] is None


def test_second_workbook_sheet_is_rejected_before_allocation(tmp_path, monkeypatch):
    import paperconan._audit as audit

    monkeypatch.setattr(audit, "_MAX_CELLS", 10)
    path = tmp_path / "two.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "one"
    ws1.append([1, 2, 3])
    ws1.append([4, 5, 6])
    ws2 = wb.create_sheet("two")
    ws2.append([1, 2, 3])
    ws2.append([4, 5, 6])
    wb.save(path)
    out = audit._load_workbook_openpyxl(str(path))
    assert out["one"] is not None
    assert out["two"] is None


def test_calamine_streams_rows_without_to_python(tmp_path, monkeypatch):
    import python_calamine as pc
    import paperconan._audit as audit

    path = tmp_path / "a.xlsx"
    _write_xlsx(path, [["a", "b"], [1, 2], [3, 4]])

    def forbidden(*args, **kwargs):
        raise AssertionError("to_python must not be called")

    monkeypatch.setattr(pc.CalamineSheet, "to_python", forbidden)
    assert audit._load_workbook_calamine(str(path))["S1"] is not None
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/test_columnar_loader.py \
  tests/test_cell_guard.py -q
```

Expected: the ragged CSV and Calamine streaming tests fail.

- [ ] **Step 3: Implement geometry accounting**

Add:

```python
def _dense_cells(row_count, max_width):
    return row_count * max_width
```

In `_fill_sheet_from_rows`, update `max_w`, then check:

```python
projected_rows = r + 1
projected_width = max(max_w, width)
if loaded + _dense_cells(projected_rows, projected_width) > _MAX_CELLS:
    return None, _dense_cells(projected_rows, projected_width)
max_w = projected_width
```

Return the final dense geometry count instead of the sum of raw row widths.

In `load_csv_rows`, track `row_count` and `max_width`; return `{stem: None}` as
soon as `_dense_cells(row_count, max_width) > _MAX_CELLS`. Pass ragged rows
directly to `Sheet.from_rows` without creating a second padded copy.

- [ ] **Step 4: Guard workbook allocation and stream Calamine**

For openpyxl, reject before `_fill_sheet_from_rows` when:

```python
declared = (ws.max_row or 0) * (ws.max_column or 0)
if loaded >= _MAX_CELLS or loaded + declared > _MAX_CELLS:
    out[s] = None
    continue
```

For Calamine:

```python
h, w = sh.height, sh.width
declared = h * w
if loaded >= _MAX_CELLS or loaded + declared > _MAX_CELLS:
    out[name] = None
    continue
norm = (
    [_calamine_cell(value) for value in row]
    for row in sh.iter_rows()
)
sheet, cells = _fill_sheet_from_rows(norm, h, w, loaded)
```

- [ ] **Step 5: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/test_columnar_loader.py \
  tests/test_cell_guard.py \
  tests/test_xls_reading.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/paperconan/_audit.py tests/test_columnar_loader.py \
  tests/test_cell_guard.py
git commit -m "fix: enforce dense input cell budgets"
```

---

### Task 3: Bound Extracted Tables and Normalize Merged DOCX Cells

**Files:**
- Modify: `src/paperconan/_extract.py`
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_extract.py`

**Interfaces:**
- `tables_to_sheets(stem, labeled_tables, max_cells=None,
  with_metadata=False)`
- The default return remains `dict[str, list[list[object]] | None]`.
- Metadata mode returns `ExtractedTableResult(tables, limitations)`.
- `load_pdf_tables` and `load_docx_tables` accept the same keyword options.
- Repeated DOCX XML-cell identities emit text once and `None` thereafter.

- [ ] **Step 1: Add extracted-table budget tests**

Append to `tests/test_extract.py`:

```python
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
```

- [ ] **Step 2: Add merged-cell identity tests**

Append:

```python
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
```

- [ ] **Step 3: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_extract.py -q
```

Expected: `tables_to_sheets` rejects the new arguments and merged text is
duplicated.

- [ ] **Step 4: Implement cumulative normalization**

Use `ExtractedTableResult` and `InputLimitation` in `_extract.py`. For each
table, coerce one row at a time, track `row_count`, `max_width`, and:

```python
table_cells = row_count * max_width
if max_cells is not None and loaded + table_cells > max_cells:
    sheets[f"{stem}!{label}"] = None
    limitations.append(InputLimitation(
        scope="sheet",
        reason="cell_limit",
        sheet=f"{stem}!{label}",
        details={"cells": table_cells, "max_cells": max_cells},
    ))
    rows = None
    break
```

If the table fits, construct `Sheet.from_rows(rows)` and increment
`loaded += table_cells`. Return
`ExtractedTableResult(tables=sheets, limitations=limitations)` when
`with_metadata=True`; otherwise return the existing row dictionary unchanged.

Update PDF and DOCX adapters to feed tables to normalization as generators
instead of first accumulating a file-wide `labeled` list.

- [ ] **Step 5: Deduplicate merged XML cells**

In `load_docx_tables`, keep one identity set per table:

```python
def table_rows(table):
    seen = set()
    for row in table.rows:
        values = []
        for cell in row.cells:
            identity = id(cell._tc)
            if identity in seen:
                values.append(None)
            else:
                seen.add(identity)
                values.append(cell.text)
        yield values
```

Distinct cells with identical text retain both values because their XML
identities differ.

- [ ] **Step 6: Integrate extractor limitations**

For PDF and DOCX, `load_table_result` calls the matching extractor once:

```python
extracted = loader(
    path,
    max_cells=_MAX_CELLS,
    with_metadata=True,
)
sheets = {
    name: None if rows is None else Sheet.from_rows(rows)
    for name, rows in extracted.tables.items()
}
limitations.extend(extracted.limitations)
```

The non-extractor dispatcher remains `_load_table_sheets(path)`. Propagate
returned limitations into `ScanCoverage`; a skipped extracted table uses
`mark_sheet_skipped`, while other tables in the file continue. Calling
`load_pdf_tables(path)` or `load_docx_tables(path)` without metadata options
continues to return the existing row dictionary.

- [ ] **Step 7: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/test_extract.py \
  tests/test_cell_guard.py \
  tests/test_scan_status.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/paperconan/_extract.py src/paperconan/_audit.py \
  tests/test_extract.py
git commit -m "fix: bound extracted tables and merged cells"
```

---

### Task 4: Build Compact Cross-Sheet Summaries

**Files:**
- Create: `src/paperconan/_summaries.py`
- Modify: `src/paperconan/_audit.py`
- Modify: `tests/test_collisions.py`
- Modify: `tests/test_recurring_row_vector.py`
- Create: `tests/test_cross_sheet_summaries.py`

**Interfaces:**
- `SparseLabelContext.cell(row, col)`
- `ColumnFingerprint`
- `CrossSheetSummary`
- `build_cross_sheet_summary(file, sheet, source, *, blocks=None,
  collision_max_rows=200, collision_max_cells=200000,
  min_column_length=12) -> tuple[CrossSheetSummary, list[InputLimitation]]`
- `RecurringRowIndex.add_sheet(file, sheet, source, *, blocks, figure_id,
  min_k=4, max_k=8, max_rows=300) -> dict[str, int | bool]`
- `RecurringRowIndex.findings(profile="review", max_findings=20)
  -> tuple[list[dict], dict[str, int]]`

- [ ] **Step 1: Write compactness and equivalence tests**

Create `tests/test_cross_sheet_summaries.py`:

```python
import numpy as np

from paperconan._audit import (
    build_cross_sheet_summary,
    detect_cross_sheet_column_duplicates,
)
from paperconan._sheet import Sheet
from paperconan._summaries import RecurringRowIndex


def _sheet(offset=0.0):
    rows = [["group", "a", "b"]]
    for i in range(14):
        rows.append([
            f"g{i}",
            offset + 1.2345 + i * 0.731,
            offset + 4.8765 + i * 0.413,
        ])
    return Sheet.from_rows(rows)


def test_summary_contains_no_sheet_or_ndarray():
    summary, limits = build_cross_sheet_summary(
        "a.xlsx", "Figure 1", _sheet(),
        collision_max_rows=200,
        collision_max_cells=1000,
    )
    assert limits == []
    assert not any(
        isinstance(value, (Sheet, np.ndarray))
        for value in vars(summary).values()
    )


def test_column_fingerprint_path_matches_compatibility_wrapper():
    sheets = {
        ("a.xlsx", "Figure 1"): _sheet(),
        ("b.xlsx", "Figure 2"): _sheet(),
    }
    summaries = [
        build_cross_sheet_summary(
            file, name, sheet,
            collision_max_rows=200,
            collision_max_cells=1000,
        )[0]
        for (file, name), sheet in sheets.items()
    ]
    direct = detect_cross_sheet_column_duplicates(sheets)
    compact = detect_cross_sheet_column_duplicates(summaries)
    assert compact == direct


def test_recurring_index_reports_budget_exhaustion():
    index = RecurringRowIndex(budget=1)
    source = _sheet()
    meta = index.add_sheet(
        "a.xlsx",
        "Figure 1",
        source,
        blocks=[(1, source.nrows, 1, source.ncols)],
        figure_id="main:1",
    )
    assert meta["budget_exhausted"] is True
    assert meta["windows_skipped"] > 0
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest \
  tests/test_cross_sheet_summaries.py \
  tests/test_collisions.py \
  tests/test_recurring_row_vector.py -q
```

Expected: summary types and compact detector paths do not exist.

- [ ] **Step 3: Implement summary value objects**

Create `src/paperconan/_summaries.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SparseLabelContext:
    nrows: int
    ncols: int
    text: dict[tuple[int, int], str]

    def cell(self, row, col):
        if 0 <= row < self.nrows and 0 <= col < self.ncols:
            return self.text.get((row, col))
        return None


@dataclass(frozen=True)
class ColumnFingerprint:
    file: str
    sheet: str
    col_idx: int
    label: str
    length: int
    digest: str
    all_int: bool
    distinct: int
    sample: tuple[int | float, ...]


@dataclass(frozen=True)
class CrossSheetSummary:
    file: str
    sheet: str
    grid: dict[tuple[int, int], float]
    labels: SparseLabelContext
    columns: tuple[ColumnFingerprint, ...]
```

- [ ] **Step 4: Add exact numeric fingerprints**

In `_audit.py`, canonicalize each source number by exact rational value:

```python
def _numeric_ratio(value):
    if isinstance(value, int):
        return value, 1
    return float(value).as_integer_ratio()


def _fingerprint_values(values):
    digest = hashlib.blake2b(digest_size=20)
    for value in values:
        numerator, denominator = _numeric_ratio(value)
        token = f"{numerator}/{denominator};".encode("ascii")
        digest.update(token)
    return digest.hexdigest()
```

Build each qualifying column while its `Sheet` is alive. Store only its digest,
length, exact all-integer flag, distinct count, and first five values. Change
`detect_cross_sheet_column_duplicates` to accept either the legacy sheet
mapping or a sequence of `CrossSheetSummary`; the legacy path builds temporary
summaries, preserving direct callers.

- [ ] **Step 5: Bound collision grids and sparse context**

Extend `_grid_from_rows` with `max_cells`. Iterate row-major and stop after the
limit. Return metadata:

```python
{
    "rows_total": sheet.nrows,
    "rows_used": rmax,
    "cells_used": len(grid),
    "row_limited": sheet.nrows > rmax,
    "cell_limited": cell_limited,
}
```

For label context, copy only string entries whose row is below
`min(sheet.nrows, collision_max_rows + 3)`. Update
`_label_context_for_matches` to read either a `Sheet` or
`SparseLabelContext`.

- [ ] **Step 6: Implement incremental recurring-row aggregation**

In `_summaries.py`, add `RecurringRowIndex`. It must not import `_audit.py`.
`_audit.py` computes `blocks=find_numeric_blocks(source)` and
`figure_id=figure_key(sheet)` and passes both into `add_sheet`. The method
performs the current window enumeration immediately, decrements a global budget
per valid window, and stores:

```python
{
    "vector": tuple(round(value, 6) for value in window),
    "site_count": 1,
    "sites": {(file, sheet, row, start_col)},
    "figures": {figure_id} if figure_id is not None else set(),
}
```

Increment `site_count` for every distinct occurrence but store at most 16 site
tuples per vector for evidence and overlap checks. Return
`{"budget_exhausted": bool, "windows_skipped": int}`. Its `findings` method
applies the existing patterned-vector, multi-figure, integer, overlap-dedup,
severity, and `max_findings` rules without any `Sheet` input and returns
`(findings, {"findings_omitted": omitted})`. The compatibility
`detect_recurring_row_vectors(sheet_mapping, profile="review", min_k=4,
max_k=8, max_rows=300, max_findings=20)` computes blocks and figure IDs in
`_audit.py`, builds an index, and delegates.

- [ ] **Step 7: Run and verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/test_cross_sheet_summaries.py \
  tests/test_collisions.py \
  tests/test_decimal_tail_gate.py \
  tests/test_recurring_row_vector.py \
  tests/test_fraction_reuse.py -q
```

Expected: all pass and compact output matches compatibility paths.

- [ ] **Step 8: Commit**

```bash
git add src/paperconan/_summaries.py src/paperconan/_audit.py \
  tests/test_cross_sheet_summaries.py tests/test_collisions.py \
  tests/test_recurring_row_vector.py
git commit -m "refactor: summarize cross-sheet scan state"
```

---

### Task 5: Scope Complete Sheets to One Source File

**Files:**
- Modify: `src/paperconan/_audit.py`
- Create: `tests/test_resource_lifetime.py`
- Modify: `tests/test_fdr.py`
- Modify: `tests/test_fraction_reuse.py`
- Modify: `tests/test_detection_recall_e2e.py`

**Interfaces:**
- `_process_file(path, *, input_dir, state) -> FileScanResult`
- `ScanBudgetState(coverage, recurring_index, profile, evidence,
  findings_kept=0, findings_omitted=0)`
- `FileScanResult` contains no `Sheet`, NumPy array, or complete numeric list.
- Within-sheet fraction reuse and digit reports run before `_process_file`
  returns.

- [ ] **Step 1: Write a lifetime regression**

Create `tests/test_resource_lifetime.py`:

```python
import gc
import weakref

import paperconan._audit as audit
from paperconan._input import TableLoadResult
from paperconan._sheet import Sheet


def test_previous_file_sheet_is_released_before_next_load(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n2\n3\n", encoding="utf-8")
    (data / "b.csv").write_text("x\n4\n5\n6\n", encoding="utf-8")
    refs = []

    def stub_load(path):
        if refs:
            gc.collect()
            assert refs[-1]() is None
        sheet = Sheet.from_rows([["x"], [1.1], [2.2], [3.3]])
        refs.append(weakref.ref(sheet.numeric))
        return TableLoadResult({path: sheet})

    monkeypatch.setattr(audit, "load_table_result", stub_load)
    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )
    assert scan["coverage"]["files_succeeded"] == 2
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m pytest tests/test_resource_lifetime.py -q
```

Expected: the first dense array remains reachable through directory-wide
`grid_sheets`.

- [ ] **Step 3: Add file-scope result objects**

In `_audit.py`, define:

```python
@dataclass
class ScanBudgetState:
    coverage: ScanCoverage
    recurring_index: RecurringRowIndex
    profile: str
    evidence: bool
    findings_kept: int = 0
    findings_omitted: int = 0


@dataclass
class FileScanResult:
    report_blocks: list[dict]
    digit_reports: list[dict]
    decimal_reports: list[dict]
    summaries: list[CrossSheetSummary]
    within_sheet_findings: list[dict]
    stats: dict
    errors: list[dict]
```

Create
`_process_file(path, *, input_dir, state) -> FileScanResult` containing the
current file-size guard, load, and per-sheet loop. It updates
`state.coverage`, `state.findings_kept`, and `state.findings_omitted`. For each
sheet, in this order:

1. add loader limitations to coverage;
2. find and analyze numeric blocks;
3. run `detect_within_sheet_fraction_reuse` for that one sheet;
4. run digit and decimal-ending reports from a temporary numeric list;
5. add the sheet to `RecurringRowIndex`;
6. build `CrossSheetSummary`;
7. delete the temporary numeric list and continue.

The returned `FileScanResult` must contain only dictionaries, scalar metadata,
and compact summaries.

- [ ] **Step 4: Aggregate compact results in `scan_dir`**

Replace `per_sheet_numbers`, `grid_sheets`, and directory-wide complete sheets
with:

```python
summaries = []
state = ScanBudgetState(
    coverage=coverage,
    recurring_index=RecurringRowIndex(
        budget=_RECURRING_ROW_VECTOR_BUDGET
    ),
    profile=profile,
    evidence=evidence,
)
for path in files:
    result = _process_file(path, input_dir=in_dir, state=state)
    report_blocks.extend(result.report_blocks)
    digit_reports.extend(result.digit_reports)
    decimal_reports.extend(result.decimal_reports)
    summaries.extend(result.summaries)
    cross_sheet_findings.extend(result.within_sheet_findings)
```

After the loop, run collisions and column fingerprints from `summaries`, and
recurring rows from `state.recurring_index`. Copy
`state.findings_kept`/`state.findings_omitted` into the existing output
counters.

When grid cell or recurring budgets truncate work, add
`collision_grid_cell_limit` or `recurring_row_vector_budget` limitations with
counts. A recurring finding-output cap adds
`recurring_row_vector_finding_limit`.

- [ ] **Step 5: Run focused behavior tests**

```bash
.venv/bin/python -m pytest \
  tests/test_resource_lifetime.py \
  tests/test_fdr.py \
  tests/test_fraction_reuse.py \
  tests/test_detection_recall_e2e.py \
  tests/test_collisions.py \
  tests/test_recurring_row_vector.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/paperconan/_audit.py tests/test_resource_lifetime.py \
  tests/test_fdr.py tests/test_fraction_reuse.py \
  tests/test_detection_recall_e2e.py
git commit -m "refactor: bound source-file scan lifetime"
```

---

### Task 6: Resource and Input Regression Gate

**Files:**
- Modify only to address regressions in this component.

- [ ] **Step 1: Run focused tests**

```bash
.venv/bin/python -m pytest \
  tests/test_input_result.py \
  tests/test_formula_cache.py \
  tests/test_columnar_loader.py \
  tests/test_cell_guard.py \
  tests/test_extract.py \
  tests/test_cross_sheet_summaries.py \
  tests/test_resource_lifetime.py \
  tests/test_collisions.py \
  tests/test_decimal_tail_gate.py \
  tests/test_recurring_row_vector.py \
  tests/test_fraction_reuse.py \
  tests/test_fdr.py \
  tests/test_detection_recall_e2e.py -q
```

Expected: all pass.

- [ ] **Step 2: Run the complete suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass with only the intentional live-network skip.

- [ ] **Step 3: Verify bounded-state invariants**

Run the exact regression nodes that exercise file lifetime, successful-file
accounting, and bounded formula examples:

```bash
.venv/bin/python -m pytest \
  tests/test_resource_lifetime.py::test_previous_file_sheet_is_released_before_next_load \
  tests/test_formula_cache.py::test_formula_gap_examples_are_bounded -q
```

Expected: both pass.
