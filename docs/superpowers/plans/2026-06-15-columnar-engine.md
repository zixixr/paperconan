# Columnar Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace paperconan's audit substrate (`{sheet: list[list]}`) with a columnar `Sheet` object (numpy numeric array + sparse text + int mask), streaming the loader so giant supplementary tables no longer OOM — with detector output byte-identical.

**Architecture:** A `Sheet` value object backs every column access. Phase 1 swaps the substrate type behind the existing accessors using a `from_rows` bridge (behavior locked, no memory win). Phase 2 streams the loader directly into `Sheet` (the memory win). A golden-snapshot test of the oracle fixtures' `scan.json` is the correctness gate throughout.

**Tech Stack:** Python, numpy, openpyxl (reference reader), python-calamine (optional accelerator), pytest. Spec: `docs/superpowers/specs/2026-06-15-columnar-engine-design.md`.

---

## File Structure

- `src/paperconan/_sheet.py` — **new**: the `Sheet` class (data model + `from_rows`, `cell`, `block`, `numeric_values`).
- `src/paperconan/_audit.py` — modify: reimplement `col_array`, `find_numeric_blocks`, `header_for`, `_block_evidence`, `_grid_from_rows`, the three direct-indexing detectors, the loaders, and the `scan_dir` threading.
- `tests/test_sheet.py` — **new**: `Sheet` unit tests.
- `tests/test_golden_columnar.py` — **new**: oracle `scan.json` golden lock.
- `tests/golden/` — **new**: committed golden finding fixtures.
- `tests/test_columnar_loader.py` — **new**: streaming-loader + calamine-parity tests.
- `scripts/bench_columnar.py` — **new**: peak-RSS/time benchmark.

---

## Task 1: Golden snapshot lock (behavior baseline)

