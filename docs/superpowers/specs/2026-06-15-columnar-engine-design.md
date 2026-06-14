# Columnar Engine Design

**Date:** 2026-06-15
**Status:** Approved (approach A + two defaults), pending spec review
**Branch:** `columnar-engine`

## Goal

Replace paperconan's audit data substrate so a sheet is held as native numeric
arrays instead of a Python list-of-lists of cell objects. This cuts peak memory
~6× and removes the per-cell Python loops in the hot path, so the giant
genomics/proteomics supplementary tables that currently OOM (or force tight
cell caps) audit cleanly. **Detector logic and detector output are unchanged.**

## Background: why the substrate is the bottleneck

`scan_dir` loads each file to `{sheet_name: rows}` where `rows` is
`list[list]` of Python cell objects (`load_workbook_rows`, openpyxl
`read_only`). Every detector then pulls each column with
`col_array(rows, r0, r1, c)` → a `numpy` float array (NaN for non-numeric) and
operates purely on that array (`detect_relations`, `detect_arithmetic_progression`,
`detect_within_column_patterns`, `detect_identical_after_rounding`,
`detect_grim_grimmer`, `detect_equal_pairs`). `find_numeric_blocks` and
`header_for` also walk `rows` cell-by-cell.

Two costs, both in the substrate, not the logic:

1. **Memory.** A 1M-cell sheet as `list[list]` of boxed Python objects is
   ~50 MB+ and GC-pressured; the *whole* list-of-lists for every sheet is
   resident at once. The same data as an `R×C float64` array is 8 MB,
   contiguous, no GC.
2. **CPU.** `col_array` and `find_numeric_blocks` are Python `for`-loops over
   cells. On the array substrate they become slices and vectorized
   `~np.isnan`.

Because the detectors already reduce columns to numpy arrays, swapping the
substrate captures nearly all of the win with the detector bodies untouched.

## Approach A (chosen): columnar substrate behind a `Sheet` object

Introduce a `Sheet` value object that replaces `rows`. Reimplement the ~6
substrate accessors against it; leave every detector body unchanged. xlsx
reading optionally accelerated by `python-calamine`, falling back to openpyxl
when it is not installed (no new hard dependency).

Rejected alternatives:
- **B — full polars rewrite** (DataFrame per sheet, detectors as polars ops):
  much more work and risk for marginal extra gain (detectors are already
  numpy), plus a heavy hard dependency.
- **C — stream the loader but keep list-of-lists**: a half-measure that fixes
  neither `col_array`'s Python loop nor the per-object overhead.

## The `Sheet` abstraction

A `Sheet` represents one loaded sheet/table.

```
class Sheet:
    nrows: int
    ncols: int
    numeric: np.ndarray          # shape (nrows, ncols), float64, NaN = empty-or-non-numeric
    text: dict[tuple[int,int], object]   # sparse: only cells that are NOT numeric and NOT None
                                         # value is the original cell object (str / datetime / bool)
```

Reconstruction of the original cell value at `(r, c)` — the single rule every
accessor and the evidence builder uses:

```
def cell(self, r, c):
    v = self.numeric[r, c]
    if not isnan(v):          # numeric cell
        return v               # int-vs-float distinction: see below
    return self.text.get((r, c))   # text/date/bool, else None (empty)
```

**int vs float fidelity.** Current code coerces cells to float in `col_array`
but the *evidence* path (`_cell_value`) preserves the original int/float. To
keep evidence byte-identical, integer-valued cells whose original type was
`int` must still serialize as ints. The numeric array is float64, so we cannot
recover "was it an int" from the array alone. Resolution: store an `int_mask`
(boolean `R×C`, or a sparse set of `(r,c)`) marking cells whose original value
was a Python `int`, and have `cell()`/`_cell_value` return `int(v)` for those.
This is built for free during the streaming fill (we see each cell's type
once).

**Why a dense `numeric` array is safe.** The per-sheet cell cap (`_MAX_CELLS`,
1M) bounds `nrows*ncols`, so the array is ≤ 8 MB regardless of sparsity. The
existing oversized→`None` path is preserved for sheets over the cap.

## Streaming loader

The memory fix is to never hold the whole list-of-lists. The loaders iterate
the source row-by-row, write each cell into the preallocated arrays, and drop
the transient Python row immediately.

- `load_workbook_rows(path)`: try `python-calamine` first (Rust reader, fast,
  low-mem); fall back to openpyxl `read_only` `iter_rows`. Single pass:
  dimensions are known before iterating (openpyxl exposes `ws.max_row`/
  `ws.max_column`; calamine exposes the sheet's total size), so allocate the
  arrays up front and fill while streaming rows — never holding the full
  list-of-lists. If a stray row exceeds the declared width (openpyxl can
  under-report), grow the column dimension on demand. Cumulative cell budget
  (`loaded >= _MAX_CELLS`) and per-sheet oversize → `None` semantics are
  preserved exactly.
- `load_csv_rows(path, delimiter)`: same streaming fill; `_coerce_cell`'s
  int/float/text decision feeds `numeric` + `int_mask` + `text`.
- `load_table(path)`: unchanged dispatch; PDF/docx extractors (`_extract.py`)
  return `rows` today — wrap their output into a `Sheet` via a
  `Sheet.from_rows(rows)` constructor so those paths need no logic change.

`Sheet.from_rows(rows)` is also the bridge the tests and the collision-grid
builder use, and keeps a single construction code path.

## Changed surface (and what stays frozen)

**Reimplemented against `Sheet` (≈6 functions):**
- `col_array(sheet, r0, r1, c)` → `sheet.numeric[r0:r1, c].copy()` (a slice;
  no loop, no per-cell `to_float`).
- `find_numeric_blocks(sheet, ...)` → build the `num` boolean matrix as
  `~np.isnan(sheet.numeric)` (vectorized), block-walk unchanged.
- `header_for(sheet, r0, c0, c1)` → uses `sheet.cell(r, c)`.
- `_block_evidence(sheet, ...)` / `_cell_value` → uses `sheet.cell(r, c)` with
  int fidelity.
- collision path `_grid_from_rows` / `_column_cells` → build the decimal grid
  from `sheet` (numeric array + int_mask) instead of `rows`.
- `scan_dir` → threads `Sheet` through; oversized (`None`) branch unchanged.

**Frozen (no logic change):** the six detector bodies. Their signature param
renames `rows`→`sheet`, but every internal call already goes through
`col_array`/`header`, so the bodies are untouched.

## Validation strategy (the correctness gate)

This is a behavior-preserving rewrite; the test plan locks behavior.

1. **Golden snapshot.** Before changing any logic, add a test that runs the
   current engine on the oracle fixtures (`tiny_paper`, `scu_biomed_nature_2024_present`
   sample, `zssom_*`, `chujie_chen_public_data`) and pins their `scan.json`
   findings as golden files. The rewrite must reproduce them **exactly** — same
   `kind`, same column/row coords, same `severity`, same numeric rule params,
   same evidence cells. Any diff fails the build.
2. **Existing suite stays green.** All current tests (`test_smoke`,
   `test_grim`, `test_grim_e2e`, `test_collisions`, `test_relations_flood`,
   `test_benign`, `test_cell_guard`, `test_oversized_guard`, `test_profiles`,
   `test_packaging`, `test_public_api`, `test_module_boundaries`, …) pass
   unchanged.
3. **Substrate unit tests.** New `test_sheet.py`: `Sheet` round-trips cell
   values (int stays int, float stays float, text/date/bool preserved, empty →
   None); `cell()` reconstruction; oversized → `None`; `from_rows` parity.
4. **Benchmark.** Pick one real paper that currently OOMs or hits the cell cap;
   measure peak RSS + wall-time on `main` vs `columnar-engine`. Record in the
   PR. Demonstrate the cap can be raised.

## Success metrics

- Oracle `scan.json` byte-identical (golden test green).
- Full existing suite green.
- Peak RSS on the benchmark paper down ≥4× (target ~6×); wall-time down.
- `_MAX_CELLS` raisable (e.g. 1M → 5–10M) without OOM on a 16 GB box — the
  prerequisite for backfill scale-up.

## Out of scope

- Rewriting detectors as polars/vectorized ops (approach B).
- Changing detector thresholds, profiles, or output schema.
- The pcwatch orchestration / fetch / triage layers (separate repo).
- Multi-threaded sheet loading.

## Risks & mitigations

- **int/float evidence drift** → `int_mask` + golden snapshot catches any diff.
- **calamine type quirks** (dates, booleans, error cells) → calamine is an
  optional accelerator; the openpyxl fallback path is the reference, and the
  golden test runs against whichever reader is active.
- **NaN vs genuine NaN in source** → source NaN/inf already filtered by
  `is_num` today (treated as non-numeric); the array uses NaN as the
  empty/non-numeric sentinel, matching that semantics. Verified by the
  round-trip test.