**Files:**
- Create: `tests/test_golden_columnar.py`
- Create: `tests/golden/.gitkeep` (golden JSON written by the test's generate mode)
- Uses fixtures: `tests/fixtures/tiny_paper`, plus a trimmed copy of an oracle dir.

This MUST be written and committed FIRST, against the current (unchanged) engine, so it captures `main` behavior and then guards the rewrite.

- [ ] **Step 1: Write the golden helper + test**

```python
# tests/test_golden_columnar.py
"""Golden lock: the columnar rewrite must reproduce current findings exactly.

Run `PAPERCONAN_GEN_GOLDEN=1 pytest tests/test_golden_columnar.py` once on the
baseline to (re)generate tests/golden/*.json, then commit them. After that the
test compares live output against the committed golden and fails on any drift.
"""
import json
import os
import pathlib
import pytest
from paperconan._audit import scan_dir

HERE = pathlib.Path(__file__).parent
GOLD = HERE / "golden"
FIXTURES = HERE / "fixtures"

# (name, input dir) — small dirs that exercise many detectors.
CASES = [
    ("tiny_paper", FIXTURES / "tiny_paper"),
]

def _stable(scan):
    """Strip volatile metadata (paths, timings, version, timestamps); keep the
    findings substance that the rewrite must preserve."""
    blocks = []
    for b in scan.get("report_blocks", []):
        blocks.append({
            "file": b["file"], "sheet": b["sheet"],
            "coords": [b.get("r0"), b.get("r1"), b.get("c0"), b.get("c1")],
            "findings": sorted(
                [{k: v for k, v in f.items() if k not in ("evidence",)} for grp in
                 ("relations", "arithmetic", "equal_pairs", "within_column",
                  "identical_after_rounding", "grim_grimmer")
                 for f in b.get(grp, [])],
                key=lambda d: json.dumps(d, sort_keys=True, default=str)),
        })
    blocks.sort(key=lambda d: (d["file"], d["sheet"], str(d["coords"])))
    collisions = sorted(
        [{k: v for k, v in c.items() if k not in ("evidence",)} for c in scan.get("collisions", [])],
        key=lambda d: json.dumps(d, sort_keys=True, default=str))
    return {"blocks": blocks, "collisions": collisions}

@pytest.mark.parametrize("name,indir", CASES)
def test_golden(tmp_path, name, indir):
    scan = scan_dir(str(indir), str(tmp_path), write_md=False, write_html=False)
    stable = _stable(scan)
    gold_path = GOLD / f"{name}.json"
    if os.environ.get("PAPERCONAN_GEN_GOLDEN"):
        GOLD.mkdir(exist_ok=True)
        gold_path.write_text(json.dumps(stable, indent=2, sort_keys=True, default=str))
        pytest.skip(f"generated {gold_path}")
    assert gold_path.exists(), f"missing golden {gold_path}; run PAPERCONAN_GEN_GOLDEN=1"
    assert stable == json.loads(gold_path.read_text())
```

Note: confirm `scan_dir`'s return value key names (`report_blocks`, the per-group keys, `collisions`, `r0/r1/c0/c1`) against `_audit.py:1117+` and adjust `_stable` to match the ACTUAL structure before generating. If `scan_dir` returns nothing useful, read the written `scan.json` from `tmp_path` instead.

- [ ] **Step 2: Generate + verify the golden is non-trivial**

Run: `PAPERCONAN_GEN_GOLDEN=1 python -m pytest tests/test_golden_columnar.py -q`
Then inspect `tests/golden/tiny_paper.json` — it must contain at least one finding (a golden of `{"blocks": [], "collisions": []}` guards nothing). If empty, add a richer fixture dir to `CASES` (e.g. a trimmed `zssom_*` copy with a known duplication) until findings appear.

- [ ] **Step 3: Run the test in compare mode (must pass on baseline)**

Run: `python -m pytest tests/test_golden_columnar.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_golden_columnar.py tests/golden/
git commit -m "test: golden snapshot lock for oracle findings (columnar baseline)"
```

---

## Task 2: The `Sheet` class

**Files:**
- Create: `src/paperconan/_sheet.py`
- Test: `tests/test_sheet.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sheet.py
import numpy as np
from paperconan._sheet import Sheet

def test_from_rows_roundtrip_types():
    rows = [["label", 1, 2.5, None],
            ["x", 3, 4.0, "txt"],
            [None, True, 0.001, 7]]
    s = Sheet.from_rows(rows)
    assert s.nrows == 3 and s.ncols == 4
    # numeric cells reconstruct with int/float fidelity
    assert s.cell(0, 1) == 1 and isinstance(s.cell(0, 1), int)
    assert s.cell(0, 2) == 2.5 and isinstance(s.cell(0, 2), float)
    assert s.cell(1, 2) == 4.0 and isinstance(s.cell(1, 2), float)
    # text / None / bool preserved (bool is NOT numeric, per is_num)
    assert s.cell(0, 0) == "label"
    assert s.cell(0, 3) is None
    assert s.cell(1, 3) == "txt"
    assert s.cell(2, 1) is True

def test_numeric_array_nan_for_nonnumeric():
    s = Sheet.from_rows([["a", 1], [2, None], [3.5, "b"]])
    nm = s.numeric
    assert np.isnan(nm[0, 0]) and nm[0, 1] == 1.0
    assert nm[1, 0] == 2.0 and np.isnan(nm[1, 1])
    assert nm[2, 0] == 3.5 and np.isnan(nm[2, 1])

def test_block_and_numeric_values():
    s = Sheet.from_rows([[1, 2], [3, 4], [5, 6]])
    blk = s.block(0, 3, 0, 2)
    assert blk.shape == (3, 2) and blk[2, 1] == 6.0
    vals = sorted(s.numeric_values())
    assert vals == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

def test_ragged_rows_padded():
    s = Sheet.from_rows([[1], [2, 3, 4], [5, 6]])
    assert s.ncols == 3
    assert np.isnan(s.numeric[0, 1]) and s.cell(0, 1) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_sheet.py -q`
Expected: FAIL (`ModuleNotFoundError: paperconan._sheet`).

- [ ] **Step 3: Implement `Sheet`**

```python
# src/paperconan/_sheet.py
"""Columnar substrate for the audit engine.

A Sheet replaces the legacy {sheet: list[list]} representation. Numeric cells
live in a dense float64 array (NaN = empty-or-non-numeric); non-numeric cells
(text, dates, bools) live in a sparse dict; integer-typed cells are tracked in
a sparse set so evidence keeps int-vs-float fidelity. The reconstruction rule in
`cell()` reproduces the original value exactly for every accessor and the
evidence builder.
"""
from __future__ import annotations
import math
import numpy as np


def _is_num(x):
    # mirror paperconan._audit.is_num WITHOUT importing it (avoid a cycle):
    # bool is NOT numeric; NaN/inf are NOT numeric.
    if x is None or isinstance(x, bool):
        return False
    if isinstance(x, (int, float)):
        return not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))
    return False


class Sheet:
    __slots__ = ("nrows", "ncols", "numeric", "_text", "_ints")

    def __init__(self, nrows, ncols, numeric, text, ints):
        self.nrows = nrows
        self.ncols = ncols
        self.numeric = numeric          # (nrows, ncols) float64, NaN sentinel
        self._text = text               # {(r, c): original non-numeric value}
        self._ints = ints               # set[(r, c)] where original was a Python int

    @classmethod
    def from_rows(cls, rows):
        rows = list(rows)
        nrows = len(rows)
        ncols = max((len(r) for r in rows), default=0)
        numeric = np.full((nrows, ncols), np.nan, dtype=float)
        text = {}
        ints = set()
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if _is_num(v):
                    numeric[r, c] = float(v)
                    if isinstance(v, int):
                        ints.add((r, c))
                elif v is not None:
                    text[(r, c)] = v
        return cls(nrows, ncols, numeric, text, ints)

    def cell(self, r, c):
        """Original value at (r, c): number (int/float fidelity), text/date/bool, or None."""
        if 0 <= r < self.nrows and 0 <= c < self.ncols:
            v = self.numeric[r, c]
            if not math.isnan(v):
                return int(v) if (r, c) in self._ints else v
        return self._text.get((r, c))

    def block(self, r0, r1, c0, c1):
        """float64 sub-array (NaN for non-numeric) — the equal-pairs block matrix."""
        return self.numeric[r0:r1, c0:c1].copy()

    def numeric_values(self):
        """Flat list of all numeric cell values (order unspecified)."""
        col = self.numeric[~np.isnan(self.numeric)]
        return col.tolist()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_sheet.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_sheet.py tests/test_sheet.py
git commit -m "feat(audit): Sheet columnar substrate (numeric array + sparse text + int mask)"
```

---

## Task 3: `col_array` and `find_numeric_blocks` on `Sheet`

**Files:**
- Modify: `src/paperconan/_audit.py:299` (`col_array`), `:253` (`find_numeric_blocks`)
- Test: `tests/test_columnar_accessors.py` (new)

- [ ] **Step 1: Write parity tests**

```python
# tests/test_columnar_accessors.py
import numpy as np
from paperconan._sheet import Sheet
from paperconan._audit import col_array, find_numeric_blocks

def test_col_array_parity():
    rows = [["h1", "h2"], [1, 10], [2, 20], [3, 30]]
    s = Sheet.from_rows(rows)
    a = col_array(s, 1, 4, 0)
    assert np.allclose(a, [1.0, 2.0, 3.0])
    b = col_array(s, 1, 4, 1)
    assert np.allclose(b, [10.0, 20.0, 30.0])

def test_col_array_nan_for_text():
    s = Sheet.from_rows([["x"], [1], ["y"], [3]])
    a = col_array(s, 0, 4, 0)
    assert np.isnan(a[0]) and a[1] == 1.0 and np.isnan(a[2]) and a[3] == 3.0

def test_find_numeric_blocks_parity():
    rows = [["A", "B", "C"]] + [[i, i * 2, "note"] for i in range(1, 8)]
    s = Sheet.from_rows(rows)
    blocks = find_numeric_blocks(s)
    # a 7-row, 2-col numeric block starting at row 1, cols 0..2
    assert any(r1 - r0 >= 3 and c1 - c0 >= 1 for (r0, r1, c0, c1) in blocks)
    assert all(0 <= c0 < c1 <= s.ncols for (_, _, c0, c1) in blocks)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_columnar_accessors.py -q`
Expected: FAIL (`col_array` still indexes `rows[r][c]`; passing a `Sheet` raises).

- [ ] **Step 3: Reimplement both functions**

Replace `col_array` (`_audit.py:299-304`) with:

```python
def col_array(sheet, r0, r1, c):
    return sheet.numeric[r0:r1, c].copy()
```

Replace the body of `find_numeric_blocks` (`_audit.py:253-285`) — keep the block-walk logic, change only how `num` is built:

```python
def find_numeric_blocks(sheet, min_rows=3, min_cols=1):
    R, C = sheet.nrows, sheet.ncols
    if R == 0 or C == 0:
        return []
    num = ~np.isnan(sheet.numeric)          # vectorized, replaces the per-cell is_num loop
    blocks = []
    visited = np.zeros_like(num)
    for j in range(C):
        i = 0
        while i < R:
            if num[i, j] and not visited[i, j]:
                i0 = i
                while i < R and num[i, j]:
                    i += 1
                i1 = i
                j1 = j + 1
                while j1 < C:
                    col_density = num[i0:i1, j1].mean() if i1 > i0 else 0
                    if col_density >= 0.7:
                        j1 += 1
                    else:
                        break
                visited[i0:i1, j:j1] = True
                if (i1 - i0) >= min_rows and (j1 - j) >= min_cols:
                    blocks.append((i0, i1, j, j1))
            else:
                i += 1
    return blocks
```

- [ ] **Step 4: Run accessor tests**

Run: `python -m pytest tests/test_columnar_accessors.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_audit.py tests/test_columnar_accessors.py
git commit -m "refactor(audit): col_array + find_numeric_blocks on Sheet (vectorized)"
```

---

## Task 4: `header_for`, `_block_evidence`, `_grid_from_rows` on `Sheet`

**Files:**
- Modify: `src/paperconan/_audit.py:288` (`header_for`), `:322` (`_block_evidence`), `:813` (`_grid_from_rows`)
- Test: append to `tests/test_columnar_accessors.py`

- [ ] **Step 1: Write tests**

```python
def test_header_for_uses_text():
    from paperconan._audit import header_for
    s = Sheet.from_rows([["Mass", "Width"], [1.0, 2.0], [3.0, 4.0]])
    assert header_for(s, 1, 0, 2) == ["Mass", "Width"]

def test_block_evidence_int_fidelity():
    from paperconan._audit import _block_evidence
    s = Sheet.from_rows([["H"], [5], [2.5]])
    ev = _block_evidence(s, 1, 3, 0, 1, ["H"], [0])
    flat = [row["values"][0] for row in ev["rows"]]
    assert 5 in flat and isinstance([v for v in flat if v == 5][0], int)
    assert 2.5 in flat

def test_grid_from_rows_only_decimals():
    from paperconan._audit import _grid_from_rows
    s = Sheet.from_rows([[1, 2.345], [3, 7]])   # 2.345 kept; ints dropped
    g = _grid_from_rows(s)
    assert (0, 1) in g and g[(0, 1)] == round(2.345, 9)
    assert (0, 0) not in g and (1, 1) not in g
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_columnar_accessors.py -q -k "header_for or block_evidence or grid_from"`
Expected: FAIL (these still index `rows`).

- [ ] **Step 3: Reimplement against `Sheet`**

`header_for` (`_audit.py:288-296`) — replace `rows[r][...]` with `sheet.cell(r, ...)`:

```python
def header_for(sheet, r0, c0, c1):
    for r in range(r0 - 1, max(-1, r0 - 5), -1):
        if r < 0:
            continue
        line = [sheet.cell(r, c) for c in range(c0, c1)]
        texty = [x for x in line if x is not None and not is_num(x)]
        if texty:
            return [str(sheet.cell(r, c)).strip() if sheet.cell(r, c) is not None else ""
                    for c in range(c0, c1)]
    return [""] * (c1 - c0)
```

`_block_evidence` (`_audit.py:322-341`) — replace the `rows[r][c]`/`len(rows)` access:

```python
def _block_evidence(sheet, r0, r1, c0, c1, header, highlight_cols, highlight_rows=None):
    r_start = max(0, r0 - 1)
    r_end = min(sheet.nrows, r1 + 1)
    data_rows = []
    for r in range(r_start, r_end):
        vals = [_cell_value(sheet.cell(r, c)) for c in range(c0, c1)]
        data_rows.append({"row_idx": r + 1, "is_context": r < r0 or r >= r1, "values": vals})
    return {"headers": list(header), "col_offset": c0,
            "highlight_cols": list(highlight_cols),
            "highlight_rows": list(highlight_rows) if highlight_rows else [],
            "rows": data_rows}
```

`_grid_from_rows` (`_audit.py:813-828`) — iterate the numeric array (ints already excluded by `fv != int(fv)`):

```python
def _grid_from_rows(sheet, min_decimal_places=3, max_rows=200):
    grid = {}
    nm = sheet.numeric
    rmax = min(sheet.nrows, max_rows)
    for ri in range(rmax):
        for ci in range(sheet.ncols):
            fv = nm[ri, ci]
            if math.isnan(fv):
                continue
            if fv != int(fv) and 0.001 <= abs(fv) < 100000:
                s = repr(float(fv))
                if "." in s and "e" not in s.lower():
                    frac = s.split(".", 1)[1]
                    if len(frac) >= min_decimal_places:
                        grid[(ri, ci)] = round(fv, 9)
    return grid
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_columnar_accessors.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_audit.py tests/test_columnar_accessors.py
git commit -m "refactor(audit): header_for + evidence + grid builder on Sheet"
```

---

## Task 5: Swap direct-indexing detectors + thread `scan_dir` through `Sheet`

This is the integration task that makes the whole engine run on `Sheet` (still loading via the legacy rows path wrapped in `from_rows`). The golden test is the gate.

**Files:**
- Modify: `src/paperconan/_audit.py` — `detect_equal_pairs:793`, `detect_identical_after_rounding:614`, `detect_grim_grimmer:688/689/704`, `scan_dir:1157-1210`, and `_attach_evidence` (signature passes `rows`).

- [ ] **Step 1: Swap the three direct-indexing detectors**

`detect_equal_pairs` (`_audit.py:793-794`) — replace the comprehension that builds `A`:

```python
def detect_equal_pairs(sheet, r0, r1, c0, c1, header):
    findings = []
    A = sheet.block(r0, r1, c0, c1)
    # ... rest of the body is UNCHANGED ...
```

`detect_identical_after_rounding` (`_audit.py:614`) — `v = rows[r][c] ...` becomes:

```python
            v = sheet.cell(r, c)
```

`detect_grim_grimmer` (`_audit.py:688-704`) — the three reads become:

```python
        mv = sheet.cell(r, mean_c)
        nv = sheet.cell(r, n_c)
        ...
            sv = sheet.cell(r, sd_c)
```

Rename each detector's first parameter `rows` → `sheet` (signature only). Also rename `rows`→`sheet` in `detect_relations`, `detect_arithmetic_progression`, `detect_within_column_patterns` signatures (their bodies already only call `col_array(sheet, ...)`, which now takes a Sheet).

- [ ] **Step 2: Thread `scan_dir`**

In the sheet loop (`_audit.py:1168-1210`), the variable is currently `rows`. Bridge it to a Sheet and update the direct accesses:

```python
        for sn, raw in sheets.items():
            sheet_start = time.perf_counter()
            if raw is None:        # oversized sheet: unchanged
                # ... unchanged oversized branch ...
                continue
            sheet = raw if isinstance(raw, Sheet) else Sheet.from_rows(raw)
            grids[(os.path.basename(f), sn)] = _grid_from_rows(sheet)
            sheet_nums = sheet.numeric_values()
            per_sheet_numbers[(os.path.basename(f), sn)] = sheet_nums
            blocks = find_numeric_blocks(sheet)
            max_cols = sheet.ncols
            # ... scan_stats uses sheet.nrows / max_cols / len(sheet_nums) ...
            for (r0, r1, c0, c1) in blocks:
                ...
                header = header_for(sheet, r0, c0, c1)
                ...
                rel = [] if wide else detect_relations(sheet, r0, r1, c0, c1, header)
                ap = detect_arithmetic_progression(sheet, r0, r1, c0, c1, header)
                eq = [] if wide else detect_equal_pairs(sheet, r0, r1, c0, c1, header)
                wc = detect_within_column_patterns(sheet, r0, r1, c0, c1, header)
                iar = detect_identical_after_rounding(sheet, r0, r1, c0, c1, header)
                gg = detect_grim_grimmer(sheet, r0, r1, c0, c1, header)
                ...
                    for group in (rel, ap, eq, wc, iar, gg):
                        _attach_evidence(group, sheet, r0, r1, c0, c1, header)
```

Update `_attach_evidence`'s signature/body so its `rows` param becomes `sheet` and it calls `_block_evidence(sheet, ...)`. Add `from ._sheet import Sheet` at the top of `_audit.py`.

- [ ] **Step 3: Run the golden test + full suite**

Run: `python -m pytest tests/test_golden_columnar.py tests/test_sheet.py tests/test_columnar_accessors.py -q`
Expected: PASS.
Run: `python -m pytest -q`
Expected: PASS (entire suite green — `test_grim`, `test_collisions`, `test_relations_flood`, `test_benign`, `test_cell_guard`, `test_oversized_guard`, etc.).

- [ ] **Step 4: Fix any drift, then commit**

If the golden test fails, diff the live `_stable` output vs golden and fix the accessor that drifted (do NOT edit the golden). When green:

```bash
git add src/paperconan/_audit.py
git commit -m "refactor(audit): run the whole engine on Sheet via from_rows bridge (behavior locked)"
```

---

## Task 6: Streaming xlsx loader → `Sheet`

Now the memory win: stop materializing the full list-of-lists; fill the Sheet arrays while iterating rows.

**Files:**
- Modify: `src/paperconan/_audit.py:153` (`load_workbook_rows`), `:237` (`load_table`)
- Test: `tests/test_columnar_loader.py` (new)

- [ ] **Step 1: Write tests**

```python
# tests/test_columnar_loader.py
import numpy as np
import openpyxl
from paperconan._audit import load_workbook_rows
from paperconan._sheet import Sheet

def _write_xlsx(path, rows, sheet_name="S1"):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = sheet_name
    for row in rows:
        ws.append(row)
    wb.save(path)

def test_load_returns_sheets(tmp_path):
    p = tmp_path / "a.xlsx"
    _write_xlsx(p, [["H1", "H2"], [1, 2.5], [3, 4.0]])
    out = load_workbook_rows(str(p))
    s = out["S1"]
    assert isinstance(s, Sheet)
    assert s.cell(0, 0) == "H1"
    assert s.cell(1, 0) == 1 and isinstance(s.cell(1, 0), int)
    assert s.cell(1, 1) == 2.5
    assert s.nrows == 3 and s.ncols == 2

def test_oversized_sheet_is_none(tmp_path, monkeypatch):
    import paperconan._audit as A
    monkeypatch.setattr(A, "_MAX_CELLS", 5)
    p = tmp_path / "big.xlsx"
    _write_xlsx(p, [[i, i, i] for i in range(10)])   # 30 cells > 5
    out = load_workbook_rows(str(p))
    assert out["S1"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_columnar_loader.py -q`
Expected: FAIL (`load_workbook_rows` currently returns list-of-lists, not `Sheet`).

- [ ] **Step 3: Rewrite `load_workbook_rows` to stream into a `Sheet`**

```python
def load_workbook_rows(path):
    """Return {sheet_name: Sheet}. A sheet over _MAX_CELLS (alone or via the
    cumulative per-file budget) is returned as None (oversized), preserving the
    legacy memory guard."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = {}
    loaded = 0
    for s in wb.sheetnames:
        ws = wb[s]
        mr, mc = ws.max_row or 0, ws.max_column or 0
        if loaded >= _MAX_CELLS or (mr and mc and mr * mc > _MAX_CELLS):
            out[s] = None
            continue
        # allocate to the declared size; grow columns if a row over-runs.
        nrows, ncols = mr, mc
        numeric = np.full((nrows, ncols), np.nan, dtype=float) if nrows and ncols else np.empty((0, 0))
        text, ints = {}, set()
        r = 0
        cells = 0
        oversized = False
        for row in ws.iter_rows(values_only=True):
            if r >= nrows:                      # openpyxl under-reported rows: grow
                numeric = np.vstack([numeric, np.full((1, numeric.shape[1]), np.nan)])
                nrows += 1
            for c, v in enumerate(row):
                if c >= numeric.shape[1]:       # row wider than declared: grow cols
                    pad = np.full((numeric.shape[0], c + 1 - numeric.shape[1]), np.nan)
                    numeric = np.hstack([numeric, pad])
                if _is_num_local(v):
                    numeric[r, c] = float(v)
                    if isinstance(v, int) and not isinstance(v, bool):
                        ints.add((r, c))
                elif v is not None:
                    text[(r, c)] = v
            cells += len(row)
            if loaded + cells > _MAX_CELLS:
                oversized = True
                break
            r += 1
        if oversized:
            out[s] = None
            continue
        loaded += cells
        out[s] = Sheet(numeric.shape[0], numeric.shape[1], numeric, text, ints)
    wb.close()
    return out
```

Use the module's existing `is_num` for `_is_num_local` (import or alias — `is_num` is already defined in `_audit.py`). Keep `ncols` consistent with `numeric.shape[1]` in the constructed `Sheet`.

- [ ] **Step 4: Update `load_table` docstring/dispatch** (it already just dispatches; ensure CSV/PDF/docx still return `{sheet: Sheet}` — handled in Task 7). For now xlsx returns Sheets.

- [ ] **Step 5: Run loader tests + golden + suite**

Run: `python -m pytest tests/test_columnar_loader.py tests/test_golden_columnar.py -q`
Expected: PASS.
Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/paperconan/_audit.py tests/test_columnar_loader.py
git commit -m "perf(audit): stream xlsx rows into Sheet arrays (no list-of-lists materialization)"
```

---

## Task 7: Streaming CSV loader + PDF/docx bridge

**Files:**
- Modify: `src/paperconan/_audit.py:208` (`load_csv_rows`), `:237` (`load_table`)
- Test: append to `tests/test_columnar_loader.py`

- [ ] **Step 1: Write tests**

```python
def test_load_csv_returns_sheet(tmp_path):
    from paperconan._audit import load_csv_rows
    from paperconan._sheet import Sheet
    p = tmp_path / "d.csv"
    p.write_text("a,b\n1,2.5\n3,x\n")
    out = load_csv_rows(str(p), delimiter=",")
    s = out["d"]
    assert isinstance(s, Sheet)
    assert s.cell(0, 0) == "a"
    assert s.cell(1, 0) == 1 and isinstance(s.cell(1, 0), int)
    assert s.cell(1, 1) == 2.5
    assert s.cell(2, 1) == "x"

def test_pdf_docx_bridge_wraps_rows(tmp_path):
    # load_table must return Sheets for every extension. Use the tiny_paper pdf
    # fixture if present; otherwise assert load_table on a csv yields a Sheet.
    from paperconan._audit import load_table
    from paperconan._sheet import Sheet
    p = tmp_path / "d.csv"; p.write_text("x\n1\n2\n3\n")
    out = load_table(str(p))
    assert all(v is None or isinstance(v, Sheet) for v in out.values())
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_columnar_loader.py -q -k "csv or bridge"`
Expected: FAIL.

- [ ] **Step 3: Stream CSV into a Sheet; bridge PDF/docx**

Rewrite `load_csv_rows` to coerce each cell and fill a Sheet (two-pass: collect coerced rows with the existing `_MAX_CELLS` bail, then `Sheet.from_rows` — CSV is already line-streamed and the cap bounds size, so `from_rows` is acceptable here):

```python
def load_csv_rows(path, delimiter):
    stem = os.path.splitext(os.path.basename(path))[0]
    rows = []
    oversized = False
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            rows, cells = [], 0
            with open(path, newline="", encoding=enc) as fh:
                for r in _csv.reader(fh, delimiter=delimiter):
                    rows.append([_coerce_cell(c) for c in r])
                    cells += len(r)
                    if cells > _MAX_CELLS:
                        oversized = True
                        break
            break
        except UnicodeDecodeError:
            continue
    if oversized:
        return {stem: None}
    return {stem: Sheet.from_rows(rows)}
```

In `load_table`, wrap the PDF/docx extractor outputs (which yield `{name: rows}`) so each value becomes a Sheet:

```python
    if ext == ".pdf":
        from ._extract import load_pdf_tables
        return {k: (None if v is None else Sheet.from_rows(v))
                for k, v in load_pdf_tables(path).items()}
    if ext == ".docx":
        from ._extract import load_docx_tables
        return {k: (None if v is None else Sheet.from_rows(v))
                for k, v in load_docx_tables(path).items()}
```

- [ ] **Step 4: Run tests + suite**

Run: `python -m pytest tests/test_columnar_loader.py tests/test_extract.py tests/test_golden_columnar.py -q`
Expected: PASS.
Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_audit.py tests/test_columnar_loader.py
git commit -m "perf(audit): CSV streams into Sheet; PDF/docx bridged via from_rows"
```

---

## Task 8: Optional calamine accelerator

**Files:**
- Modify: `src/paperconan/_audit.py` (`load_workbook_rows`)
- Modify: `pyproject.toml` (optional extra `[project.optional-dependencies] fast = ["python-calamine>=0.2"]`)
- Test: append to `tests/test_columnar_loader.py`

- [ ] **Step 1: Write a reader-parity test (skips if calamine absent)**

```python
def test_calamine_matches_openpyxl(tmp_path):
    calamine = __import__("importlib").util.find_spec("python_calamine")
    if calamine is None:
        import pytest; pytest.skip("python-calamine not installed")
    import paperconan._audit as A
    p = tmp_path / "a.xlsx"
    _write_xlsx(p, [["H", "K"], [1, 2.5], [3, 4.0], [5, 6.25]])
    via_cal = A.load_workbook_rows(str(p))            # uses calamine path
    monkey = A._load_workbook_openpyxl(str(p))        # explicit openpyxl reference
    for name in via_cal:
        a, b = via_cal[name], monkey[name]
        assert (a is None) == (b is None)
        if a is not None:
            assert np.array_equal(np.nan_to_num(a.numeric), np.nan_to_num(b.numeric))
            assert {k: str(v) for k, v in a._text.items()} == {k: str(v) for k, v in b._text.items()}
```

- [ ] **Step 2: Run (expect skip or fail)**

Run: `python -m pytest tests/test_columnar_loader.py -q -k calamine`
Expected: SKIP if calamine absent; FAIL if present (no `_load_workbook_openpyxl` yet).

- [ ] **Step 3: Split the openpyxl path out + add a calamine fast path**

Rename the Task-6 body to `_load_workbook_openpyxl(path)`. Add `_load_workbook_calamine(path)` building the same `Sheet` from `python_calamine.CalamineWorkbook.from_path(path)` (iterate `ws.to_python(skip_empty_area=False)` rows, same fill loop + same cap/oversize logic). Then:

```python
def load_workbook_rows(path):
    try:
        import python_calamine  # noqa: F401
    except Exception:
        return _load_workbook_openpyxl(path)
    try:
        return _load_workbook_calamine(path)
    except Exception:
        return _load_workbook_openpyxl(path)   # any reader quirk → reference path
```

- [ ] **Step 4: Run tests + golden + suite (with and ideally without calamine installed)**

Run: `python -m pytest tests/test_columnar_loader.py tests/test_golden_columnar.py -q`
Expected: PASS.
Run: `pip install python-calamine && python -m pytest tests/test_columnar_loader.py -q -k calamine`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paperconan/_audit.py tests/test_columnar_loader.py pyproject.toml
git commit -m "perf(audit): optional python-calamine xlsx fast path (openpyxl fallback)"
```

---

## Task 9: Benchmark + raise the cell cap

**Files:**
- Create: `scripts/bench_columnar.py`
- Modify: `src/paperconan/_audit.py:1107` (`_MAX_CELLS` default)

- [ ] **Step 1: Write the benchmark**

```python
# scripts/bench_columnar.py
"""Peak-RSS + wall-time for scan_dir on a directory. Run on `main` and on
`columnar-engine` and compare. Usage: python scripts/bench_columnar.py <dir>"""
import resource, sys, time, tempfile
from paperconan._audit import scan_dir

d = sys.argv[1]
t0 = time.perf_counter()
with tempfile.TemporaryDirectory() as out:
    scan_dir(d, out, write_md=False, write_html=False)
dt = time.perf_counter() - t0
peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
peak_mb = peak_kb / (1024 if sys.platform == "darwin" else 1) / 1024
print(f"scan {d}: {dt:.1f}s  peak_rss={peak_mb:.0f}MB")
```

- [ ] **Step 2: Measure on both branches**

Run on a large fixture (use a real downloaded paper dir, or the largest under `tests/fixtures`):
`python scripts/bench_columnar.py <big-dir>` on `git stash`/`main` vs `columnar-engine`.
Record both numbers in the commit message. Expected: peak RSS down ≥4× on the branch.

- [ ] **Step 3: Raise the default cell cap**

Change `_audit.py:1107`:

```python
_MAX_CELLS = int(os.environ.get("PAPERCONAN_MAX_CELLS", "10000000"))
```

- [ ] **Step 4: Confirm no OOM at the higher cap**

Run: `python scripts/bench_columnar.py <big-dir>`
Expected: completes; peak RSS still well under a 16 GB box. If `test_oversized_guard` asserts the old default, update it to set the env var explicitly rather than relying on the default.

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_columnar.py src/paperconan/_audit.py
git commit -m "perf(audit): raise _MAX_CELLS default to 10M (array substrate bounds memory); add benchmark"
```

---

## Self-Review Notes

- **Spec coverage:** Sheet (T2) ✓, streaming loader (T6/T7) ✓, optional calamine (T8) ✓, golden lock (T1) ✓, int fidelity via `_ints`/`cell()` ✓, raise cap (T9) ✓, NaN sentinel semantics (T2 tests) ✓.
- **Type consistency:** every accessor and detector takes a `Sheet` after T5; `col_array`/`find_numeric_blocks`/`header_for`/`_block_evidence`/`_grid_from_rows` signatures all switched to `sheet`. `_attach_evidence` switched in T5.
- **Direct-index detectors** (`detect_equal_pairs`, `detect_identical_after_rounding`, `detect_grim_grimmer`) each have an explicit swap step in T5 — they are NOT in the "frozen, unchanged" set.
- **Ordering:** golden lock precedes all logic changes; substrate swap (T3–T5) precedes the loader rewrite (T6–T8), so correctness is locked before the memory optimization.
