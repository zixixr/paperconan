#!/usr/bin/env python3
"""
paper_audit.py — scan published source data for statistical signals and data inconsistencies.

Usage:
    python3 paper_audit.py <dir-with-xlsx-files> [--out OUT_DIR]

Outputs to <OUT_DIR or <dir>/audit>:
  - scan.json   structured findings (every block, every detector)
  - REPORT.md   ranked top-5 + supporting evidence in markdown

What it detects (signals requiring contextual review):
  1. Identical / constant-offset / constant-ratio / exact-linear column relations
  2. Arithmetic-progression columns (constant first difference)
  3. Repeated last-two-decimal endings beyond chance
  4. Last-digit chi-square departures from an approximately uniform reference
  5. Row pairs that sum to integers / equal-value column pairs
  6. Candidate arithmetic relationships (col_b = col_a + k, col_b = K - col_a, etc.)

Dependencies: openpyxl, numpy, scipy
"""
from __future__ import annotations
import argparse
from bisect import bisect_left
import csv as _csv
import datetime
import heapq
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations

import openpyxl
import numpy as np
from scipy import stats

from ._coverage import ScanCoverage
from ._input import (
    InputLimitation,
    TableLoadResult,
    discover_supported_inputs,
    inspect_ooxml_formula_cache,
)
from ._numeric import (
    assess_relation_intercept,
    integer_shift_close,
    max_ulp_tolerance,
    relation_close,
    scalar_ulp_tolerance,
)
from ._profiles import apply_profile_to_findings, normalize_profile
from ._resources import (
    BoundedFindingCollector,
    StateBudget,
    state_units_for_nbytes,
)
from ._sheet import (
    Sheet,
    SheetBuilder,
    SheetBuildLimit,
    _MAX_EXACT_FLOAT_INT,
)
from ._source_sidecar import SidecarLimitError, read_sidecar
from ._summaries import (
    ColumnFingerprint,
    CrossSheetSummary,
    RecurringRowIndex,
    SparseLabelContext,
)
from .schema import PaperconanInputError

# Canonical list of the per-block finding-group keys emitted into every
# `relations_blocks[]` entry (see scan_dir's report_blocks.append). This is the
# SINGLE SOURCE OF TRUTH: the markdown report, the packet distiller, and the
# paperconan-watch severity counters / triage gate all iterate this set, so a
# HIGH finding in ANY group (notably row_pairs) is counted and can reach review.
BLOCK_FINDING_GROUPS = (
    "relations", "equal_pairs", "progressions", "row_pairs",
    "within_col", "identical_after_rounding", "grim",
)


def _version():
    """paperconan version, resolved lazily to avoid an import cycle with __init__."""
    try:
        from . import __version__
        return __version__
    except Exception:
        return "unknown"


# ---------- value helpers ----------

def is_num(x):
    if x is None or isinstance(x, bool):
        return False
    if isinstance(x, (int, float)):
        return not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))
    return False


def to_float(x):
    return float(x) if is_num(x) else None


def last_significant_digit(x):
    if x is None or x == 0:
        return None
    s = f"{x:.10g}"
    digits = [c for c in s if c.isdigit()]
    return digits[-1] if digits else None


def trailing_decimal_digits(x, k=2):
    if x is None:
        return None
    try:
        s = repr(float(x))
    except (TypeError, ValueError):
        return None
    if "e" in s or "E" in s or "." not in s:
        return None
    frac = s.split(".", 1)[1]
    return frac[-k:] if len(frac) >= k else None


def _decimals_of(x, cap=6):
    """Number of significant decimal places in x's shortest float repr, capped.

    Cells are coerced to float on load, so displayed trailing zeros are lost.
    Recovering decimals from the float repr therefore UNDER-counts precision for
    values like 2.50 -> 2.5. That is conservatively safe for GRIM: fewer decimals
    means a coarser grid and fewer flags, never a false flag."""
    s = repr(float(x))
    if "e" in s or "E" in s:
        return cap  # scientific notation: assume high precision (conservative)
    if "." not in s:
        return 0
    frac = s.split(".", 1)[1].rstrip("0")
    return min(len(frac), cap)


def grim_consistent(mean, n, decimals):
    """True if `mean`, reported to `decimals` places, is achievable as an integer
    total divided by `n`. Conservative: any bracketing integer total that rounds
    back to the reported mean counts as consistent (tolerant of the rounding
    convention used by the authors)."""
    if n <= 0:
        return True
    scale = 10 ** decimals
    target = round(mean * scale)
    base = mean * n
    for t in (math.floor(base), math.ceil(base), round(base)):
        if round((t / n) * scale) == target:
            return True
    return False


_GRIMMER_MAX_STATES = 200_000
_GRIMMER_MAX_CANDIDATES = 200_000


def _candidate_integer_totals(mean, n, decimals):
    scale = 10 ** decimals
    target = round(mean * scale)
    lo = (target - 0.5) / scale
    hi = (target + 0.5) / scale
    return [
        total for total in range(math.floor(lo * n) - 1,
                                 math.ceil(hi * n) + 2)
        if round((total / n) * scale) == target
    ]


def _candidate_moment_bounds(total, sd, n, decimals, ddof):
    denom = n - ddof
    if denom <= 0:
        return None
    scale = 10 ** decimals
    target = round(sd * scale)
    edge_scale = 2 * scale
    lower_edge = max(0, 2 * target - 1)
    upper_edge = 2 * target + 1
    moment_factor = n * denom
    squared_edge_scale = edge_scale * edge_scale
    first = (
        lower_edge * lower_edge * moment_factor
    ) // squared_edge_scale
    last_numerator = upper_edge * upper_edge * moment_factor
    last = (
        last_numerator + squared_edge_scale - 1
    ) // squared_edge_scale

    residue = (-(total * total)) % n
    first += (residue - first) % n
    last -= (last - residue) % n
    return first, last


def _candidate_moment_count(bounds, n):
    if bounds is None:
        return 0
    first, last = bounds
    if first > last:
        return 0
    return ((last - first) // n) + 1


def _candidate_sum_squares(total, sd, n, decimals, ddof):
    bounds = _candidate_moment_bounds(total, sd, n, decimals, ddof)
    if bounds is None:
        return []
    if _candidate_moment_count(bounds, n) > _GRIMMER_MAX_CANDIDATES:
        return []

    denom = n - ddof
    scale = 10 ** decimals
    target = round(sd * scale)
    total_squared = total * total
    first, last = bounds
    out = []
    for moment_numerator in range(first, last + 1, n):
        candidate_sd = math.sqrt(
            moment_numerator / (n * denom)
        )
        if round(candidate_sd * scale) == target:
            sum_squares = (moment_numerator + total_squared) // n
            out.append(sum_squares)
    return out


def _integer_moments_reachable(total, sum_squares, n, *,
                               max_states=None):
    if max_states is None:
        max_states = _GRIMMER_MAX_STATES
    if n <= 0 or sum_squares < 0:
        return False
    if total * total > n * sum_squares:
        return False
    if (sum_squares - total) % 2:
        return False

    base = math.floor(total / n)
    shifted_sum = total - n * base
    shifted_squares = (
        sum_squares - 2 * base * total + n * base * base
    )
    if shifted_squares < 0:
        return False

    if n == 1:
        return shifted_squares == shifted_sum * shifted_sum
    if n == 2:
        discriminant = 2 * shifted_squares - shifted_sum * shifted_sum
        if discriminant < 0:
            return False
        root = math.isqrt(discriminant)
        return (
            root * root == discriminant
            and (shifted_sum + root) % 2 == 0
        )

    radius = math.isqrt(shifted_squares)
    values = range(-radius, radius + 1)
    states = {(0, 0)}
    for used in range(n):
        remaining = n - used - 1
        next_states = set()
        for partial_sum, partial_sq in states:
            for value in values:
                new_sum = partial_sum + value
                new_sq = partial_sq + value * value
                if new_sq > shifted_squares:
                    continue
                sum_left = shifted_sum - new_sum
                sq_left = shifted_squares - new_sq
                if remaining == 0:
                    if sum_left == 0 and sq_left == 0:
                        return True
                    continue
                if sum_left * sum_left > remaining * sq_left:
                    continue
                next_states.add((new_sum, new_sq))
                if len(next_states) > max_states:
                    return None
        states = next_states
        if not states:
            return False
    return False


def grimmer_consistent(mean, sd, n, mean_decimals, sd_decimals):
    if n <= 1 or sd < 0:
        return True
    unknown = False
    for total in _candidate_integer_totals(mean, n, mean_decimals):
        for ddof in (1, 0):
            bounds = _candidate_moment_bounds(
                total, sd, n, sd_decimals, ddof
            )
            if (
                _candidate_moment_count(bounds, n)
                > _GRIMMER_MAX_CANDIDATES
            ):
                unknown = True
                continue
            for sum_squares in _candidate_sum_squares(
                total, sd, n, sd_decimals, ddof
            ):
                reachable = _integer_moments_reachable(
                    total, sum_squares, n
                )
                if reachable is True:
                    return True
                if reachable is None:
                    unknown = True
    return True if unknown else False


# ---------- sheet I/O ----------

def _dense_cells(row_count, max_width):
    return row_count * max_width


def _sheet_build_limitation(error, sheet_name):
    details = error.limitation_details()
    if error.reason == "cell_limit":
        details["max_cells"] = _MAX_CELLS
    return InputLimitation(
        scope="sheet",
        reason=error.reason,
        sheet=sheet_name,
        details=details,
    )


def _record_preflight_cell_limit(
    limitation_sink, sheet_name, declared
):
    if limitation_sink is None:
        return
    error = SheetBuildLimit(
        "cell_limit",
        cells=declared,
        observed_sparse_cells=0,
        observed_sparse_bytes=0,
        max_sparse_cells=_MAX_SPARSE_CELLS,
        max_sparse_bytes=_MAX_SPARSE_BYTES,
    )
    limitation_sink.append(
        _sheet_build_limitation(error, sheet_name)
    )


def _fill_sheet_from_rows(
    rows_iter,
    mr,
    mc,
    loaded,
    *,
    limitation_sink=None,
    sheet_name=None,
):
    """Stream rows of openpyxl-shaped cell values (int/float/str/datetime/bool/None)
    into a Sheet, honouring the cumulative `_MAX_CELLS` budget that `loaded` cells
    have already consumed across this file.

    Returns (sheet_or_None, cells): None means the per-file cumulative budget was
    exceeded mid-stream (oversized). Both readers (openpyxl, calamine) funnel through
    this so they produce a byte-identical Sheet; the calamine path normalizes its
    typed values to openpyxl's shape BEFORE calling here.

    The produced Sheet matches Sheet.from_rows of the same rows: nrows == rows
    consumed, ncols == max row width seen (trailing all-empty rows/cols are kept as
    NaN padding, not trimmed). `mr`/`mc` are only the pre-allocation hint; the array
    grows on demand if a reader under-declares dimensions."""
    declared = _dense_cells(mr, mc)
    if loaded >= _MAX_CELLS or loaded + declared > _MAX_CELLS:
        error = SheetBuildLimit(
            "cell_limit",
            cells=declared,
            observed_sparse_cells=0,
            observed_sparse_bytes=0,
            max_sparse_cells=_MAX_SPARSE_CELLS,
            max_sparse_bytes=_MAX_SPARSE_BYTES,
        )
        if limitation_sink is not None:
            limitation_sink.append(
                _sheet_build_limitation(error, sheet_name)
            )
        return None, declared
    try:
        builder = SheetBuilder(
            declared_rows=mr,
            declared_cols=mc,
            loaded_cells=loaded,
            max_cells=_MAX_CELLS,
            max_sparse_cells=_MAX_SPARSE_CELLS,
            max_sparse_bytes=_MAX_SPARSE_BYTES,
        )
        for row in rows_iter:
            builder.append_row(row)
        sheet = builder.finish()
    except SheetBuildLimit as error:
        if limitation_sink is not None:
            limitation_sink.append(
                _sheet_build_limitation(error, sheet_name)
            )
        return None, error.cells
    return sheet, builder.cells


def _load_workbook_openpyxl(path, *, _limitations=None):
    """Return dict of sheet_name -> Sheet via openpyxl (the reference reader). A sheet
    over _MAX_CELLS (on its own, or once this file's cumulative cell budget is spent)
    is returned as None (oversized), preserving the legacy memory guard. Rows stream
    directly into the Sheet's columnar arrays — the full list-of-lists is never
    materialized."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        out = {}
        loaded = 0                                   # cumulative cells across this file's sheets
        for s in wb.sheetnames:
            ws = wb[s]
            mr, mc = ws.max_row or 0, ws.max_column or 0
            declared = _dense_cells(mr, mc)
            # Skip a sheet that is too big on its own, OR once this file's cumulative cell budget is
            # spent (a many-sheet workbook materialized at once OOMs even if each sheet is under cap).
            if loaded >= _MAX_CELLS or loaded + declared > _MAX_CELLS:
                _record_preflight_cell_limit(
                    _limitations, s, declared
                )
                out[s] = None
                continue
            fill_kwargs = {}
            if _limitations is not None:
                fill_kwargs = {
                    "limitation_sink": _limitations,
                    "sheet_name": s,
                }
            sheet, cells = _fill_sheet_from_rows(
                ws.iter_rows(values_only=True),
                mr,
                mc,
                loaded,
                **fill_kwargs,
            )
            out[s] = sheet
            if sheet is not None:
                loaded += cells
    except BaseException:
        try:
            wb.close()
        except BaseException:
            pass
        raise
    else:
        wb.close()
        return out


def _calamine_cell(v):
    """Normalize one python_calamine typed value to the shape openpyxl's read_only
    reader produces, so a Sheet built from calamine rows is byte-identical:
      - "" (calamine's empty cell) -> None (openpyxl yields None for empty cells)
      - whole-number float -> int (openpyxl coerces every integral value to int)
      - datetime.date -> datetime.datetime at midnight (openpyxl never yields bare date)
    bool / str / datetime / non-integral float pass through unchanged."""
    if isinstance(v, bool):
        return v
    if v == "":
        return None
    if isinstance(v, float):
        if math.isfinite(v) and v == int(v):
            return int(v)
        return v
    if isinstance(v, datetime.datetime):
        return v
    if isinstance(v, datetime.date):
        return datetime.datetime(v.year, v.month, v.day)
    return v


_CALAMINE_OPENPYXL_FALLBACK = object()
_CALAMINE_READER_ERROR = object()


def _load_workbook_calamine_scoped(path, *, _limitations=None):
    """Read with Calamine, returning a fallback signal before openpyxl is opened.

    Keeping Calamine state in this helper ensures its workbook, sheets, row iterators,
    and any partial output leave scope before the outer loader starts openpyxl.
    """
    import python_calamine
    wb = python_calamine.CalamineWorkbook.from_path(path)
    out = {}
    loaded = 0                                       # cumulative cells across this file's sheets
    is_ooxml = os.path.splitext(path)[1].lower() in {".xlsx", ".xlsm"}
    for name in wb.sheet_names:
        sh = wb.get_sheet_by_name(name)
        h, w = sh.height, sh.width
        declared = _dense_cells(h, w)
        if loaded >= _MAX_CELLS or loaded + declared > _MAX_CELLS:
            _record_preflight_cell_limit(
                _limitations, name, declared
            )
            out[name] = None
            continue
        wide_ooxml_integer = False

        def normalized_rows():
            nonlocal wide_ooxml_integer
            for row in sh.iter_rows():
                normalized = []
                for value in row:
                    if (
                        is_ooxml
                        and isinstance(value, float)
                        and math.isfinite(value)
                        and value.is_integer()
                        and abs(value) >= _MAX_EXACT_FLOAT_INT
                    ):
                        wide_ooxml_integer = True
                    normalized.append(_calamine_cell(value))
                yield normalized

        fill_kwargs = {}
        if _limitations is not None:
            fill_kwargs = {
                "limitation_sink": _limitations,
                "sheet_name": name,
            }
        sheet, cells = _fill_sheet_from_rows(
            normalized_rows(), h, w, loaded, **fill_kwargs
        )
        out[name] = sheet
        if sheet is not None:
            if wide_ooxml_integer:
                return _CALAMINE_OPENPYXL_FALLBACK
            loaded += cells
    return out


def _load_workbook_calamine(path, *, _limitations=None):
    """Return dict of sheet_name -> Sheet via python-calamine (a fast Rust reader),
    producing a Sheet byte-identical to _load_workbook_openpyxl. Same _MAX_CELLS
    per-sheet + cumulative guard, same oversized->None, same trim-to-max-width."""
    pending_limitations = []
    result = _load_workbook_calamine_scoped(
        path,
        _limitations=(
            pending_limitations
            if _limitations is not None
            else None
        ),
    )
    if result is _CALAMINE_OPENPYXL_FALLBACK:
        if _limitations is None:
            return _load_workbook_openpyxl(path)
        return _load_workbook_openpyxl(
            path, _limitations=_limitations
        )
    if _limitations is not None:
        _limitations.extend(pending_limitations)
    return result


def _try_load_workbook_calamine(path, *, _limitations=None):
    """Return a detached error signal after Calamine exception state unwinds."""
    try:
        return _load_workbook_calamine(
            path, _limitations=_limitations
        )
    except Exception:
        return _CALAMINE_READER_ERROR


def load_workbook_rows(path, *, _limitations=None):
    """Return dict of sheet_name -> Sheet. Uses python-calamine (a fast Rust xlsx
    reader) when installed. OOXML inputs fall back to the openpyxl reference path
    after Calamine errors; legacy inputs remain on Calamine because openpyxl cannot
    read them. Both successful paths produce a byte-identical Sheet."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".xlsx", ".xlsm"}:
        return _load_workbook_calamine(
            path, _limitations=_limitations
        )
    result = _try_load_workbook_calamine(
        path, _limitations=_limitations
    )
    if result is _CALAMINE_READER_ERROR:
        if _limitations is None:
            return _load_workbook_openpyxl(path)
        return _load_workbook_openpyxl(
            path, _limitations=_limitations
        )
    return result


def _coerce_cell(s):
    """Parse a CSV string cell into int / float / text. Empty -> None.
    Deliberately conservative: no thousands separators, no percent, no currency."""
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def load_csv_rows(path, delimiter, *, _limitations=None):
    """Load a delimited text file as {sheet_name: Sheet|None}, mirroring load_workbook_rows.
    A flat file has no sheets, so it becomes a single sheet named after the file stem.
    Oversized (> _MAX_CELLS) -> {stem: None}; otherwise the rows are wrapped in a Sheet."""
    stem = os.path.splitext(os.path.basename(path))[0]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as fh:
                normalized_rows = (
                    [_coerce_cell(cell) for cell in row]
                    for row in _csv.reader(fh, delimiter=delimiter)
                )
                fill_kwargs = {}
                if _limitations is not None:
                    fill_kwargs = {
                        "limitation_sink": _limitations,
                        "sheet_name": stem,
                    }
                sheet, _cells = _fill_sheet_from_rows(
                    normalized_rows,
                    0,
                    0,
                    0,
                    **fill_kwargs,
                )
            break
        except UnicodeDecodeError:
            continue
    return {stem: sheet}


def _load_table_sheets(path, *, _limitations=None):
    """Dispatch by extension to a {sheet_name: Sheet|None} loader."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tsv":
        return load_csv_rows(
            path, delimiter="\t", _limitations=_limitations
        )
    if ext == ".csv":
        return load_csv_rows(
            path, delimiter=",", _limitations=_limitations
        )
    if ext == ".pdf":
        from ._extract import load_pdf_tables
        return load_pdf_tables(
            path,
            max_cells=_MAX_CELLS,
            max_sparse_cells=_MAX_SPARSE_CELLS,
            max_sparse_bytes=_MAX_SPARSE_BYTES,
        )
    if ext == ".docx":
        from ._extract import load_docx_tables
        return load_docx_tables(
            path,
            max_cells=_MAX_CELLS,
            max_sparse_cells=_MAX_SPARSE_CELLS,
            max_sparse_bytes=_MAX_SPARSE_BYTES,
        )
    return load_workbook_rows(path, _limitations=_limitations)


def load_table_result(path) -> TableLoadResult:
    ext = os.path.splitext(path)[1].lower()
    if ext in {".pdf", ".docx"}:
        if ext == ".pdf":
            from ._extract import load_pdf_tables
            loader = load_pdf_tables
        else:
            from ._extract import load_docx_tables
            loader = load_docx_tables
        extracted = loader(
            path,
            max_cells=_MAX_CELLS,
            max_sparse_cells=_MAX_SPARSE_CELLS,
            max_sparse_bytes=_MAX_SPARSE_BYTES,
            with_metadata=True,
        )
        sheets = {
            name: (
                value
                if value is None or isinstance(value, Sheet)
                else Sheet.from_rows(
                    value,
                    max_cells=_MAX_CELLS,
                    max_sparse_cells=_MAX_SPARSE_CELLS,
                    max_sparse_bytes=_MAX_SPARSE_BYTES,
                )
            )
            for name, value in extracted.tables.items()
        }
        limitations = list(extracted.limitations)
    else:
        limitations = []
        sheets = _load_table_sheets(
            path, _limitations=limitations
        )
    for sheet, gap in inspect_ooxml_formula_cache(path).items():
        limitations.append(InputLimitation(
            scope="sheet",
            reason="formula_cache_missing",
            sheet=sheet,
            details={"count": gap["count"], "cells": gap["cells"]},
        ))
    return TableLoadResult(sheets=sheets, limitations=limitations)


def load_table(path) -> dict[str, Sheet | None]:
    """Dispatch by extension to a {sheet_name: Sheet|None} loader."""
    return load_table_result(path).sheets


def _iter_extracted_sheets(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from ._extract import iter_pdf_tables
        loader = iter_pdf_tables
    elif ext == ".docx":
        from ._extract import iter_docx_tables
        loader = iter_docx_tables
    else:
        raise ValueError(f"not an extracted-table input: {ext}")
    yield from loader(
        path,
        max_cells=_MAX_CELLS,
        max_sparse_cells=_MAX_SPARSE_CELLS,
        max_sparse_bytes=_MAX_SPARSE_BYTES,
    )


def find_numeric_blocks(sheet, min_rows=3, min_cols=1):
    R, C = sheet.nrows, sheet.ncols
    if R == 0 or C == 0:
        return []
    num = sheet.numeric_mask()
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
                if (i1 - i0) >= min_rows and (j1 - j) >= min_cols:
                    visited[i0:i1, j:j1] = True
                    blocks.append((i0, i1, j, j1))
            else:
                i += 1
    return blocks


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


def col_array(sheet, r0, r1, c):
    return sheet.numeric[r0:r1, c].copy()


def _sample(arr, k=8):
    """A tiny value peek for downstream LLM triage: the first <=k finite numeric
    values of `arr` as built-in floats rounded to 6 significant figures. Bounded to
    <=k elements so it CANNOT reintroduce the evidence-bloat OOM (~64 bytes here)."""
    out = []
    for v in arr[:k]:
        fv = float(v)
        if math.isnan(fv) or math.isinf(fv):
            continue
        out.append(round(fv, 6))
    return out


# ---------- evidence helpers ----------

def _cell_value(v):
    """JSON-serializable cell value: keep numbers as-is, stringify dates/objects, None stays None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    return str(v)


def _bounded_evidence_indices(start, end, highlights, limit):
    if limit <= 0:
        return []
    logical_count = max(0, end - start)
    if logical_count <= limit:
        return list(range(start, end))
    selected = sorted({
        index for index in highlights
        if start <= index < end
    })[:limit]
    selected_set = set(selected)
    for index in range(start, end):
        if len(selected) >= limit:
            break
        if index not in selected_set:
            selected.append(index)
            selected_set.add(index)
    return sorted(selected)


def _evidence_window(
    sheet,
    r0,
    r1,
    c0,
    header,
    row_indices,
    col_indices,
    highlight_cols,
    highlight_rows,
):
    data_rows = []
    for row_index in row_indices:
        data_rows.append({
            "row_idx": row_index + 1,
            "is_context": row_index < r0 or row_index >= r1,
            "values": [
                _cell_value(sheet.cell(row_index, col_index))
                for col_index in col_indices
            ],
        })
    return {
        "headers": [
            header[col_index - c0]
            for col_index in col_indices
        ],
        "col_offset": col_indices[0] if col_indices else c0,
        "highlight_cols": list(highlight_cols),
        "highlight_rows": list(highlight_rows),
        "rows": data_rows,
        "col_indices": list(col_indices),
    }


def _block_evidence(
    sheet,
    r0,
    r1,
    c0,
    c1,
    header,
    highlight_cols,
    highlight_rows=None,
    highlight_cells=None,
):
    """Slice a numeric block (with 1 row of context above/below if available) into a
    JSON-friendly evidence dict that the HTML renderer can show as a table.

    Each truncated window remains within _MAX_EV_ROWS × _MAX_EV_COLS.
    Additional deterministic windows cover highlights that do not fit in the
    first window without materializing the full highlighted-row by
    highlighted-column cross-product. Small blocks retain the legacy shape."""
    r_start = max(0, r0 - 1)
    r_end = min(sheet.nrows, r1 + 1)
    all_rows = range(r_start, r_end)
    all_cols = range(c0, c1)
    highlight_rows = list(highlight_rows or [])
    normalized_rows = sorted({
        row_number - 1
        for row_number in highlight_rows
        if r_start <= row_number - 1 < r_end
    })
    normalized_cols = sorted({
        col_index
        for col_index in highlight_cols
        if c0 <= col_index < c1
    })
    normalized_cells = sorted({
        (row_index, col_index)
        for row_index, col_index in (highlight_cells or [])
        if (
            r_start <= row_index < r_end
            and c0 <= col_index < c1
        )
    })
    row_limit = max(0, _MAX_EV_ROWS)
    col_limit = max(0, _MAX_EV_COLS)
    truncated = (
        len(all_rows) > row_limit
        or len(all_cols) > col_limit
    )

    if not truncated:
        out = _evidence_window(
            sheet,
            r0,
            r1,
            c0,
            header,
            all_rows,
            all_cols,
            highlight_cols,
            highlight_rows,
        )
        out.pop("col_indices")
        return out

    windows = []
    seen_windows = set()

    def add_window(selected_rows, selected_cols):
        selected_rows = tuple(sorted(selected_rows))
        selected_cols = tuple(sorted(selected_cols))
        key = (selected_rows, selected_cols)
        if (
            not selected_rows
            or not selected_cols
            or key in seen_windows
        ):
            return
        seen_windows.add(key)
        windows.append(_evidence_window(
            sheet,
            r0,
            r1,
            c0,
            header,
            selected_rows,
            selected_cols,
            highlight_cols,
            highlight_rows,
        ))

    base_rows = _bounded_evidence_indices(
        r_start, r_end, normalized_rows, row_limit
    )
    base_cols = _bounded_evidence_indices(
        c0, c1, normalized_cols, col_limit
    )
    add_window(base_rows, base_cols)

    def cell_is_covered(cell):
        row_index, col_index = cell
        return any(
            row_index in {
                row["row_idx"] - 1 for row in window["rows"]
            }
            and col_index in window["col_indices"]
            for window in windows
        )

    pending_cells = [
        cell for cell in normalized_cells
        if not cell_is_covered(cell)
    ]
    while pending_cells and row_limit > 0 and col_limit > 0:
        selected_rows = set()
        selected_cols = set()
        selected_cells = []
        for row_index, col_index in pending_cells:
            next_rows = selected_rows | {row_index}
            next_cols = selected_cols | {col_index}
            if (
                len(next_rows) <= row_limit
                and len(next_cols) <= col_limit
            ):
                selected_rows = next_rows
                selected_cols = next_cols
                selected_cells.append((row_index, col_index))
        if not selected_cells:
            break
        add_window(
            _bounded_evidence_indices(
                r_start, r_end, selected_rows, row_limit
            ),
            _bounded_evidence_indices(
                c0, c1, selected_cols, col_limit
            ),
        )
        selected_cell_set = set(selected_cells)
        pending_cells = [
            cell for cell in pending_cells
            if cell not in selected_cell_set
        ]

    covered_rows = {
        row["row_idx"] - 1
        for window in windows
        for row in window["rows"]
    }
    uncovered_rows = [
        row_index for row_index in normalized_rows
        if row_index not in covered_rows
    ]
    for start in range(0, len(uncovered_rows), max(1, row_limit)):
        selected_rows = uncovered_rows[start:start + max(1, row_limit)]
        add_window(
            _bounded_evidence_indices(
                r_start, r_end, selected_rows, row_limit
            ),
            base_cols,
        )

    covered_cols = {
        col_index
        for window in windows
        for col_index in window["col_indices"]
    }
    uncovered_cols = [
        col_index for col_index in normalized_cols
        if col_index not in covered_cols
    ]
    for start in range(0, len(uncovered_cols), max(1, col_limit)):
        selected_cols = uncovered_cols[start:start + max(1, col_limit)]
        add_window(
            base_rows,
            _bounded_evidence_indices(
                c0, c1, selected_cols, col_limit
            ),
        )

    if not windows:
        windows.append(_evidence_window(
            sheet,
            r0,
            r1,
            c0,
            header,
            [],
            [],
            highlight_cols,
            highlight_rows,
        ))
    out = dict(windows[0])
    out["truncated"] = True
    out["windows"] = windows
    return out


def benign_reason(f):
    """Return a common innocent explanation for a finding kind, or None.

    Attached to findings as `likely_benign` so the agent always has the
    false-positive context in hand and the HTML report can show it inline.
    """
    kind = f.get("kind")
    if kind == "arithmetic_progression":
        if f.get("reused_progression"):
            return ("this exact progression is re-plotted across >=2 panels — an "
                    "independent-variable axis (field / angle / time / dose / wavelength "
                    "sweep), not measured data")
        step = f.get("step")
        if step is not None and abs(step - round(step)) < 1e-9:
            return ("an integer-step progression is usually an axis (day / dose / "
                    "timepoint), not measured data")
        return None
    if kind == "rounded_to_half_or_int":
        return ("values ending in .0/.5 are common for derived or instrument-rounded "
                "quantities (cell counts, scores, calibrated readouts)")
    if kind == "identical_after_rounding":
        return ("cells share a rounded value but differ at full precision — usually "
                "display rounding, not duplication")
    if kind in ("cross_sheet_value_overlap", "cross_sheet_position_identical"):
        if f.get("same_figure"):
            return f.get("context")
        if f.get("same_file") is False:
            return ("a control/baseline cohort is often reused across a main figure and "
                    "its extended-data figure — confirm the legend discloses the reuse")
    if kind in ("grim_inconsistent", "grimmer_inconsistent"):
        return ("GRIM/GRIMMER assume the statistic is a mean of integer-valued "
                "items (counts/scores); verify the measure is integer-granular "
                "before acting")
    return None


def _attach_benign(findings):
    """Mutate findings in-place to add a `likely_benign` note where one applies."""
    for f in findings:
        reason = benign_reason(f)
        if reason:
            f["likely_benign"] = reason
    return findings


def _attach_evidence(findings, sheet, r0, r1, c0, c1, header):
    """Mutate each finding in-place to add an `evidence` field, derived from the same
    block coordinates the detector was scanning. Highlight columns come from the
    finding's own col_*_idx / col_idx fields."""
    truncated = False
    for f in findings:
        hi_cols = []
        for k in ("col_a_idx", "col_b_idx", "col_idx"):
            if k in f and isinstance(f[k], int):
                hi_cols.append(f[k])
        hi_rows = []
        for k in ("row_a_idx", "row_b_idx", "row_idx"):
            if k in f and isinstance(f[k], int):
                hi_rows.append(f[k] + 1)
        # identical_after_rounding / within_col_dispersed_repeats list specific
        # (row, col) example cells (1-based).
        hi_cells = []
        if (
            isinstance(f.get("row_idx"), int)
            and isinstance(f.get("col_idx"), int)
        ):
            hi_cells.append((f["row_idx"], f["col_idx"]))
        for ex in f.get("example_cells", []) or []:
            try:
                row_index = int(ex[0]) - 1
                col_index = int(ex[1]) - 1
                hi_rows.append(row_index + 1)
                hi_cols.append(col_index)
                hi_cells.append((row_index, col_index))
            except (TypeError, ValueError, IndexError):
                pass
        # De-duplicate (order-preserving): a column/row referenced by both an *_idx
        # field and one or more example_cells should highlight once, not N times.
        hi_cols = list(dict.fromkeys(hi_cols))
        hi_rows = list(dict.fromkeys(hi_rows))
        hi_cells = list(dict.fromkeys(hi_cells))
        evidence = _block_evidence(
            sheet,
            r0,
            r1,
            c0,
            c1,
            header,
            highlight_cols=hi_cols,
            highlight_rows=hi_rows,
            highlight_cells=hi_cells,
        )
        f["evidence"] = evidence
        truncated = truncated or bool(evidence.get("truncated"))
    return truncated


# ---------- detectors ----------

def _isclose_rowwise(actual, expected, rtol=1e-10):
    return relation_close(actual, expected, rtol=rtol)


def _allclose_rowwise(actual, expected, rtol=1e-10):
    return bool(np.all(_isclose_rowwise(actual, expected, rtol=rtol)))


@dataclass(frozen=True)
class _NumericPairStats:
    n: int
    equal: int
    all_equal: bool
    all_int: bool
    constant_offset: int | None
    has_wide_integer: bool
    sample_a: tuple[int | float, ...]
    sample_b: tuple[int | float, ...]


def _numeric_pair_stats(sheet, r0, r1, ca, cb):
    n = 0
    equal = 0
    all_int = True
    offset = None
    offset_is_constant = True
    has_wide_integer = False
    sample_a = []
    sample_b = []
    for row in range(r0, r1):
        left = sheet.exact_numeric(row, ca)
        right = sheet.exact_numeric(row, cb)
        if left is None or right is None:
            continue
        n += 1
        if left == right:
            equal += 1
        if len(sample_a) < 8:
            sample_a.append(left)
            sample_b.append(right)
        pair_is_int = (
            isinstance(left, int)
            and not isinstance(left, bool)
            and isinstance(right, int)
            and not isinstance(right, bool)
        )
        all_int = all_int and pair_is_int
        if pair_is_int:
            current_offset = right - left
            if offset is None:
                offset = current_offset
            elif current_offset != offset:
                offset_is_constant = False
        else:
            offset_is_constant = False
        has_wide_integer = has_wide_integer or (
            isinstance(left, int)
            and abs(left) > _MAX_EXACT_FLOAT_INT
        ) or (
            isinstance(right, int)
            and abs(right) > _MAX_EXACT_FLOAT_INT
        )
    return _NumericPairStats(
        n=n,
        equal=equal,
        all_equal=n > 0 and equal == n,
        all_int=all_int,
        constant_offset=(
            offset
            if all_int and offset_is_constant
            else None
        ),
        has_wide_integer=has_wide_integer,
        sample_a=tuple(sample_a),
        sample_b=tuple(sample_b),
    )


def _sample_exact(values, k=8):
    out = []
    for value in values[:k]:
        if isinstance(value, int):
            out.append(value)
        else:
            out.append(round(float(value), 6))
    return out


_GRIM_MEAN_RE = re.compile(r"\b(mean|average|avg)\b|均值|平均", re.I)
_GRIM_SD_RE = re.compile(r"\b(s\.?d\.?|std)\b|标准差", re.I)
_GRIM_SE_RE = re.compile(
    r"\b(?:sem|s\.?e\.?|(?:standard|std\.?)\s+error)\b", re.I)
_GRIM_N_RE = re.compile(r"\bn\b|sample.?size|样本量|例数", re.I)
_GRIM_INT_RE = re.compile(
    r"count|number|cells|foci|colon|nuclei|score|rating|likert"
    r"|个数|数目|计数|数量|评分|#", re.I)
_GRIM_RATIO_RE = re.compile(
    r"%|percent|percentage|\bratio\b|\brate\b|\bindex\b|proportion|fraction"
    r"|百分|比例|比率|占比|指数", re.I)
_GRIM_ROLE_WORDS = {
    "mean", "average", "avg", "sd", "std", "stdev",
    "n", "sample", "size",
}


def _grim_role_tokens(label):
    words = re.findall(r"[a-z0-9]+", str(label or "").lower())
    return {word for word in words if word not in _GRIM_ROLE_WORDS}


def _grim_best_partner(mean_i, candidates, header):
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    mean_tokens = _grim_role_tokens(header[mean_i])
    ranked = sorted(
        candidates,
        key=lambda idx: (
            -len(mean_tokens & _grim_role_tokens(header[idx])),
            abs(idx - mean_i),
            idx,
        ),
    )
    best = ranked[0]
    overlap = len(mean_tokens & _grim_role_tokens(header[best]))
    if overlap == 0:
        return None
    return best


def _grim_column_groups(header):
    mean_cols = [
        i for i, value in enumerate(header)
        if _GRIM_MEAN_RE.search(str(value or ""))
        and _GRIM_INT_RE.search(str(value or ""))
        and not _GRIM_RATIO_RE.search(str(value or ""))
    ]
    sd_cols = [
        i for i, value in enumerate(header)
        if _GRIM_SD_RE.search(str(value or ""))
        and not _GRIM_SE_RE.search(str(value or ""))
        and i not in mean_cols
    ]
    n_cols = [
        i for i, value in enumerate(header)
        if _GRIM_N_RE.search(str(value or ""))
        and i not in mean_cols
        and i not in sd_cols
    ]
    groups = []
    for mean_i in mean_cols:
        n_i = _grim_best_partner(mean_i, n_cols, header)
        if n_i is None:
            continue
        sd_i = _grim_best_partner(mean_i, sd_cols, header)
        groups.append((mean_i, n_i, sd_i))
    return groups


def _finding_emitter(group, sink):
    local = []

    def emit(severity, builder):
        if sink is None:
            local.append(builder())
            return True
        return sink.offer(group, severity, builder)

    def commit_candidate(items):
        items = tuple(items)
        if sink is None:
            payloads = [
                builder()
                for _severity, builder in items
            ]
            local.extend(payloads)
            return tuple(True for _item in items)
        return sink.offer_batch(
            (
                group,
                severity,
                builder,
            )
            for severity, builder in items
        )

    emit.commit_candidate = commit_candidate
    return local, emit


@dataclass(frozen=True)
class _DenseFamilyResult:
    family: str
    candidates_total: int
    candidates_examined: int
    candidates_skipped: int
    work_required: int
    work_examined: int
    work_skipped: int
    work_skipped_lower_bound: int
    state_required: int
    state_required_lower_bound: int
    peak_state_units: int
    limits_reached: tuple[str, ...]


class _DenseFamilyResources:
    def __init__(
        self,
        *,
        family,
        max_rows,
        work_limit,
        state_limit,
    ):
        self.family = family
        self.max_rows = (
            None if max_rows is None else max(0, int(max_rows))
        )
        self.work_limit = (
            None if work_limit is None else max(0, int(work_limit))
        )
        self._state = StateBudget(
            None
            if state_limit is None
            else max(0, int(state_limit))
        )
        self.candidates_total = 0
        self.candidates_started = 0
        self.candidates_examined = 0
        self._work_admitted = 0
        self.work_examined = 0
        self.minimum_candidate_work = 0
        self.state_required = 0
        self._limits_reached = set()
        self._stopped = False

    @classmethod
    def unlimited(cls, family):
        return cls(
            family=family,
            max_rows=None,
            work_limit=None,
            state_limit=None,
        )

    def begin(
        self,
        *,
        row_count,
        candidates_total,
        minimum_candidate_work,
        state_required,
    ):
        self.candidates_total = max(0, int(candidates_total))
        self.minimum_candidate_work = max(
            0, int(minimum_candidate_work)
        )
        self.state_required = max(0, int(state_required))
        if self.max_rows is not None and row_count > self.max_rows:
            self._limits_reached.add("row")
            self._stopped = True
            return False
        return True

    def _begin_candidate(self, source_visits):
        if self._stopped:
            return False
        source_visits = max(0, int(source_visits))
        if (
            self.work_limit is not None
            and self._work_admitted + source_visits > self.work_limit
        ):
            self._limits_reached.add("work")
            self._stopped = True
            return False
        self.candidates_started += 1
        self._work_admitted += source_visits
        return True

    def _reserve(self, name, units):
        lease = self._state.try_reserve(name, units)
        if lease is None:
            self._limits_reached.add("state")
            self._stopped = True
        return lease

    @staticmethod
    def _release_leases(leases):
        for lease in reversed(tuple(leases)):
            lease.release()

    def start_allocated_candidate(
        self,
        name,
        units,
        source_visits,
        emit,
        *,
        initial_reservations=(),
    ):
        leases = []
        for initial_name, initial_units in initial_reservations:
            initial_lease = self._reserve(
                initial_name, initial_units
            )
            if initial_lease is None:
                self._release_leases(leases)
                return None, None, ()
            leases.append(initial_lease)
        lease = self._reserve(name, units)
        if lease is None:
            self._release_leases(leases)
            return None, None, ()
        leases.append(lease)
        if not self._begin_candidate(source_visits):
            self._release_leases(leases)
            return None, None, ()
        try:
            candidate = self._candidate(
                emit,
                *leases,
                source_visits=source_visits,
                source_lease=lease,
            )
        except BaseException:
            self._release_leases(leases)
            raise
        return candidate, lease, tuple(leases[:-1])

    def _candidate(
        self,
        emit,
        *initial_leases,
        source_visits=0,
        source_lease=None,
    ):
        return _DenseCandidate(
            self,
            emit,
            initial_leases=initial_leases,
            source_visits=source_visits,
            source_lease=source_lease,
        )

    def start_candidate(self, source_visits, emit):
        if not self._begin_candidate(source_visits):
            return None
        return self._candidate(
            emit,
            source_visits=source_visits,
        )

    def _complete_work(self, source_visits):
        self.work_examined += max(0, int(source_visits))

    def _complete_candidate(self):
        self.candidates_examined += 1

    @property
    def state(self):
        return self._state

    @property
    def stopped(self):
        return self._stopped

    def result(self):
        unstarted = self.candidates_total - self.candidates_started
        work_required = (
            self.candidates_total * self.minimum_candidate_work
        )
        work_skipped = max(0, work_required - self.work_examined)
        state_required_lower_bound = (
            self._state.required_peak_units
        )
        assert state_required_lower_bound <= self.state_required
        return _DenseFamilyResult(
            family=self.family,
            candidates_total=self.candidates_total,
            candidates_examined=self.candidates_examined,
            candidates_skipped=(
                self.candidates_total - self.candidates_examined
            ),
            work_required=work_required,
            work_examined=self.work_examined,
            work_skipped=work_skipped,
            work_skipped_lower_bound=(
                max(0, unstarted) * self.minimum_candidate_work
            ),
            state_required=self.state_required,
            state_required_lower_bound=state_required_lower_bound,
            peak_state_units=self._state.peak_units,
            limits_reached=tuple(
                name for name in ("row", "work", "state")
                if name in self._limits_reached
            ),
        )


class _CandidateFindingBuffer:
    def __init__(self):
        self._items = []

    def offer(self, severity, builder):
        self._items.append((severity, builder))

    def commit(self, emit):
        items = tuple(self._items)
        commit_candidate = getattr(
            emit, "commit_candidate", None
        )
        if commit_candidate is not None:
            commit_candidate(items)
        else:
            payloads = [
                (severity, builder())
                for severity, builder in items
            ]
            for severity, payload in payloads:
                emit(
                    severity,
                    lambda payload=payload: payload,
                )
        self._items.clear()

    def discard(self):
        self._items.clear()


class _DenseCandidateRejected(RuntimeError):
    pass


class _DenseCandidate:
    def __init__(
        self,
        resources,
        emit,
        *,
        initial_leases=(),
        source_visits=0,
        source_lease=None,
    ):
        self._resources = resources
        self.emit = emit
        self.findings = _CandidateFindingBuffer()
        self._leases = {}
        self._peak_lease_count = 0
        self._rejected = False
        self._source_visits = max(0, int(source_visits))
        self._source_lease_id = (
            None if source_lease is None else id(source_lease)
        )
        self._source_work_completed = False
        self.entered = False
        self.closed = False
        for lease in initial_leases:
            self._adopt(lease)

    def _adopt(self, lease):
        key = id(lease)
        assert key not in self._leases
        assert not lease.released
        self._leases[key] = lease
        self._peak_lease_count = max(
            self._peak_lease_count, len(self._leases)
        )

    def __enter__(self):
        assert not self.closed
        assert not self.entered
        self.entered = True
        return self

    def reserve(self, name, units):
        assert self.entered
        assert not self.closed
        if self._rejected:
            raise _DenseCandidateRejected
        lease = self._resources._reserve(name, units)
        if lease is None:
            self._rejected = True
            raise _DenseCandidateRejected
        self._adopt(lease)
        return lease

    def allocate(self, name, units, factory):
        assert self.entered
        assert not self.closed
        lease = self.reserve(name, units)
        return self.materialize(lease, factory), lease

    def materialize(
        self,
        lease,
        factory,
        *,
        release_after=(),
        completes_source=False,
    ):
        assert self.entered
        assert not self.closed
        if lease is None:
            assert completes_source
        else:
            assert id(lease) in self._leases
            assert not lease.released
        release_after = tuple(release_after)
        assert all(item is not lease for item in release_after)
        try:
            value = factory()
            values = value if isinstance(value, tuple) else (value,)
            if lease is not None:
                lease.validate_nbytes(*(
                    item.nbytes
                    for item in values
                    if hasattr(item, "nbytes")
                ))
            for transient_lease in release_after:
                self.release(transient_lease)
            if (
                completes_source
                or id(lease) == self._source_lease_id
            ):
                self._complete_source_work()
        except BaseException:
            self._rejected = True
            raise
        return value

    def _complete_source_work(self):
        assert self.entered
        assert not self.closed
        assert not self._source_work_completed
        self._resources._complete_work(self._source_visits)
        self._source_work_completed = True

    def release(self, lease):
        assert self.entered
        assert not self.closed
        tracked = self._leases.pop(id(lease))
        assert tracked is lease
        assert not lease.released
        lease.release()

    def offer(self, severity, builder):
        assert self.entered
        assert not self.closed
        self.findings.offer(severity, builder)

    @property
    def rejected(self):
        return self._rejected

    @property
    def live_lease_count(self):
        return len(self._leases)

    @property
    def peak_lease_count(self):
        return self._peak_lease_count

    def __exit__(self, exc_type, _exc, _traceback):
        assert not self.closed
        assert self.entered
        resource_rejection = exc_type is _DenseCandidateRejected
        try:
            if exc_type is None and not self._rejected:
                try:
                    self.findings.commit(self.emit)
                except BaseException:
                    self.findings.discard()
                    raise
                self._resources._complete_candidate()
            else:
                self.findings.discard()
        finally:
            for lease in reversed(tuple(self._leases.values())):
                assert not lease.released
                lease.release()
            self._leases.clear()
            self.closed = True
        return resource_rejection


class _BoundedRankedFindingBuffer:
    def __init__(self, cap):
        self.cap = max(0, int(cap))
        self.offered = 0
        self._sequence = 0
        self._items = []

    def offer(self, sort_key, severity, builder):
        key = (*tuple(sort_key), self._sequence)
        self._sequence += 1
        self.offered += 1
        item = (key, severity, builder)
        if self.cap == 0:
            return False
        if len(self._items) < self.cap:
            self._items.append(item)
            return True
        worst_index = max(
            range(len(self._items)),
            key=lambda index: self._items[index][0],
        )
        if key >= self._items[worst_index][0]:
            return False
        self._items[worst_index] = item
        return True

    def drain(self, emit):
        omitted = self.offered - len(self._items)
        for _key, severity, builder in sorted(
            self._items, key=lambda item: item[0]
        ):
            emit(severity, builder)
        self._items.clear()
        return omitted


def _compact_high_precision_fractions(frac_x, hp_rows):
    count = 0
    for index, selected in enumerate(hp_rows):
        value = frac_x[index]
        if selected and _sig_frac_digits(value) >= 4:
            frac_x[count] = round(float(value), 6)
            count += 1
    return count


def detect_relations(
    sheet,
    r0,
    r1,
    c0,
    c1,
    header,
    *,
    _resources=None,
    _finding_sink=None,
):
    findings, emit = _finding_emitter("relations", _finding_sink)
    resources = _resources or _DenseFamilyResources.unlimited(
        "relations"
    )
    row_count = r1 - r0
    bool_units = state_units_for_nbytes(row_count)
    relation_state_upper_bounds = {
        "mask": bool_units,
        "mask_rhs_workspace": bool_units,
        "filtered_values": 2 * row_count,
        "diff": row_count,
        "nonzero_workspace": bool_units,
        "relation_close_workspace": 12 * row_count,
        "ratio": row_count,
        "ratio_stats_workspace": 2 * row_count,
        "sum": row_count,
        "sum_compare_workspace": 13 * row_count,
        "linear_fit_workspace": 12 * row_count,
        "fitted": row_count,
        "fitted_build_workspace": 2 * row_count,
        "fitted_relation_workspace": 12 * row_count,
        "integer_shift_workspace": 8 * row_count,
        "diff_is_int": bool_units,
        "fractional_workspace": row_count,
        "frac_x": row_count,
        "hp_rows": bool_units,
        "high_precision_unique_workspace": 4 * row_count,
        "high_precision_unique": row_count,
        "integer_diff_round_workspace": row_count,
        "int_diff_rounded": row_count,
        "integer_diff_unique_workspace": 4 * row_count,
        "int_diffs": row_count,
        "diff_rounded": row_count,
        "diff_unique_workspace": 4 * row_count,
        "unique_diffs": row_count,
    }
    pair_count = (c1 - c0) * (c1 - c0 - 1) // 2
    if not resources.begin(
        row_count=row_count,
        candidates_total=pair_count,
        minimum_candidate_work=2 * row_count,
        state_required=sum(relation_state_upper_bounds.values()),
    ):
        return findings

    for ci in range(c0, c1):
        ai = sheet.numeric[r0:r1, ci]
        for cj in range(ci + 1, c1):
            aj = sheet.numeric[r0:r1, cj]
            candidate = resources.start_candidate(
                2 * row_count,
                emit,
            )
            if candidate is None:
                break

            def run_pair_candidate():
                pair_stats = candidate.materialize(
                    None,
                    lambda: _numeric_pair_stats(
                        sheet, r0, r1, ci, cj
                    ),
                    completes_source=True,
                )
                if pair_stats.n < 4:
                    return
                if pair_stats.all_equal:
                    candidate.offer(
                        "high",
                        lambda ci=ci, cj=cj,
                        pair_stats=pair_stats: dict(
                            kind="identical_column",
                            col_a=header[ci - c0],
                            col_b=header[cj - c0],
                            col_a_idx=ci,
                            col_b_idx=cj,
                            n=pair_stats.n,
                            severity="high",
                            col_a_sample=_sample_exact(
                                pair_stats.sample_a
                            ),
                            col_b_sample=_sample_exact(
                                pair_stats.sample_b
                            ),
                            rule=f"col[{cj}] == col[{ci}]",
                        ),
                    )
                    return
                if (
                    pair_stats.all_int
                    and pair_stats.constant_offset not in (None, 0)
                ):
                    offset = pair_stats.constant_offset
                    candidate.offer(
                        "high",
                        lambda ci=ci, cj=cj,
                        pair_stats=pair_stats,
                        offset=offset: dict(
                            kind="constant_offset",
                            col_a=header[ci - c0],
                            col_b=header[cj - c0],
                            col_a_idx=ci,
                            col_b_idx=cj,
                            n=pair_stats.n,
                            offset=offset,
                            severity="high",
                            col_a_sample=_sample_exact(
                                pair_stats.sample_a
                            ),
                            col_b_sample=_sample_exact(
                                pair_stats.sample_b
                            ),
                            rule=(
                                f"col[{cj}] = col[{ci}] + {offset}"
                            ),
                        ),
                    )
                    return
                if pair_stats.has_wide_integer:
                    return

                mask, mask_lease = candidate.allocate(
                    "mask",
                    relation_state_upper_bounds["mask"],
                    lambda: np.isnan(ai),
                )
                np.logical_not(mask, out=mask)
                mask_rhs, mask_rhs_lease = candidate.allocate(
                    "mask_rhs_workspace",
                    relation_state_upper_bounds[
                        "mask_rhs_workspace"
                    ],
                    lambda: np.isnan(aj),
                )
                np.logical_not(mask_rhs, out=mask_rhs)
                np.logical_and(mask, mask_rhs, out=mask)
                del mask_rhs
                candidate.release(mask_rhs_lease)
                n = int(mask.sum())
                if n < 4:
                    del mask
                    candidate.release(mask_lease)
                    return
                (x, y), filtered_lease = candidate.allocate(
                    "filtered_values",
                    2 * row_count,
                    lambda: (ai[mask], aj[mask]),
                )
                del mask
                candidate.release(mask_lease)
                x_sample = tuple(_sample(x))
                y_sample = tuple(_sample(y))

                diff, diff_lease = candidate.allocate(
                    "diff",
                    n,
                    lambda: y - x,
                )
                mean_diff = float(np.mean(diff))
                lo = int(np.argmin(x))
                hi = int(np.argmax(x))
                dx = float(x[hi]) - float(x[lo])
                varying_x = dx != 0 and math.isfinite(dx)
                fit_valid = False
                if varying_x:
                    fit_count = 0
                    x_center = 0.0
                    y_center = 0.0
                    centered_xx = 0.0
                    centered_xy = 0.0
                    for x_value, y_value in zip(x, y):
                        fit_count += 1
                        x_delta = float(x_value) - x_center
                        x_center += x_delta / fit_count
                        y_delta = float(y_value) - y_center
                        y_center += y_delta / fit_count
                        centered_xx += x_delta * (
                            float(x_value) - x_center
                        )
                        centered_xy += x_delta * (
                            float(y_value) - y_center
                        )
                    if (
                        centered_xx > 0
                        and math.isfinite(centered_xx)
                        and math.isfinite(centered_xy)
                    ):
                        slope = centered_xy / centered_xx
                    elif centered_xx == 0:
                        # Finite centered products can underflow at tiny
                        # scales; the widest secant remains bounded.
                        slope = (
                            float(y[hi]) - float(y[lo])
                        ) / dx
                    else:
                        slope = 0.0
                    fit_valid = math.isfinite(slope)
                    if fit_valid:
                        centered_radius = 0.0
                        centered_residual = 0.0
                        intercept_x = float(x[0])
                        intercept_y = float(y[0])
                        y_min = intercept_y
                        y_max = intercept_y
                        for x_value, y_value in zip(x, y):
                            x_scalar = float(x_value)
                            y_scalar = float(y_value)
                            centered_x = x_scalar - x_center
                            centered_y = y_scalar - y_center
                            centered_radius = max(
                                centered_radius, abs(centered_x)
                            )
                            centered_residual = max(
                                centered_residual,
                                abs(
                                    centered_y
                                    - slope * centered_x
                                ),
                            )
                            y_min = min(y_min, y_scalar)
                            y_max = max(y_max, y_scalar)
                            if abs(x_scalar) < abs(intercept_x):
                                intercept_x = x_scalar
                                intercept_y = y_scalar
                        intercept_product = slope * intercept_x
                        intercept = intercept_y - intercept_product
                        intercept_assessment = (
                            assess_relation_intercept(
                                slope=slope,
                                intercept=intercept,
                                x_center=x_center,
                                centered_radius=centered_radius,
                                centered_residual=centered_residual,
                                transformed_span=max(
                                    abs(slope * dx),
                                    abs(y_max - y_min),
                                ),
                                anchor_y=intercept_y,
                                intercept_product=intercept_product,
                            )
                        )
                        relation_intercept_state = (
                            None
                            if intercept_assessment is None
                            else intercept_assessment.state
                        )
                    else:
                        intercept = 0.0
                        relation_intercept_state = None
                if not fit_valid:
                    slope = 0.0
                    intercept = 0.0
                    relation_intercept_state = None
                relation_workspace = candidate.reserve(
                    "relation_close_workspace",
                    12 * n,
                )
                offset_close = (
                    mean_diff != 0
                    and bool(
                        relation_close(y, x + mean_diff).all()
                    )
                )
                candidate.release(relation_workspace)
                if offset_close:
                    candidate.offer(
                        "high",
                        lambda ci=ci, cj=cj, n=n,
                        mean_diff=mean_diff,
                        x_sample=x_sample,
                        y_sample=y_sample: dict(
                            kind="constant_offset",
                            col_a=header[ci - c0],
                            col_b=header[cj - c0],
                            col_a_idx=ci,
                            col_b_idx=cj,
                            n=n,
                            offset=mean_diff,
                            severity="high",
                            col_a_sample=list(x_sample),
                            col_b_sample=list(y_sample),
                            rule=(
                                f"col[{cj}] = col[{ci}] + "
                                f"{mean_diff:.6g}"
                            ),
                        ),
                    )
                    del diff, x, y
                    candidate.release(diff_lease)
                    candidate.release(filtered_lease)
                    return

                ratio_emitted = False
                ratio_compatible = False
                mean_ratio = 0.0
                nonzero_workspace = candidate.reserve(
                    "nonzero_workspace",
                    state_units_for_nbytes(n),
                )
                all_nonzero = bool((x != 0).all())
                candidate.release(nonzero_workspace)
                if all_nonzero:
                    ratio, ratio_lease = candidate.allocate(
                        "ratio",
                        n,
                        lambda: y / x,
                    )
                    mean_ratio = float(np.mean(ratio))
                    ratio_tol = 1e-9 * max(
                        abs(mean_ratio), 1e-300
                    )
                    ratio_stats_workspace = candidate.reserve(
                        "ratio_stats_workspace",
                        2 * n,
                    )
                    stable_ratio = np.std(ratio) < ratio_tol
                    candidate.release(ratio_stats_workspace)
                    if (
                        stable_ratio
                        and abs(mean_ratio - 1) > 1e-9
                        and abs(mean_ratio) > 1e-9
                    ):
                        ratio_close_workspace = candidate.reserve(
                            "relation_close_workspace",
                            12 * n,
                        )
                        ratio_compatible = bool(
                            relation_close(y, mean_ratio * x).all()
                        )
                        candidate.release(ratio_close_workspace)
                    del ratio
                    candidate.release(ratio_lease)

                affine_compatible = False
                if n >= 5 and fit_valid:
                    linear_fit_workspace = candidate.reserve(
                        "linear_fit_workspace",
                        12 * n,
                    )
                    try:
                        (
                            _fit_slope,
                            _fit_intercept,
                            r,
                            _p,
                            _se,
                        ) = stats.linregress(x, y)
                    except ValueError:
                        candidate.release(linear_fit_workspace)
                        del diff, x, y
                        candidate.release(diff_lease)
                        candidate.release(filtered_lease)
                        return
                    y_has_variation = np.std(y) > 0
                    candidate.release(linear_fit_workspace)
                    fitted_build_workspace = candidate.reserve(
                        "fitted_build_workspace",
                        2 * n,
                    )
                    fitted, fitted_lease = candidate.allocate(
                        "fitted",
                        n,
                        lambda: slope * x + intercept,
                    )
                    candidate.release(fitted_build_workspace)
                    if y_has_variation:
                        fitted_relation_workspace = (
                            candidate.reserve(
                                "fitted_relation_workspace",
                                12 * n,
                            )
                        )
                        fitted_close = bool(
                            relation_close(
                                y, fitted, rtol=1e-7
                            ).all()
                        )
                        candidate.release(
                            fitted_relation_workspace
                        )
                        affine_compatible = (
                            fitted_close
                            and abs(r) > 0.99
                            and relation_intercept_state is not None
                        )
                    del fitted
                    candidate.release(fitted_lease)

                if (
                    ratio_compatible
                    and (
                        relation_intercept_state == "proportional"
                        or (
                            relation_intercept_state == "ambiguous"
                            and affine_compatible
                        )
                    )
                ):
                    relation_model_ambiguous = (
                        relation_intercept_state == "ambiguous"
                    )
                    candidate.offer(
                        "high",
                        lambda ci=ci, cj=cj, n=n,
                        mean_ratio=mean_ratio,
                        x_sample=x_sample,
                        y_sample=y_sample,
                        relation_model_ambiguous=(
                            relation_model_ambiguous
                        ): {
                            "kind": "constant_ratio",
                            "col_a": header[ci - c0],
                            "col_b": header[cj - c0],
                            "col_a_idx": ci,
                            "col_b_idx": cj,
                            "n": n,
                            "ratio": mean_ratio,
                            "severity": "high",
                            "col_a_sample": list(x_sample),
                            "col_b_sample": list(y_sample),
                            "rule": (
                                f"col[{cj}] = col[{ci}] * "
                                f"{mean_ratio:.6g}"
                            ),
                            **(
                                {
                                    "relation_model_ambiguous": True,
                                    "relation_model_alternatives": [
                                        "constant_ratio",
                                        "exact_linear",
                                    ],
                                }
                                if relation_model_ambiguous
                                else {}
                            ),
                        },
                    )
                    ratio_emitted = True

                csum, sum_lease = candidate.allocate(
                    "sum",
                    n,
                    lambda: x + y,
                )
                if n >= 5:
                    K = float(np.mean(csum))
                    sum_compare_workspace = candidate.reserve(
                        "sum_compare_workspace",
                        13 * n,
                    )
                    sum_close = (
                        K != 0
                        and bool(
                            relation_close(
                                csum,
                                np.full_like(csum, K),
                            ).all()
                        )
                    )
                    candidate.release(sum_compare_workspace)
                    if sum_close:
                        candidate.offer(
                            "high",
                            lambda ci=ci, cj=cj, n=n, K=K,
                            x_sample=x_sample,
                            y_sample=y_sample: dict(
                                kind="sum_constant",
                                col_a=header[ci - c0],
                                col_b=header[cj - c0],
                                col_a_idx=ci,
                                col_b_idx=cj,
                                n=n,
                                sum=K,
                                severity="high",
                                col_a_sample=list(x_sample),
                                col_b_sample=list(y_sample),
                                rule=(
                                    f"col[{ci}] + col[{cj}] = "
                                    f"{K:.6g}"
                                ),
                            ),
                        )
                del csum
                candidate.release(sum_lease)

                if affine_compatible:
                    is_identity = (
                        abs(slope - 1) < 1e-9
                        and relation_intercept_state
                        == "proportional"
                    )
                    redundant_scaling = ratio_emitted
                    if not (
                        is_identity
                        or redundant_scaling
                    ):
                        candidate.offer(
                            "high",
                            lambda ci=ci, cj=cj, n=n,
                            slope=slope,
                            intercept=intercept,
                            x_sample=x_sample,
                            y_sample=y_sample: dict(
                                kind="exact_linear",
                                col_a=header[ci - c0],
                                col_b=header[cj - c0],
                                col_a_idx=ci,
                                col_b_idx=cj,
                                n=n,
                                slope=float(slope),
                                intercept=float(intercept),
                                severity="high",
                                col_a_sample=list(
                                    x_sample
                                ),
                                col_b_sample=list(
                                    y_sample
                                ),
                                rule=(
                                    f"col[{cj}] = "
                                    f"{slope:.4g} * "
                                    f"col[{ci}] + "
                                    f"{intercept:.4g}"
                                ),
                            ),
                        )

                if n >= 24:
                    best_len = cur_len = 1
                    best_val = float(diff[0])
                    best_tolerance = (
                        scalar_ulp_tolerance(x[0], y[0])
                        + 1e-9 * abs(best_val)
                    )
                    current_tolerance = best_tolerance
                    for t in range(1, len(diff)):
                        pair_tolerance = (
                            scalar_ulp_tolerance(
                                x[t - 1],
                                y[t - 1],
                                x[t],
                                y[t],
                            )
                            + 1e-9
                            * max(
                                abs(diff[t - 1]),
                                abs(diff[t]),
                            )
                        )
                        if (
                            abs(diff[t] - diff[t - 1])
                            <= pair_tolerance
                        ):
                            cur_len += 1
                            current_tolerance = max(
                                current_tolerance,
                                pair_tolerance,
                            )
                        else:
                            if cur_len > best_len:
                                best_len = cur_len
                                best_val = float(diff[t - 1])
                                best_tolerance = current_tolerance
                            cur_len = 1
                            current_tolerance = (
                                scalar_ulp_tolerance(x[t], y[t])
                                + 1e-9 * abs(diff[t])
                            )
                    if cur_len > best_len:
                        best_len = cur_len
                        best_val = float(diff[-1])
                        best_tolerance = current_tolerance
                    run_floor = max(20, int(round(0.5 * n)))
                    col_hp = (
                        sum(
                            1
                            for value in x
                            if _sig_frac_digits(value) >= 2
                        )
                        >= 0.6 * len(x)
                    )
                    off_is_small_integer = (
                        abs(best_val - round(best_val))
                        <= max(
                            best_tolerance,
                            scalar_ulp_tolerance(
                                best_val, round(best_val)
                            ),
                        )
                        and abs(round(best_val)) >= 1
                    )
                    non_trivial_offset = (
                        not off_is_small_integer
                    ) or col_hp
                    if (
                        best_len >= run_floor
                        and best_len < n
                        and abs(best_val) > best_tolerance
                        and non_trivial_offset
                    ):
                        candidate.offer(
                            "high",
                            lambda ci=ci, cj=cj, n=n,
                            best_len=best_len,
                            best_val=best_val,
                            x_sample=x_sample,
                            y_sample=y_sample: dict(
                                kind="partial_constant_offset",
                                col_a=header[ci - c0],
                                col_b=header[cj - c0],
                                col_a_idx=ci,
                                col_b_idx=cj,
                                n=n,
                                run_length=int(best_len),
                                offset=float(best_val),
                                severity="high",
                                col_a_sample=list(x_sample),
                                col_b_sample=list(y_sample),
                                rule=(
                                    f"col[{cj}] = col[{ci}] + "
                                    f"{best_val:.6g} over a run "
                                    f"of {int(best_len)}/{n} "
                                    "consecutive rows"
                                ),
                            ),
                        )
                        del diff, x, y
                        candidate.release(diff_lease)
                        candidate.release(filtered_lease)
                        return

                if n >= 5:
                    integer_shift_workspace = candidate.reserve(
                        "integer_shift_workspace",
                        8 * n,
                    )
                    diff_is_int, diff_is_int_lease = (
                        candidate.allocate(
                            "diff_is_int",
                            state_units_for_nbytes(n),
                            lambda: integer_shift_close(x, y),
                        )
                    )
                    candidate.release(integer_shift_workspace)
                    integer_shift_count = int(
                        diff_is_int.sum()
                    )
                    fractional_workspace = candidate.reserve(
                        "fractional_workspace",
                        n,
                    )
                    frac_x, frac_x_lease = candidate.allocate(
                        "frac_x",
                        n,
                        lambda: x - np.round(x),
                    )
                    hp_rows, hp_rows_lease = candidate.allocate(
                        "hp_rows",
                        state_units_for_nbytes(n),
                        lambda: (
                            diff_is_int
                            & (np.abs(frac_x) > 1e-6)
                        ),
                    )
                    candidate.release(fractional_workspace)
                    n_real_frac = int(hp_rows.sum())
                    high_precision_count = (
                        _compact_high_precision_fractions(
                            frac_x, hp_rows
                        )
                    )
                    del hp_rows
                    candidate.release(hp_rows_lease)
                    high_precision_workspace = candidate.reserve(
                        "high_precision_unique_workspace",
                        4 * n,
                    )
                    (
                        high_precision_unique,
                        high_precision_unique_lease,
                    ) = candidate.allocate(
                        "high_precision_unique",
                        n,
                        lambda: np.unique(
                            frac_x[:high_precision_count]
                        ),
                    )
                    distinct_hp = len(high_precision_unique)
                    del high_precision_unique, frac_x
                    candidate.release(
                        high_precision_unique_lease
                    )
                    candidate.release(high_precision_workspace)
                    candidate.release(frac_x_lease)

                    integer_diff_round_workspace = (
                        candidate.reserve(
                            "integer_diff_round_workspace",
                            n,
                        )
                    )
                    (
                        int_diff_rounded,
                        int_diff_rounded_lease,
                    ) = candidate.allocate(
                        "int_diff_rounded",
                        n,
                        lambda: np.round(diff[diff_is_int]),
                    )
                    candidate.release(
                        integer_diff_round_workspace
                    )
                    integer_diff_unique_workspace = (
                        candidate.reserve(
                            "integer_diff_unique_workspace",
                            4 * n,
                        )
                    )
                    int_diffs, int_diffs_lease = candidate.allocate(
                        "int_diffs",
                        n,
                        lambda: np.unique(int_diff_rounded),
                    )
                    int_diff_count = len(int_diffs)
                    del int_diffs, int_diff_rounded
                    candidate.release(int_diffs_lease)
                    candidate.release(
                        integer_diff_unique_workspace
                    )
                    candidate.release(int_diff_rounded_lease)
                    del diff_is_int
                    candidate.release(diff_is_int_lease)
                    if (
                        integer_shift_count
                        >= max(5, int(round(0.8 * n)))
                        and distinct_hp >= 3
                        and int_diff_count >= 2
                    ):
                        candidate.offer(
                            "high",
                            lambda ci=ci, cj=cj, n=n,
                            n_real_frac=n_real_frac,
                            distinct_hp=distinct_hp,
                            x_sample=x_sample,
                            y_sample=y_sample: dict(
                                kind=(
                                    "integer_diff_shared_fraction"
                                ),
                                col_a=header[ci - c0],
                                col_b=header[cj - c0],
                                col_a_idx=ci,
                                col_b_idx=cj,
                                n=n,
                                n_shared_fraction=n_real_frac,
                                n_high_precision=distinct_hp,
                                severity="high",
                                col_a_sample=list(x_sample),
                                col_b_sample=list(y_sample),
                                rule=(
                                    f"col[{cj}] and col[{ci}] "
                                    "share the same decimal "
                                    f"fraction on {n_real_frac}/{n} "
                                    f"rows ({distinct_hp} distinct "
                                    "high-precision fractions) but "
                                    "differ by whole numbers"
                                ),
                            ),
                        )
                        del diff, x, y
                        candidate.release(diff_lease)
                        candidate.release(filtered_lease)
                        return

                if n >= 8:
                    (
                        diff_rounded,
                        diff_rounded_lease,
                    ) = candidate.allocate(
                        "diff_rounded",
                        n,
                        lambda: np.round(diff, 4),
                    )
                    diff_unique_workspace = candidate.reserve(
                        "diff_unique_workspace",
                        4 * n,
                    )
                    unique_diffs, unique_diffs_lease = (
                        candidate.allocate(
                            "unique_diffs",
                            n,
                            lambda: np.unique(diff_rounded),
                        )
                    )
                    unique_diff_count = len(unique_diffs)
                    unique_diff_values = tuple(
                        float(value) for value in unique_diffs
                    )
                    del unique_diffs, diff_rounded
                    candidate.release(unique_diffs_lease)
                    candidate.release(diff_unique_workspace)
                    candidate.release(diff_rounded_lease)
                    if (
                        2 <= unique_diff_count
                        <= min(6, n // 3)
                    ):
                        candidate.offer(
                            "medium",
                            lambda ci=ci, cj=cj, n=n,
                            unique_diff_count=unique_diff_count,
                            unique_diff_values=unique_diff_values,
                            x_sample=x_sample,
                            y_sample=y_sample: dict(
                                kind="small_diff_set",
                                col_a=header[ci - c0],
                                col_b=header[cj - c0],
                                col_a_idx=ci,
                                col_b_idx=cj,
                                n=n,
                                unique_diffs=list(
                                    unique_diff_values
                                ),
                                severity="medium",
                                col_a_sample=list(x_sample),
                                col_b_sample=list(y_sample),
                                rule=(
                                    f"col[{cj}] - col[{ci}] only "
                                    f"takes {unique_diff_count} "
                                    "discrete values"
                                ),
                            ),
                        )
                del diff, x, y
                candidate.release(diff_lease)
                candidate.release(filtered_lease)

            with candidate:
                run_pair_candidate()
            if candidate.rejected:
                break
    return findings


_ROW_PAIR_MAX_ROWS = 80
_ROW_PAIR_MAX_COLS = 200
_ROW_PAIR_MAX_FINDINGS_PER_BLOCK = 25


def _row_label(sheet, r, c0):
    labels = []
    for c in range(max(0, c0 - 4), c0):
        v = sheet.cell(r, c)
        if v is not None and not is_num(v):
            s = str(v).strip()
            if s:
                labels.append(s)
    return " | ".join(labels) if labels else f"row {r + 1}"


def _has_fractional_part(v):
    fv = float(v)
    return abs(fv - round(fv)) > 1e-9


def _ones_digit(v):
    return int(math.floor(abs(float(v)) + 1e-9)) % 10


def _decimal_digit(v, place=1):
    scale = 10 ** place
    return int(math.floor(abs(float(v)) * scale + 1e-8)) % 10


def _sig_frac_digits(v):
    """Count significant fractional decimal digits of v's distance to the nearest integer.
    167.93 -> 2 (.07), 178.7615 -> 4 (.2385), 100.5 -> 1, an integer -> 0."""
    fv = abs(float(v) - round(float(v)))
    if fv < 1e-9:
        return 0
    return len(f"{fv:.9f}".split(".")[1].rstrip("0"))


def _is_multiple_of_ten_diff(d):
    if abs(d) < 10 - 1e-8:
        return False
    nearest = round(d / 10.0) * 10.0
    return abs(d - nearest) <= 1e-7


def _row_pair_low_cardinality_integer_like(x, y):
    combined = np.concatenate([x, y])
    finite = combined[np.isfinite(combined)]
    if len(finite) == 0:
        return True
    near_integer = np.mean(np.abs(finite - np.round(finite)) < 1e-9)
    distinct = len(set(np.round(finite, 4).tolist()))
    max_abs = float(np.max(np.abs(finite)))
    return bool(near_integer >= 0.9 and max_abs <= 20 and distinct <= max(5, len(finite) // 4))


def _row_pair_finding_builder(
    *,
    label_a,
    label_b,
    ra,
    rb,
    n,
    changed,
    same_decimal1,
    frac_decimal1,
    same_ones,
    same_ones_decimal1,
    frac_ones_decimal1,
    coarse_10_diff,
    frac_coarse_10,
    top_diffs,
    examples,
    severity,
):
    top_diffs = tuple(top_diffs)
    examples = tuple(dict(item) for item in examples)

    def build():
        return {
            "kind": "row_pair_digit_coupling",
            "row_a": label_a,
            "row_b": label_b,
            "row_a_idx": ra,
            "row_b_idx": rb,
            "n": n,
            "changed": changed,
            "same_decimal1": same_decimal1,
            "same_decimal1_frac": frac_decimal1,
            "same_ones": same_ones,
            "same_ones_decimal1": same_ones_decimal1,
            "same_ones_decimal1_frac": frac_ones_decimal1,
            "coarse_10_diff": coarse_10_diff,
            "coarse_10_diff_frac": frac_coarse_10,
            "top_diffs": [
                {"diff": float(diff), "count": int(count)}
                for diff, count in top_diffs
            ],
            "examples": [dict(item) for item in examples],
            "example_cells": (
                [(ra + 1, item["col"]) for item in examples[:4]]
                + [(rb + 1, item["col"]) for item in examples[:4]]
            ),
            "severity": severity,
            "rule": (
                f"rows {ra + 1} and {rb + 1}: first decimal digit "
                f"matches {same_decimal1}/{n}; ones+decimal matches "
                f"{same_ones_decimal1}/{n}; coarse 10-step "
                f"differences {coarse_10_diff}/{n}"
            ),
        }

    return build


def detect_row_pair_digit_coupling(
    sheet,
    r0,
    r1,
    c0,
    c1,
    header,
    min_n=10,
    *,
    with_coverage=False,
    _finding_sink=None,
):
    """Detect paired rows that preserve low-order digits across many cells.

    This targets source-data layouts where replicate/condition rows are aligned by
    measurement column. The statistical signal is: row B differs from row A in
    value, but the first decimal digit and often the ones digit are preserved
    across many paired cells, with differences frequently landing on coarse
    multiples of 10.
    """
    findings, emit = _finding_emitter("row_pairs", _finding_sink)
    ranked = _BoundedRankedFindingBuffer(
        _ROW_PAIR_MAX_FINDINGS_PER_BLOCK
    )
    n_rows = r1 - r0
    n_cols = c1 - c0
    if n_rows < 2 or n_cols < min_n:
        return (
            (findings, {"findings_omitted": 0})
            if with_coverage
            else findings
        )
    if n_rows > _ROW_PAIR_MAX_ROWS or n_cols > _ROW_PAIR_MAX_COLS:
        return (
            (findings, {"findings_omitted": 0})
            if with_coverage
            else findings
        )

    labels = {r: _row_label(sheet, r, c0) for r in range(r0, r1)}
    for i, ra in enumerate(range(r0, r1)):
        label_a = labels[ra]
        if _AXIS_CONTEXT_LABEL_RE.search(label_a):
            continue
        a = sheet.numeric[ra, c0:c1]
        for rb in range(r0 + i + 1, r1):
            label_b = labels[rb]
            if _AXIS_CONTEXT_LABEL_RE.search(label_b):
                continue
            b = sheet.numeric[rb, c0:c1]
            mask = ~np.isnan(a) & ~np.isnan(b)
            n = int(mask.sum())
            if n < min_n:
                continue
            x = a[mask].astype(float)
            y = b[mask].astype(float)
            cols = [c for c, keep in zip(range(c0, c1), mask.tolist()) if keep]

            if _row_pair_low_cardinality_integer_like(x, y):
                continue

            non_integer_pairs = sum(
                1 for xv, yv in zip(x, y)
                if _has_fractional_part(xv) or _has_fractional_part(yv)
            )
            if non_integer_pairs < max(4, math.ceil(0.25 * n)):
                continue

            changed_mask = ~_isclose_rowwise(x, y, rtol=1e-9)
            changed = int(changed_mask.sum())
            if changed / n < 0.5:
                continue

            same_decimal1 = 0
            same_ones = 0
            same_ones_decimal1 = 0
            coarse_10_diff = 0
            examples = []
            diffs = []
            for col, xv, yv, is_changed in zip(cols, x, y, changed_mask.tolist()):
                dec_same = _decimal_digit(xv, 1) == _decimal_digit(yv, 1)
                ones_same = _ones_digit(xv) == _ones_digit(yv)
                if dec_same:
                    same_decimal1 += 1
                if ones_same:
                    same_ones += 1
                if dec_same and ones_same:
                    same_ones_decimal1 += 1
                diff = float(yv - xv)
                diffs.append(round(diff, 6))
                if is_changed and _is_multiple_of_ten_diff(diff):
                    coarse_10_diff += 1
                if len(examples) < 8 and dec_same and is_changed:
                    examples.append({
                        "col": col + 1,
                        "header": header[col - c0] if 0 <= col - c0 < len(header) else "",
                        "a": float(xv),
                        "b": float(yv),
                        "diff": diff,
                    })

            frac_decimal1 = same_decimal1 / n
            frac_ones_decimal1 = same_ones_decimal1 / n
            frac_coarse_10 = coarse_10_diff / n
            severity = None
            if (
                n >= 12
                and frac_decimal1 >= 0.90
                and frac_ones_decimal1 >= 0.50
                and changed / n >= 0.50
                and frac_coarse_10 >= 0.50
            ):
                severity = "high"
            elif (
                n >= 12
                and frac_decimal1 >= 0.85
                and changed / n >= 0.50
                and (frac_ones_decimal1 >= 0.45 or frac_coarse_10 >= 0.45)
            ):
                severity = "medium"
            if not severity:
                continue

            top_diffs = Counter(diffs).most_common(6)
            ranked.offer(
                (
                    0 if severity == "high" else 1,
                    -frac_decimal1,
                    -frac_ones_decimal1,
                    -frac_coarse_10,
                    -n,
                ),
                severity,
                _row_pair_finding_builder(
                    label_a=label_a,
                    label_b=label_b,
                    ra=ra,
                    rb=rb,
                    n=n,
                    changed=changed,
                    same_decimal1=same_decimal1,
                    frac_decimal1=frac_decimal1,
                    same_ones=same_ones,
                    same_ones_decimal1=same_ones_decimal1,
                    frac_ones_decimal1=frac_ones_decimal1,
                    coarse_10_diff=coarse_10_diff,
                    frac_coarse_10=frac_coarse_10,
                    top_diffs=top_diffs,
                    examples=examples,
                    severity=severity,
                ),
            )

    omitted = ranked.drain(emit)
    if with_coverage:
        return findings, {"findings_omitted": omitted}
    return findings


# Above this many pairwise column relations in ONE block, the sheet is a dense /
# correlated matrix (correlation tables, normalized replicate panels) where identical or
# linear columns are expected by construction rather than an independent duplication
# signal. One proteomics sheet produced ~20,000 such 'high' relations, obscuring
# review-relevant signals.
RELATION_FLOOD_CAP = 40

# Above this many within-column findings on ONE (file, sheet), the sheet is a large
# data table whose columns are repetitive by construction (categorical codes, dose
# grids, few-value panels). Review-relevant within-col signals live in low-count sheets
# (offline corpus: those sheets held <=2 within_col each), so a sheet-wide
# flood is noise — demote it wholesale instead of flooding the judge.
WITHIN_COL_SHEET_CAP = 25


def _demote_within_col_flood(within_col, cap=WITHIN_COL_SHEET_CAP):
    """Demote a per-sheet flood of within-column findings to low severity, dropping them
    from the packet (prefilter='drop'). Kept in scan.json (reversible via forensic).
    Mutates + returns the same list."""
    if len(within_col) <= cap:
        return within_col
    for f in within_col:
        f["severity"] = "low"
        f["prefilter"] = "drop"
        f["prefilter_reason"] = "within_col_sheet_flood"
        f["within_col_flood_sheet"] = True
    return within_col


def _demote_dense_relations(relations, cap=RELATION_FLOOD_CAP):
    """Demote a flood of pairwise column relations to low severity (tagging them
    ``dense_block``) so a dense matrix stops dominating high-severity output. Findings
    are kept, not dropped — just down-weighted. Returns the same list."""
    if len(relations) <= cap:
        return relations
    for r in relations:
        r["severity"] = "low"
        r["dense_block"] = True
    return relations


def _demote_dense_sheets(
    report_blocks, cap=RELATION_FLOOD_CAP, profile="review"
):
    """Apply the dense-flood demotion per (file, sheet), not per block: a dense matrix
    is split into many numeric blocks, each holding only part of the column relations,
    so the flood must be judged by the SHEET total. Mutates findings in place."""
    if normalize_profile(profile) == "forensic":
        return report_blocks

    by_sheet = {}
    for b in report_blocks:
        key = (b["file"], b["sheet"])
        agg = by_sheet.setdefault(key, {"relations": [], "equal_pairs": [], "within_col": []})
        agg["relations"].extend(b.get("relations", []))
        agg["equal_pairs"].extend(b.get("equal_pairs", []))
        agg["within_col"].extend(b.get("within_col", []))
    for agg in by_sheet.values():
        _demote_dense_relations(agg["relations"], cap)   # same dict objects as in blocks
        _demote_dense_relations(agg["equal_pairs"], cap)
        _demote_within_col_flood(agg["within_col"])      # per-sheet within-col flood gate
    return report_blocks


def _demote_reused_progressions(report_blocks, profile="review"):
    """A perfect arithmetic progression that is REUSED — the identical (step, n, first)
    appears in >=2 numeric blocks/sheets — is an independent-variable axis re-plotted across
    panels (magnetic-field / 2-theta / time / dose / wavelength sweep).
    Reuse across panels supports treating the progression as an axis.
    Demote these out of the high/medium review priority (kept in scan.json, reversible via
    forensic). A ONE-OFF perfect progression keeps its severity as a review-relevant
    linear-fill signal (and matches the golden fixture's single ap_col). Mutates in place
    and returns report_blocks."""
    if normalize_profile(profile) == "forensic":
        return report_blocks

    sig_count = {}
    progs = []
    for b in report_blocks:
        for f in b.get("progressions", []):
            if f.get("kind") != "arithmetic_progression":
                continue
            sig = (round(float(f.get("step", 0.0)), 9), f.get("n"),
                   round(float(f.get("first", 0.0)), 9))
            sig_count[sig] = sig_count.get(sig, 0) + 1
            progs.append((sig, f))
    for sig, f in progs:
        if sig_count.get(sig, 0) >= 2:
            f["severity"] = "low"
            f["reused_progression"] = True
            f["prefilter"] = "drop"
            f["prefilter_reason"] = "reused_progression_axis"
            note = benign_reason(f)               # runs AFTER _attach_benign, so set it here
            if note:
                f["likely_benign"] = note
    return report_blocks


def detect_arithmetic_progression(
    sheet,
    r0,
    r1,
    c0,
    c1,
    header,
    *,
    _resources=None,
    _finding_sink=None,
):
    findings, emit = _finding_emitter(
        "progressions", _finding_sink
    )
    resources = _resources or _DenseFamilyResources.unlimited(
        "arithmetic_progression"
    )
    row_count = r1 - r0
    state_upper_bounds = {
        "column": row_count,
        "numeric_mask": state_units_for_nbytes(row_count),
        "values": row_count,
        "diffs": max(0, row_count - 1),
        "progression_close_workspace": 4 * row_count,
    }
    if not resources.begin(
        row_count=row_count,
        candidates_total=c1 - c0,
        minimum_candidate_work=row_count,
        state_required=sum(state_upper_bounds.values()),
    ):
        return findings

    for c in range(c0, c1):
        candidate, column_lease, initial_leases = (
            resources.start_allocated_candidate(
                "column",
                row_count,
                row_count,
                emit,
            )
        )
        if candidate is None:
            break
        assert initial_leases == ()

        def run_column_candidate():
            column = candidate.materialize(
                column_lease,
                lambda: col_array(sheet, r0, r1, c),
            )
            numeric_mask, numeric_mask_lease = candidate.allocate(
                "numeric_mask",
                state_units_for_nbytes(row_count),
                lambda: np.isnan(column),
            )
            np.logical_not(numeric_mask, out=numeric_mask)
            values, values_lease = candidate.allocate(
                "values",
                row_count,
                lambda: column[numeric_mask],
            )
            del numeric_mask, column
            candidate.release(numeric_mask_lease)
            candidate.release(column_lease)
            n = len(values)
            if n < 5:
                del values
                candidate.release(values_lease)
                return
            diffs, diffs_lease = candidate.allocate(
                "diffs",
                max(0, n - 1),
                lambda: np.diff(values),
            )
            source_ulp_tolerance = max_ulp_tolerance(values)
            close_workspace = candidate.reserve(
                "progression_close_workspace",
                4 * n,
            )
            closes = np.allclose(
                diffs,
                diffs[0],
                atol=source_ulp_tolerance,
                rtol=1e-9,
            )
            candidate.release(close_workspace)
            step = float(diffs[0])
            first = float(values[0])
            del diffs, values
            candidate.release(diffs_lease)
            candidate.release(values_lease)
            if closes and abs(step) > source_ulp_tolerance:
                sev = (
                    "medium"
                    if abs(step - round(step)) < 1e-9
                    else "high"
                )
                candidate.offer(
                    sev,
                    lambda c=c, n=n, step=step,
                    first=first, sev=sev: dict(
                    kind="arithmetic_progression",
                    col=header[c - c0],
                    col_idx=c,
                    block_c0=c0,
                    n=int(n),
                    step=step,
                    first=first,
                    severity=sev,
                    rule=(
                        f"col[{c}] = arithmetic progression, "
                        f"step={step:.6g}"
                    ),
                    ),
                )

        with candidate:
            run_column_candidate()
        if candidate.rejected:
            break
    return findings


def _numpy_frequency_summary(values):
    unique, first_indices, counts = np.unique(
        values,
        return_index=True,
        return_counts=True,
    )
    order = np.lexsort((first_indices, -counts))
    return unique, counts, order


class _DenseStateTracker:
    """Track proportional detector state in float64-equivalent 8-byte units."""

    def __init__(self):
        self._live = {}
        self.live_units = 0
        self.peak_units = 0
        self.seen_names = set()

    def retain(self, name, *arrays):
        units = sum(
            (max(0, int(array.nbytes)) + 7) // 8
            for array in arrays
        )
        self.retain_units(name, units)

    def retain_units(self, name, units):
        self.release(name)
        units = max(0, int(units))
        self._live[name] = units
        self.live_units += units
        self.peak_units = max(self.peak_units, self.live_units)
        self.seen_names.add(name)

    def release(self, *names):
        for name in names:
            self.live_units -= self._live.pop(name, 0)

    def release_all(self):
        self._live.clear()
        self.live_units = 0


def detect_within_column_patterns(
    sheet,
    r0,
    r1,
    c0,
    c1,
    header,
    min_n=6,
    *,
    _resources=None,
    _finding_sink=None,
):
    """Detect within-column anomalies:
       - many identical values in one column (Su Jiacao: '13 中 8 个相同')
       - many values sharing same last-2 decimals (Su Jiacao: '13 中 11 个末两位相同')
       - too many .0 / .5 endings (Su Jiacao: '71 个中 51 个末位 0 或 5')
       - missing last digits (Su Jiacao: '70 个数据中末位完全没有 3 或 7')
    """
    findings, emit = _finding_emitter("within_col", _finding_sink)
    resources = _resources or _DenseFamilyResources.unlimited(
        "within_column"
    )
    row_count = r1 - r0
    state_upper_bounds = {
        "column": row_count,
        "numeric_mask": state_units_for_nbytes(row_count),
        "values": row_count,
        "rounded": row_count,
        "frequency_workspace": 8 * row_count,
        "unique": row_count,
        "counts": row_count,
        "order": row_count,
        "integer_workspace": 3 * row_count,
    }
    if not resources.begin(
        row_count=row_count,
        candidates_total=c1 - c0,
        minimum_candidate_work=row_count,
        state_required=sum(state_upper_bounds.values()),
    ):
        return findings

    for c in range(c0, c1):
        candidate, column_lease, initial_leases = (
            resources.start_allocated_candidate(
                "column",
                row_count,
                row_count,
                emit,
            )
        )
        if candidate is None:
            break
        assert initial_leases == ()

        def run_column_candidate():
            column = candidate.materialize(
                column_lease,
                lambda: col_array(sheet, r0, r1, c),
            )
            numeric_mask, numeric_mask_lease = candidate.allocate(
                "numeric_mask",
                state_units_for_nbytes(row_count),
                lambda: np.isnan(column),
            )
            np.logical_not(numeric_mask, out=numeric_mask)
            values, values_lease = candidate.allocate(
                "values",
                row_count,
                lambda: column[numeric_mask],
            )
            del numeric_mask, column
            candidate.release(numeric_mask_lease)
            candidate.release(column_lease)
            n = len(values)
            if n < min_n:
                del values
                candidate.release(values_lease)
                return
            col_name = (
                header[c - c0]
                if c - c0 < len(header)
                else f"col{c}"
            )

            rounded, rounded_lease = candidate.allocate(
                "rounded",
                n,
                lambda: np.round(values, 4),
            )
            frequency_workspace = candidate.reserve(
                "frequency_workspace",
                8 * n,
            )
            unique_lease = candidate.reserve("unique", n)
            counts_lease = candidate.reserve("counts", n)
            order_lease = candidate.reserve("order", n)
            unique, value_counts, value_order = (
                _numpy_frequency_summary(rounded)
            )
            unique_lease.validate_nbytes(unique.nbytes)
            counts_lease.validate_nbytes(value_counts.nbytes)
            order_lease.validate_nbytes(value_order.nbytes)
            candidate.release(frequency_workspace)
            n_distinct = int(len(unique))

            integer_workspace = candidate.reserve(
                "integer_workspace",
                3 * n,
            )
            all_integer = bool(
                np.all(
                    np.abs(values - np.round(values)) < 1e-9
                )
            )
            candidate.release(integer_workspace)

            top_index = int(value_order[0])
            top_val = float(unique[top_index])
            top_count = int(value_counts[top_index])
            value_sample = tuple(
                float(unique[index])
                for index in value_order[:8]
            )
            del rounded, unique, value_counts, value_order
            candidate.release(rounded_lease)
            candidate.release(unique_lease)
            candidate.release(counts_lease)
            candidate.release(order_lease)
            if (
                top_count >= max(4, n // 2)
                and n - top_count >= 1
            ):
                candidate.offer(
                    "high",
                    lambda c=c, col_name=col_name, n=n,
                    top_val=top_val, top_count=top_count,
                    n_distinct=n_distinct,
                    all_integer=all_integer,
                    value_sample=value_sample: dict(
                        kind="within_col_value_duplication",
                        col=col_name,
                        col_idx=c,
                        n=n,
                        dup_value=float(top_val),
                        dup_count=int(top_count),
                        frac_repeat=top_count / n,
                        n_distinct=n_distinct,
                        all_integer=all_integer,
                        value_sample=list(value_sample),
                        severity="high",
                        rule=(
                            f"col[{c}] has value {top_val} repeated "
                            f"{top_count}/{n} times"
                        ),
                    ),
                )

            ec = Counter()
            ending_count = 0
            for value in values:
                ending = trailing_decimal_digits(value, 2)
                if ending is not None:
                    ec[ending] += 1
                    ending_count += 1
            if ending_count >= max(min_n, 8):
                top_end, top_end_count = ec.most_common(1)[0]
                if top_end_count >= max(
                    5, 2 * ending_count // 3
                ):
                    candidate.offer(
                        "high",
                        lambda c=c, col_name=col_name,
                        ending_count=ending_count,
                        top_end=top_end,
                        top_end_count=top_end_count,
                        n_distinct=n_distinct,
                        all_integer=all_integer,
                        value_sample=value_sample: dict(
                            kind="within_col_decimal_repetition",
                            col=col_name,
                            col_idx=c,
                            n=ending_count,
                            ending=top_end,
                            count=int(top_end_count),
                            frac_repeat=(
                                top_end_count / ending_count
                            ),
                            n_distinct=n_distinct,
                            all_integer=all_integer,
                            value_sample=list(value_sample),
                            severity="high",
                            rule=(
                                f"col[{c}]: "
                                f"{top_end_count}/{ending_count} "
                                "values share last-2 decimals "
                                f"'.{top_end}'"
                            ),
                        ),
                    )

            last_digit_counts = Counter()
            last_digit_count = 0
            for value in values:
                digit = last_significant_digit(value)
                if digit is not None:
                    last_digit_counts[digit] += 1
                    last_digit_count += 1
            del values
            candidate.release(values_lease)
            if last_digit_count >= max(min_n, 10):
                zeros_fives = (
                    last_digit_counts["0"]
                    + last_digit_counts["5"]
                )
                if zeros_fives >= max(
                    7, 0.7 * last_digit_count
                ):
                    candidate.offer(
                        "medium",
                        lambda c=c, col_name=col_name,
                        last_digit_count=last_digit_count,
                        zeros_fives=zeros_fives: dict(
                            kind="rounded_to_half_or_int",
                            col=col_name,
                            col_idx=c,
                            n=last_digit_count,
                            count_05=int(zeros_fives),
                            severity="medium",
                            rule=(
                                f"col[{c}]: {zeros_fives}/"
                                f"{last_digit_count} values end in "
                                "0 or 5"
                            ),
                        ),
                    )

            if last_digit_count >= 20:
                present = set(last_digit_counts)
                missing = [
                    digit
                    for digit in "123456789"
                    if digit not in present
                ]
                if missing and len(present) <= 6:
                    candidate.offer(
                        "medium",
                        lambda c=c, col_name=col_name,
                        last_digit_count=last_digit_count,
                        missing=tuple(missing): dict(
                            kind="missing_last_digits",
                            col=col_name,
                            col_idx=c,
                            n=last_digit_count,
                            missing=list(missing),
                            severity="medium",
                            rule=(
                                f"col[{c}]: last digits "
                                f"{list(missing)} never appear in "
                                f"{last_digit_count} values"
                            ),
                        ),
                    )

        with candidate:
            run_column_candidate()
        if candidate.rejected:
            break
    return findings


def detect_dispersed_repeats(
    sheet,
    r0,
    r1,
    c0,
    c1,
    header,
    min_n=30,
    *,
    _resources=None,
    _finding_sink=None,
):
    """Many DISTINCT high-precision values each repeated across DISPERSED rows.

    Complements within_col_value_duplication (single dominant value). Targets a
    continuous, high-precision column whose exact-duplicate mass far exceeds the
    near-zero birthday expectation, where repeats are scattered across the table
    (not adjacent fill-down / technical replicates). Thresholds are conservative
    defaults pinned by tests; not env-tunable.
    """
    findings, emit = _finding_emitter("within_col", _finding_sink)
    resources = _resources or _DenseFamilyResources.unlimited(
        "dispersed_repeats"
    )
    row_count = r1 - r0
    bool_units = state_units_for_nbytes(row_count)
    state_upper_bounds = {
        "numeric_mask": bool_units,
        "rows": row_count,
        "values": row_count,
        "integer_gate_workspace": 3 * row_count,
        "rounded": row_count,
        "frequency_workspace": 8 * row_count,
        "unique_all": row_count,
        "counts_all": row_count,
        "order_all": row_count,
        "core_mask": bool_units,
        "core_rows": row_count,
        "core_values": row_count,
        "decimal_places": bool_units,
        "precision_gate": bool_units,
        "rounded_core": row_count,
        "unique_workspace": 10 * row_count,
        "unique_core": row_count,
        "first_core": row_count,
        "inverse": row_count,
        "counts": row_count,
        "partition_workspace": row_count,
        "sort_workspace": 3 * row_count,
        "sorted_positions": row_count,
        "group_start_workspace": 2 * row_count,
        "group_starts": row_count,
        "group_rows": row_count,
        "group_diffs": row_count,
        "group_gaps": bool_units,
        "sample_rounded": row_count,
        "sample_frequency_workspace": 8 * row_count,
        "sample_unique": row_count,
        "sample_counts": row_count,
        "sample_order": row_count,
    }
    if not resources.begin(
        row_count=row_count,
        candidates_total=c1 - c0,
        minimum_candidate_work=row_count,
        state_required=sum(state_upper_bounds.values()),
    ):
        return findings

    def _dec_places(v):
        s = f"{v:.10f}".rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0

    for c in range(c0, c1):
        column = sheet.numeric[r0:r1, c]
        candidate, numeric_mask_lease, initial_leases = (
            resources.start_allocated_candidate(
                "numeric_mask",
                state_units_for_nbytes(row_count),
                row_count,
                emit,
            )
        )
        if candidate is None:
            break
        assert initial_leases == ()

        def run_column_candidate():
            numeric_mask = candidate.materialize(
                numeric_mask_lease,
                lambda: np.isnan(column),
            )
            np.logical_not(numeric_mask, out=numeric_mask)
            rows, rows_lease = candidate.allocate(
                "rows",
                row_count,
                lambda: np.flatnonzero(numeric_mask),
            )
            rows += r0
            values, values_lease = candidate.allocate(
                "values",
                row_count,
                lambda: column[numeric_mask],
            )
            del numeric_mask
            candidate.release(numeric_mask_lease)
            n = int(len(values))
            if n < min_n:
                del rows, values
                candidate.release(rows_lease)
                candidate.release(values_lease)
                return

            integer_workspace = candidate.reserve(
                "integer_gate_workspace",
                3 * n,
            )
            all_integer = bool(
                np.all(
                    np.abs(values - np.round(values)) < 1e-9
                )
            )
            candidate.release(integer_workspace)
            if all_integer:
                del rows, values
                candidate.release(rows_lease)
                candidate.release(values_lease)
                return

            rounded, rounded_lease = candidate.allocate(
                "rounded",
                n,
                lambda: np.round(values, 6),
            )
            frequency_workspace = candidate.reserve(
                "frequency_workspace",
                8 * n,
            )
            unique_all_lease = candidate.reserve(
                "unique_all", n
            )
            counts_all_lease = candidate.reserve(
                "counts_all", n
            )
            order_all_lease = candidate.reserve(
                "order_all", n
            )
            unique_all, counts_all, order_all = (
                _numpy_frequency_summary(rounded)
            )
            unique_all_lease.validate_nbytes(unique_all.nbytes)
            counts_all_lease.validate_nbytes(counts_all.nbytes)
            order_all_lease.validate_nbytes(order_all.nbytes)
            candidate.release(frequency_workspace)
            top_index = int(order_all[0])
            top_v = unique_all[top_index]
            top_c = int(counts_all[top_index])
            boundary = top_v if top_c > 0.25 * n else None
            core_mask, core_mask_lease = candidate.allocate(
                "core_mask",
                state_units_for_nbytes(n),
                lambda: (
                    np.ones(n, dtype=np.bool_)
                    if boundary is None
                    else rounded != boundary
                ),
            )
            core_rows, core_rows_lease = candidate.allocate(
                "core_rows",
                n,
                lambda: rows[core_mask],
            )
            core_values, core_values_lease = candidate.allocate(
                "core_values",
                n,
                lambda: values[core_mask],
            )
            m = int(len(core_values))
            del rows, values, rounded, unique_all
            del counts_all, order_all, core_mask
            candidate.release(rows_lease)
            candidate.release(values_lease)
            candidate.release(rounded_lease)
            candidate.release(unique_all_lease)
            candidate.release(counts_all_lease)
            candidate.release(order_all_lease)
            candidate.release(core_mask_lease)
            if m < min_n:
                del core_rows, core_values
                candidate.release(core_rows_lease)
                candidate.release(core_values_lease)
                return

            decimal_places, decimal_places_lease = (
                candidate.allocate(
                    "decimal_places",
                    state_units_for_nbytes(m),
                    lambda: np.fromiter(
                        (
                            _dec_places(value)
                            for value in core_values
                        ),
                        dtype=np.uint8,
                        count=m,
                    ),
                )
            )
            precision_gate, precision_gate_lease = (
                candidate.allocate(
                    "precision_gate",
                    state_units_for_nbytes(m),
                    lambda: np.greater_equal(
                        decimal_places, 2
                    ),
                )
            )
            frac_hi_prec = (
                float(np.count_nonzero(precision_gate)) / m
            )
            del precision_gate
            candidate.release(precision_gate_lease)
            if frac_hi_prec < 0.6:
                return

            rounded_core, rounded_core_lease = candidate.allocate(
                "rounded_core",
                m,
                lambda: np.round(core_values, 6),
            )
            unique_workspace = candidate.reserve(
                "unique_workspace",
                10 * m,
            )
            unique_core_lease = candidate.reserve(
                "unique_core", m
            )
            first_core_lease = candidate.reserve(
                "first_core", m
            )
            inverse_lease = candidate.reserve("inverse", m)
            counts_lease = candidate.reserve("counts", m)
            (
                unique_core,
                first_core,
                inverse_core,
                counts_core,
            ) = np.unique(
                rounded_core,
                return_index=True,
                return_inverse=True,
                return_counts=True,
            )
            unique_core_lease.validate_nbytes(unique_core.nbytes)
            first_core_lease.validate_nbytes(first_core.nbytes)
            inverse_lease.validate_nbytes(inverse_core.nbytes)
            counts_lease.validate_nbytes(counts_core.nbytes)
            candidate.release(unique_workspace)
            distinct = int(len(unique_core))
            if distinct < 50 or distinct / m < 0.3:
                return

            partition_workspace = candidate.reserve(
                "partition_workspace",
                m,
            )
            med_dp = int(np.partition(
                decimal_places,
                len(decimal_places) // 2,
            )[len(decimal_places) // 2])
            candidate.release(partition_workspace)
            support = (
                float(np.max(core_values))
                - float(np.min(core_values))
            ) * (10 ** med_dp)
            if support < 20 * m:
                return

            block_h = r1 - r0
            sort_workspace = candidate.reserve(
                "sort_workspace",
                3 * m,
            )
            sorted_positions, sorted_positions_lease = (
                candidate.allocate(
                    "sorted_positions",
                    m,
                    lambda: np.argsort(
                        inverse_core, kind="stable"
                    ),
                )
            )
            candidate.release(sort_workspace)
            distinct_count = len(counts_core)
            group_start_workspace = candidate.reserve(
                "group_start_workspace",
                2 * distinct_count,
            )
            group_starts, group_starts_lease = (
                candidate.allocate(
                    "group_starts",
                    distinct_count,
                    lambda: np.cumsum(
                        np.concatenate((
                            np.array([0], dtype=np.int64),
                            counts_core[:-1],
                        ))
                    ),
                )
            )
            candidate.release(group_start_workspace)
            top_groups = []
            dispersed_count = 0
            dup_cells = 0
            for group_index, group_count in enumerate(counts_core):
                group_count = int(group_count)
                if group_count < 2:
                    continue
                start = int(group_starts[group_index])
                stop = start + group_count
                group_rows, group_rows_lease = candidate.allocate(
                    "group_rows",
                    group_count,
                    lambda: core_rows[
                        sorted_positions[start:stop]
                    ],
                )
                span = int(group_rows[-1] - group_rows[0])
                group_diffs, group_diffs_lease = (
                    candidate.allocate(
                        "group_diffs",
                        group_count,
                        lambda: np.diff(group_rows),
                    )
                )
                group_gaps, group_gaps_lease = (
                    candidate.allocate(
                        "group_gaps",
                        state_units_for_nbytes(group_count),
                        lambda: np.greater(group_diffs, 1),
                    )
                )
                non_adjacent = bool(np.any(group_gaps))
                del group_gaps
                candidate.release(group_gaps_lease)
                del group_diffs
                candidate.release(group_diffs_lease)
                if span >= 0.5 * block_h and non_adjacent:
                    dispersed_count += 1
                    dup_cells += group_count
                    top_groups.append((
                        group_count,
                        int(first_core[group_index]),
                        group_index,
                    ))
                    top_groups.sort(
                        key=lambda item: (-item[0], item[1])
                    )
                    del top_groups[3:]
                del group_rows
                candidate.release(group_rows_lease)

            if (
                dispersed_count < 10
                or dup_cells < 0.15 * m
            ):
                return
            col_name = (
                header[c - c0]
                if c - c0 < len(header)
                else f"col{c}"
            )

            sample_rounded, sample_rounded_lease = (
                candidate.allocate(
                    "sample_rounded",
                    m,
                    lambda: np.round(core_values, 4),
                )
            )
            sample_frequency_workspace = candidate.reserve(
                "sample_frequency_workspace",
                8 * m,
            )
            sample_unique_lease = candidate.reserve(
                "sample_unique", m
            )
            sample_counts_lease = candidate.reserve(
                "sample_counts", m
            )
            sample_order_lease = candidate.reserve(
                "sample_order", m
            )
            (
                sample_unique,
                sample_counts,
                sample_order,
            ) = _numpy_frequency_summary(sample_rounded)
            sample_unique_lease.validate_nbytes(
                sample_unique.nbytes
            )
            sample_counts_lease.validate_nbytes(
                sample_counts.nbytes
            )
            sample_order_lease.validate_nbytes(
                sample_order.nbytes
            )
            candidate.release(sample_frequency_workspace)
            n_distinct = int(len(sample_unique))
            value_sample = tuple(
                float(sample_unique[index])
                for index in sample_order[:8]
            )
            example_cells = []
            for group_count, _first, group_index in top_groups:
                start = int(group_starts[group_index])
                for offset in range(min(group_count, 8)):
                    position = int(
                        sorted_positions[start + offset]
                    )
                    example_cells.append((
                        int(core_rows[position]) + 1,
                        c + 1,
                    ))
            example_cells = tuple(example_cells)
            del sample_rounded, sample_unique
            del sample_counts, sample_order
            candidate.release(sample_rounded_lease)
            candidate.release(sample_unique_lease)
            candidate.release(sample_counts_lease)
            candidate.release(sample_order_lease)
            del decimal_places, rounded_core, unique_core
            del first_core, inverse_core, counts_core
            candidate.release(decimal_places_lease)
            candidate.release(rounded_core_lease)
            candidate.release(unique_core_lease)
            candidate.release(first_core_lease)
            candidate.release(inverse_lease)
            candidate.release(counts_lease)
            del core_values, core_rows
            del sorted_positions, group_starts
            candidate.release(core_values_lease)
            candidate.release(core_rows_lease)
            candidate.release(sorted_positions_lease)
            candidate.release(group_starts_lease)

            candidate.offer(
                "medium",
                lambda c=c, col_name=col_name, m=m,
                dispersed_count=dispersed_count,
                dup_cells=dup_cells,
                n_distinct=n_distinct,
                value_sample=value_sample,
                example_cells=example_cells: dict(
                    kind="within_col_dispersed_repeats",
                    col=col_name,
                    col_idx=c,
                    n=m,
                    n_repeat_groups=dispersed_count,
                    dup_cells=dup_cells,
                    frac_repeat=dup_cells / m,
                    n_distinct=n_distinct,
                    all_integer=False,
                    value_sample=list(value_sample),
                    example_cells=list(example_cells),
                    severity="medium",
                    rule=(
                        f"col[{c}]: {dispersed_count} distinct "
                        "high-precision values each recur across "
                        f"dispersed rows ({dup_cells}/{m} cells)"
                    ),
                ),
            )

        with candidate:
            run_column_candidate()
        if candidate.rejected:
            break
    return findings


def detect_identical_after_rounding(
    sheet,
    r0,
    r1,
    c0,
    c1,
    header,
    *,
    _resources=None,
    _finding_sink=None,
):
    """Detect pairs/groups of cells that differ at higher precision but match at lower (e.g.
    4.2735 vs 4.2812 — both round to 4.3). Kang Tiebang ED6h/6j signal."""
    findings, emit = _finding_emitter(
        "identical_after_rounding", _finding_sink
    )
    resources = _resources or _DenseFamilyResources.unlimited(
        "identical_after_rounding"
    )
    row_count = r1 - r0
    col_count = c1 - c0
    cell_count = row_count * col_count
    state_upper_bounds = {
        "candidate_workspace": 3 * cell_count,
        "candidate_mask": state_units_for_nbytes(cell_count),
        "bucket_workspace": 2 * cell_count,
        "bucket_mask": state_units_for_nbytes(cell_count),
        "flat_indices": cell_count,
        "values": cell_count,
        "rounded": cell_count,
        "unique_workspace": 10 * cell_count,
        "rounded_values": cell_count,
        "first_indices": cell_count,
        "inverse": cell_count,
        "counts": cell_count,
        "sort_workspace": 3 * cell_count,
        "sorted_positions": cell_count,
        "group_start_workspace": 2 * cell_count,
        "group_starts": cell_count,
        "group_values": cell_count,
        "precise_rounded": cell_count,
        "precise_unique_workspace": 4 * cell_count,
        "precise_values": cell_count,
    }
    if not resources.begin(
        row_count=row_count,
        candidates_total=1,
        minimum_candidate_work=cell_count,
        state_required=sum(state_upper_bounds.values()),
    ):
        return findings

    block = sheet.numeric[r0:r1, c0:c1]
    candidate, candidate_mask_lease, initial_leases = (
        resources.start_allocated_candidate(
            "candidate_mask",
            state_units_for_nbytes(cell_count),
            cell_count,
            emit,
            initial_reservations=(
                ("candidate_workspace", 3 * cell_count),
            ),
        )
    )
    if candidate is None:
        return findings

    def run_rounding_candidate():
        candidate_mask = candidate.materialize(
            candidate_mask_lease,
            lambda: (
                ~np.isnan(block) & (np.abs(block) > 1e-9)
            ),
            release_after=initial_leases,
        )
        if int(np.count_nonzero(candidate_mask)) < 20:
            return
        bucket_workspace = candidate.reserve(
            "bucket_workspace",
            2 * cell_count,
        )
        bucket_mask, bucket_mask_lease = candidate.allocate(
            "bucket_mask",
            state_units_for_nbytes(cell_count),
            lambda: candidate_mask & (np.abs(block) < 100),
        )
        candidate.release(bucket_workspace)
        flat_indices, flat_indices_lease = candidate.allocate(
            "flat_indices",
            cell_count,
            lambda: np.flatnonzero(bucket_mask),
        )
        del bucket_mask, candidate_mask
        candidate.release(bucket_mask_lease)
        candidate.release(candidate_mask_lease)
        if len(flat_indices) < 4:
            return
        values, values_lease = candidate.allocate(
            "values",
            cell_count,
            lambda: block.ravel()[flat_indices],
        )
        value_count = len(values)
        rounded, rounded_lease = candidate.allocate(
            "rounded",
            value_count,
            lambda: np.round(values, 1),
        )
        unique_workspace = candidate.reserve(
            "unique_workspace",
            10 * value_count,
        )
        rounded_values_lease = candidate.reserve(
            "rounded_values", value_count
        )
        first_indices_lease = candidate.reserve(
            "first_indices", value_count
        )
        inverse_lease = candidate.reserve(
            "inverse", value_count
        )
        counts_lease = candidate.reserve(
            "counts", value_count
        )
        (
            rounded_values,
            first_indices,
            inverse,
            counts,
        ) = np.unique(
            rounded,
            return_index=True,
            return_inverse=True,
            return_counts=True,
        )
        rounded_values_lease.validate_nbytes(
            rounded_values.nbytes
        )
        first_indices_lease.validate_nbytes(
            first_indices.nbytes
        )
        inverse_lease.validate_nbytes(inverse.nbytes)
        counts_lease.validate_nbytes(counts.nbytes)
        candidate.release(unique_workspace)
        sort_workspace = candidate.reserve(
            "sort_workspace",
            3 * value_count,
        )
        sorted_positions, sorted_positions_lease = (
            candidate.allocate(
                "sorted_positions",
                value_count,
                lambda: np.argsort(inverse, kind="stable"),
            )
        )
        candidate.release(sort_workspace)
        distinct_count = len(counts)
        group_start_workspace = candidate.reserve(
            "group_start_workspace",
            2 * distinct_count,
        )
        group_starts, group_starts_lease = candidate.allocate(
            "group_starts",
            distinct_count,
            lambda: np.cumsum(
                np.concatenate((
                    np.array([0], dtype=np.int64),
                    counts[:-1],
                ))
            ),
        )
        candidate.release(group_start_workspace)

        # Find buckets where multiple DIFFERENT (>1e-4 apart) values map to the same rounded value
        rounding_groups = []
        width = c1 - c0
        for group_index, count in enumerate(counts):
            count = int(count)
            if count < 4:
                continue
            start = int(group_starts[group_index])
            stop = start + count
            positions = sorted_positions[start:stop]
            group_values, group_values_lease = candidate.allocate(
                "group_values",
                count,
                lambda: values[positions],
            )
            precise_rounded, precise_rounded_lease = (
                candidate.allocate(
                    "precise_rounded",
                    count,
                    lambda: np.round(group_values, 4),
                )
            )
            precise_unique_workspace = candidate.reserve(
                "precise_unique_workspace",
                4 * count,
            )
            precise_values, precise_values_lease = (
                candidate.allocate(
                    "precise_values",
                    count,
                    lambda: np.unique(precise_rounded),
                )
            )
            candidate.release(precise_unique_workspace)
            if len(precise_values) >= 3:
                example_values = tuple(
                    float(value) for value in precise_values[:6]
                )
                example_cells = []
                for position in positions[:6]:
                    flat_index = int(
                        flat_indices[int(position)]
                    )
                    row_offset, col_offset = divmod(
                        flat_index, width
                    )
                    example_cells.append((
                        r0 + row_offset + 1,
                        c0 + col_offset + 1,
                    ))
                rounding_groups.append((
                    count,
                    int(first_indices[group_index]),
                    float(rounded_values[group_index]),
                    int(len(precise_values)),
                    example_values,
                    tuple(example_cells),
                ))
                rounding_groups.sort(
                    key=lambda item: (-item[0], item[1])
                )
                del rounding_groups[5:]
            del group_values, precise_rounded, precise_values
            candidate.release(group_values_lease)
            candidate.release(precise_rounded_lease)
            candidate.release(precise_values_lease)
        if rounding_groups:
            for (
                count,
                _first_index,
                rounded_value,
                unique_count,
                example_values,
                example_cells,
            ) in rounding_groups:
                candidate.offer(
                    "medium",
                    lambda count=count,
                    rounded_value=rounded_value,
                    unique_count=unique_count,
                    example_values=example_values,
                    example_cells=example_cells: dict(
                        kind="identical_after_rounding",
                        rounded_to=rounded_value,
                        n_cells=count,
                        n_unique=unique_count,
                        example_values=list(example_values),
                        example_cells=list(example_cells),
                        severity="medium",
                        rule=(
                            f"{count} cells share rounded value "
                            f"{rounded_value} but have {unique_count} "
                            "distinct precise values"
                        ),
                    ),
                )
        del rounded_values, first_indices, inverse, counts
        del sorted_positions, group_starts, rounded, values
        del flat_indices
        candidate.release(rounded_values_lease)
        candidate.release(first_indices_lease)
        candidate.release(inverse_lease)
        candidate.release(counts_lease)
        candidate.release(sorted_positions_lease)
        candidate.release(group_starts_lease)
        candidate.release(rounded_lease)
        candidate.release(values_lease)
        candidate.release(flat_indices_lease)

    with candidate:
        run_rounding_candidate()
    if candidate.rejected:
        return findings
    return findings


def detect_grim_grimmer(
    sheet, r0, r1, c0, c1, header, *, _finding_sink=None
):
    """GRIM/GRIMMER: flag reported means (and SDs) impossible for integer-valued
    data at the stated n. Strictly gated — needs a header-located mean+n group
    AND a count/score keyword in the MEAN column header signalling integer items —
    to stay false-positive-safe on continuous measurements where GRIM does not apply.
    GRIMMER runs only on a true SD column (SEM/SE columns are deliberately ignored,
    since GRIMMER is undefined for a standard error)."""
    findings, emit = _finding_emitter("grim", _finding_sink)
    for mean_i, n_i, sd_i in _grim_column_groups(header):
        mean_c, n_c = c0 + mean_i, c0 + n_i
        sd_c = c0 + sd_i if sd_i is not None else None
        grim_fail_count = grimmer_fail_count = 0
        grim_examples = []
        grimmer_examples = []
        checked = grimmer_checked = 0
        for r in range(r0, r1):
            mv = sheet.cell(r, mean_c)
            nv = sheet.cell(r, n_c)
            if not (is_num(mv) and is_num(nv)):
                continue
            n = int(round(float(nv)))
            if n < 2:
                continue
            mean = float(mv)
            d = _decimals_of(mean)
            if n >= 10 ** d:                 # power gate: no discriminating power
                continue
            checked += 1
            if not grim_consistent(mean, n, d):
                grim_fail_count += 1
                if len(grim_examples) < 8:
                    grim_examples.append((r, mean, n, d))
                continue                     # GRIM-failing rows are not re-reported
            if sd_c is not None:
                sv = sheet.cell(r, sd_c)
                if is_num(sv):
                    sd = float(sv)
                    ds = _decimals_of(sd)
                    grimmer_checked += 1
                    if not grimmer_consistent(mean, sd, n, d, ds):
                        grimmer_fail_count += 1
                        if len(grimmer_examples) < 8:
                            grimmer_examples.append(
                                (r, mean, sd, n, ds)
                            )

        mean_name = str(header[mean_i] or f"col{mean_c}")
        n_name = str(header[n_i] or f"col{n_c}")
        sd_name = str(header[sd_i] or f"col{sd_c}") if sd_i is not None else None

        if grim_fail_count:
            def build_grim_finding(
                mean_name=mean_name,
                n_name=n_name,
                sd_name=sd_name,
                mean_c=mean_c,
                sd_c=sd_c,
                checked=checked,
                n_failed=grim_fail_count,
                grim_fail=tuple(grim_examples),
            ):
                finding = dict(
                    kind="grim_inconsistent",
                    severity="high",
                    mean_col=mean_name,
                    n_col=n_name,
                    sd_col=sd_name,
                    col_a_idx=mean_c,
                    n=checked,
                    n_rows_checked=checked,
                    n_failed=n_failed,
                    failed_rows=[
                        dict(
                            row=r + 1,
                            mean=m,
                            n=nn,
                            decimals=dd,
                            nearest_consistent=round(
                                round(m * nn) / nn, dd
                            ),
                        )
                        for (r, m, nn, dd) in grim_fail[:8]
                    ],
                    example_cells=[
                        [r + 1, mean_c + 1]
                        for (r, *_rest) in grim_fail[:8]
                    ],
                    rule=(
                        f"{n_failed}/{checked} rows report a "
                        "mean impossible for integer data at the "
                        f"stated n (GRIM): col '{mean_name}'"
                    ),
                )
                if sd_c is not None:
                    finding["col_b_idx"] = sd_c
                return finding

            emit("high", build_grim_finding)
        if grimmer_fail_count:
            emit(
                "high",
                lambda mean_name=mean_name, n_name=n_name,
                sd_name=sd_name, mean_c=mean_c, sd_c=sd_c,
                grimmer_checked=grimmer_checked,
                n_failed=grimmer_fail_count,
                grimmer_fail=tuple(grimmer_examples): dict(
                    kind="grimmer_inconsistent",
                    severity="high",
                    mean_col=mean_name,
                    n_col=n_name,
                    sd_col=sd_name,
                    col_a_idx=mean_c,
                    col_b_idx=sd_c,
                    n=grimmer_checked,
                    n_rows_checked=grimmer_checked,
                    n_failed=n_failed,
                    failed_rows=[
                        dict(
                            row=r + 1,
                            mean=m,
                            sd=s,
                            n=nn,
                            sd_decimals=ds,
                        )
                        for (r, m, s, nn, ds)
                        in grimmer_fail[:8]
                    ],
                    example_cells=[
                        [r + 1, sd_c + 1]
                        for (r, *_rest) in grimmer_fail[:8]
                    ],
                    rule=(
                        f"{n_failed}/{grimmer_checked} "
                        "rows report an SD impossible for integer "
                        "data at the stated mean & n (GRIMMER): "
                        f"col '{sd_name}'"
                    ),
                ),
            )
    return findings


def detect_last_digit(values, label):
    counts = Counter()
    n = 0
    for value in values:
        digit = last_significant_digit(value)
        if digit is None or digit == "0":
            continue
        counts[int(digit)] += 1
        n += 1
    if n < 40:
        return None
    obs = np.array([counts.get(d, 0) for d in range(1, 10)], dtype=float)
    expected = np.full(9, obs.sum() / 9.0)
    chi2 = ((obs - expected) ** 2 / expected).sum()
    p = float(1 - stats.chi2.cdf(chi2, df=8))
    most_common = counts.most_common(3)
    return dict(label=label, n=int(obs.sum()), chi2=float(chi2), p=p,
                counts={str(d): int(counts.get(d, 0)) for d in range(0, 10)},
                top=[[str(d), c] for d, c in most_common])


def detect_repeated_decimals(values, label):
    counts = Counter()
    n = 0
    for value in values:
        ending = trailing_decimal_digits(value, 2)
        if ending is None:
            continue
        counts[ending] += 1
        n += 1
    if n < 60:
        return None
    flags = [(e, c) for e, c in counts.most_common(15) if c >= max(5, 5 * n / 100)]
    return dict(label=label, n=n, n_unique=len(counts), top=flags)


def benjamini_hochberg(pvals, alpha=0.05):
    """Benjamini-Hochberg step-up FDR. Returns (adjusted_pvals, significant_flags),
    both in the original order. Adjusted p (q-value) is the BH-corrected p; a sheet
    is significant when its q-value <= alpha. Controls false positives when dozens of
    per-sheet last-digit tests run at once."""
    m = len(pvals)
    if m == 0:
        return [], []
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [1.0] * m
    running_min = 1.0
    for rank in range(m, 0, -1):          # largest p (rank m) down to smallest (rank 1)
        i = order[rank - 1]
        running_min = min(running_min, pvals[i] * m / rank)
        adj[i] = min(running_min, 1.0)
    sig = [adj[i] <= alpha for i in range(m)]
    return adj, sig


def detect_equal_pairs(
    sheet,
    r0,
    r1,
    c0,
    c1,
    header,
    *,
    _resources=None,
    _finding_sink=None,
):
    """Detect column pairs where many rows have identical values
    (e.g. tumor length == tumor width)."""
    findings, emit = _finding_emitter("equal_pairs", _finding_sink)
    resources = _resources or _DenseFamilyResources.unlimited(
        "equal_pairs"
    )
    row_count = r1 - r0
    col_count = c1 - c0
    pair_count = col_count * (col_count - 1) // 2
    if not resources.begin(
        row_count=row_count,
        candidates_total=pair_count,
        minimum_candidate_work=2 * row_count,
        state_required=0,
    ):
        return findings

    for i in range(c1 - c0):
        for j in range(i + 1, c1 - c0):
            candidate = resources.start_candidate(
                2 * row_count,
                emit,
            )
            if candidate is None:
                break
            with candidate:
                pair_stats = candidate.materialize(
                    None,
                    lambda: _numeric_pair_stats(
                        sheet, r0, r1, c0 + i, c0 + j
                    ),
                    completes_source=True,
                )
                n = pair_stats.n
                if n < 6:
                    continue
                eq = pair_stats.equal
                all_equal = pair_stats.all_equal
                if (
                    eq >= max(6, n // 2)
                    and eq / n >= 0.5
                    and not all_equal
                ):
                    severity = (
                        "medium" if eq < n else "high"
                    )
                    candidate.offer(
                        severity,
                        lambda i=i, j=j, n=n, eq=eq,
                        pair_stats=pair_stats,
                        severity=severity: dict(
                            kind="many_equal_pairs",
                            col_a=header[i],
                            col_b=header[j],
                            col_a_idx=c0 + i,
                            col_b_idx=c0 + j,
                            n=n,
                            equal=eq,
                            severity=severity,
                            col_a_sample=_sample_exact(
                                pair_stats.sample_a
                            ),
                            col_b_sample=_sample_exact(
                                pair_stats.sample_b
                            ),
                            rule=(
                                f"col[{c0+i}] == col[{c0+j}] "
                                f"in {eq}/{n} rows"
                            ),
                        ),
                    )
    return findings


# ---------- driver ----------

def _grid_from_rows(
    sheet,
    min_decimal_places=3,
    max_rows=200,
    max_cells=None,
    *,
    retained_cell_limit=None,
    with_coverage=False,
):
    """Build {(r, c): rounded_value} of decimal-bearing numeric cells from a Sheet.
    Only keeps non-integer values with >= min_decimal_places decimals in a sane range —
    these are the values whose bit-identical reuse across tables warrants review."""
    grid = {}
    nm = sheet.numeric
    rmax = max(0, min(sheet.nrows, max_rows))
    cell_limit = None if max_cells is None else max(0, int(max_cells))
    retained_limit = (
        cell_limit
        if retained_cell_limit is None
        else max(0, int(retained_cell_limit))
    )
    cells_required = 0
    cell_limited = False
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
                        if (
                            cell_limit is not None
                            and cells_required >= cell_limit
                        ):
                            cell_limited = True
                            break
                        cells_required += 1
                        if (
                            retained_limit is None
                            or len(grid) < retained_limit
                        ):
                            grid[(ri, ci)] = round(fv, 9)
        if cell_limited:
            break
    meta = {
        "rows_total": sheet.nrows,
        "rows_used": rmax,
        "row_limited": sheet.nrows > rmax,
    }
    if cell_limit is not None:
        meta.update({
            "cells_used": cells_required,
            "cell_limited": cell_limited,
        })
    return (grid, meta) if with_coverage else grid


import re as _re

# Matches a figure id inside a sheet name: an optional "extended/ED/ex" marker
# followed by a figure number, e.g. "Figure 5o", "exFig.6b-e", "ED_Fig8b", " exFig.6i".
_FIG_RE = _re.compile(r"(ext(?:ended)?|ed|ex)?\s*\.?\s*fig(?:ure)?\s*\.?\s*0*(\d+)", _re.I)
_CONTROL_BASELINE_LABEL_RE = _re.compile(
    r"\b(?:control|ctrl|baseline|vehicle|untreated|wt|wild[- ]?type|reference|mock|"
    r"naive|sham|pbs|dmso)\b|参照|对照|基线",
    _re.I,
)
_AXIS_CONTEXT_LABEL_RE = _re.compile(
    r"\b(?:time|day|dose|conc(?:entration)?|wavelength|m/z|mz|position|chr|"
    r"coordinate|coord|index|bin)\b|波长|时间|剂量",
    _re.I,
)


def figure_key(sheet_name):
    """Normalize a sheet name into a figure identity like 'main:5' or 'ext:6'.

    Returns None when no figure number can be parsed (e.g. 'Sheet1'). Two sheets
    with the SAME key are panels of the same display item — sharing data between
    them (a combined growth curve and its per-replicate breakdown) is expected and
    should not read as a cross-experiment duplication.
    """
    if not sheet_name:
        return None
    m = _FIG_RE.search(str(sheet_name))
    if not m:
        return None
    prefix = (m.group(1) or "").lower()
    namespace = "ext" if prefix else "main"
    return f"{namespace}:{m.group(2)}"


@dataclass(frozen=True)
class _CrossSheetPairStats:
    same_position_count: int
    same_position_columns: dict
    shared_cells: tuple
    shared_value_count: int
    shared_value_examples: tuple
    delta: dict


def _cross_sheet_pair_stats(
    ga,
    gb,
    *,
    shared_cell_limit=40,
    budget=None,
    with_coverage=False,
):
    candidate_value_count = len(ga) + len(gb)
    value_visits = 0

    def finish_pair_result(result, **extra_coverage):
        if value_visits > candidate_value_count:
            raise AssertionError(
                "pair value work exceeded its candidate"
            )
        if budget is not None:
            budget.record_values(value_visits)
            budget.skip_values(
                candidate_value_count - value_visits
            )
        coverage = {
            "pair_admitted": True,
            "candidate_value_count": candidate_value_count,
            "value_visits": value_visits,
            **extra_coverage,
        }
        return (result, coverage) if with_coverage else result

    if budget is not None and not budget.begin_pair(
        candidate_value_count
    ):
        coverage = {
            "pair_admitted": False,
            "candidate_value_count": candidate_value_count,
            "value_visits": 0,
        }
        return (None, coverage) if with_coverage else None

    counts_b = Counter()
    for value in gb.values():
        value_visits += 1
        counts_b[value] += 1

    same_position = 0
    modified = 0
    same_position_columns = Counter()
    shared_cells = []
    shared_limit = max(0, int(shared_cell_limit))
    matched_counts = Counter()
    shared_values = 0
    shared_value_count = 0
    shared_example_heap = []
    missing = object()
    for key, value in ga.items():
        value_visits += 1
        other = gb.get(key, missing)
        if other is not missing:
            if other == value:
                same_position += 1
                same_position_columns[key[1]] += 1
                if len(shared_cells) < shared_limit:
                    shared_cells.append((key, value))
            else:
                modified += 1

        matched = matched_counts.get(value, 0)
        if matched >= counts_b.get(value, 0):
            continue
        matched_counts[value] = matched + 1
        shared_values += 1
        if matched:
            continue
        shared_value_count += 1
        numeric_value = float(value)
        if len(shared_example_heap) < 5:
            heapq.heappush(shared_example_heap, -numeric_value)
        elif numeric_value < -shared_example_heap[0]:
            heapq.heapreplace(
                shared_example_heap, -numeric_value
            )

    only_a = len(ga) - shared_values
    only_b = len(gb) - shared_values
    if only_a == 0 and only_b == 0:
        pattern = "perfect_dup"
    elif only_a == 0 or only_b == 0:
        pattern = "superset"
    elif modified > 0:
        pattern = "value_tweaked"
    else:
        pattern = "value_divergent"

    result = _CrossSheetPairStats(
        same_position_count=same_position,
        same_position_columns=dict(same_position_columns),
        shared_cells=tuple(shared_cells),
        shared_value_count=shared_value_count,
        shared_value_examples=tuple(sorted(
            -value for value in shared_example_heap
        )),
        delta={
            "pattern": pattern,
            "modified_cells": modified,
            "shared_values": shared_values,
            "only_in_a": only_a,
            "only_in_b": only_b,
        },
    )
    return finish_pair_result(
        result,
        retained_value_counts=(
            len(counts_b) + len(matched_counts)
        ),
    )


def _value_delta(ga, gb):
    """Characterize HOW two near-duplicate grids differ, so a clean re-plot can be
    distinguished from aligned values that differ at selected positions.

    - modified_cells: same (row,col) position, different value — only meaningful when
      the two tables share a layout and contain aligned value changes.
    - only_in_a / only_in_b: value-multiset members unique to each side (layout-robust).
    - pattern:
        value_tweaked : >=1 aligned cell has a different value
        perfect_dup   : identical value multisets, no in-place edits (clean re-plot)
        superset      : one side's values strictly contain the other's (e.g. an extra
                        replicate column — main shows n=5, extended shows n=6)
        value_divergent : both sides hold values the other lacks (partial overlap)
    """
    return _cross_sheet_pair_stats(ga, gb).delta


def value_tweak_subtype(delta: dict | None) -> str | None:
    """Sub-classify a ``value_tweaked`` cross-sheet overlap from an existing ``_value_delta``
    result, without changing detector output (reads fields only).

    - ``copy_then_edit``: a near-perfect copy with only a handful of cells retyped — the
      strongest manual-edit fingerprint (the page #8 pattern). Worth surfacing to judges.
    - ``block_edit``: a heavier rewrite of a shared block.
    - ``None``: not a ``value_tweaked`` pattern.

    Descriptive only — KEEP/DROP is unchanged; ``perfect_dup`` / ``mass`` / high-fraction
    overlaps stay KEEP-protected exactly as before.
    """
    if not delta or delta.get("pattern") != "value_tweaked":
        return None
    modified = delta.get("modified_cells") or 0
    shared = delta.get("shared_values") or 0
    denom = shared + modified
    if modified <= 3 or (denom and modified / denom <= 0.02):
        return "copy_then_edit"
    return "block_edit"


@dataclass
class CrossSheetWorkBudget:
    pair_limit: int
    value_limit: int
    tail_match_limit: int
    finding_limit: int
    pairs_examined: int = 0
    pairs_skipped: int = 0
    values_examined: int = 0
    values_skipped: int = 0
    tail_matches_retained: int = 0
    tail_matches_skipped_lower_bound: int = 0
    findings_retained: int = 0
    findings_skipped: int = 0
    bucket_findings_skipped: int = 0
    axis_context_available: bool = True
    axis_loading_visits: int = 0
    axis_grouping_visits: int = 0
    axis_progression_visits: int = 0
    axis_fingerprint_visits: int = 0
    axis_recurrence_order_visits: int = 0
    axis_recurrence_group_visits: int = 0
    axis_recurrence_comparison_visits: int = 0
    axis_recurrence_mark_visits: int = 0
    axis_output_visits: int = 0
    axis_work_skipped_lower_bound: int = 0
    axis_work_skipped_is_lower_bound: bool = False
    axis_state_unit_limit: int = 0
    axis_peak_state_units: int = 0
    _limits_reached: set = None

    def __post_init__(self):
        self.pair_limit = max(0, int(self.pair_limit))
        self.value_limit = max(0, int(self.value_limit))
        self.tail_match_limit = max(
            0, int(self.tail_match_limit)
        )
        self.finding_limit = max(0, int(self.finding_limit))
        if self._limits_reached is None:
            self._limits_reached = set()

    def consume_values(self, count):
        count = max(0, int(count))
        if self.values_examined + count > self.value_limit:
            self._limits_reached.add("value")
            self.values_skipped += count
            return False
        self.values_examined += count
        return True

    def begin_pair(self, planned_value_count):
        planned_value_count = max(0, int(planned_value_count))
        blocked_by = None
        if self.pairs_examined >= self.pair_limit:
            blocked_by = "pair"
        elif (
            self.values_examined + planned_value_count
            > self.value_limit
        ):
            blocked_by = "value"
        if blocked_by is not None:
            self._limits_reached.add(blocked_by)
            self.pairs_skipped += 1
            self.values_skipped += planned_value_count
            return False
        self.pairs_examined += 1
        return True

    def record_values(self, count):
        count = max(0, int(count))
        if self.values_examined + count > self.value_limit:
            raise AssertionError(
                "cross-sheet value work exceeded its preflight"
            )
        self.values_examined += count

    def skip_values(self, count):
        self.values_skipped += max(0, int(count))

    def consume_pair(self, value_count):
        if not self.begin_pair(value_count):
            return False
        self.record_values(value_count)
        return True

    def skip_pairs(self, count, *, values=0):
        self.pairs_skipped += max(0, int(count))
        self.values_skipped += max(0, int(values))

    def retain_tail_match(self):
        if (
            self.tail_matches_retained
            >= self.tail_match_limit
        ):
            self._limits_reached.add("tail_match")
            self.tail_matches_skipped_lower_bound += 1
            return False
        self.tail_matches_retained += 1
        return True

    def retain_finding(self):
        if self.findings_retained >= self.finding_limit:
            self._limits_reached.add("finding")
            self.findings_skipped += 1
            return False
        self.findings_retained += 1
        return True

    def skip_bucket_findings(self, count):
        count = max(0, int(count))
        if not count:
            return
        self.bucket_findings_skipped += count
        self.findings_skipped += count
        self._limits_reached.add("fingerprint_bucket")

    def record_axis_coverage(
        self,
        *,
        available,
        loading_visits,
        grouping_visits,
        progression_visits,
        fingerprint_visits,
        recurrence_order_visits,
        recurrence_group_visits,
        recurrence_comparison_visits,
        recurrence_mark_visits,
        output_visits,
        work_skipped_lower_bound,
        work_skipped_is_lower_bound,
        state_unit_limit,
        peak_state_units,
    ):
        self.axis_context_available = (
            self.axis_context_available and bool(available)
        )
        self.axis_loading_visits += max(0, int(loading_visits))
        self.axis_grouping_visits += max(0, int(grouping_visits))
        self.axis_progression_visits += max(
            0, int(progression_visits)
        )
        self.axis_fingerprint_visits += max(
            0, int(fingerprint_visits)
        )
        self.axis_recurrence_order_visits += max(
            0, int(recurrence_order_visits)
        )
        self.axis_recurrence_group_visits += max(
            0, int(recurrence_group_visits)
        )
        self.axis_recurrence_comparison_visits += max(
            0, int(recurrence_comparison_visits)
        )
        self.axis_recurrence_mark_visits += max(
            0, int(recurrence_mark_visits)
        )
        self.axis_output_visits += max(0, int(output_visits))
        self.axis_work_skipped_lower_bound += max(
            0, int(work_skipped_lower_bound)
        )
        self.axis_work_skipped_is_lower_bound = (
            self.axis_work_skipped_is_lower_bound
            or bool(work_skipped_is_lower_bound)
        )
        self.axis_state_unit_limit = max(
            self.axis_state_unit_limit,
            max(0, int(state_unit_limit)),
        )
        self.axis_peak_state_units = max(
            self.axis_peak_state_units,
            max(0, int(peak_state_units)),
        )
        if self.axis_peak_state_units > self.axis_state_unit_limit:
            raise AssertionError("axis peak exceeded state limit")
        if not available:
            self._limits_reached.add("axis")

    def limitation_metadata(self):
        order = (
            "pair",
            "value",
            "axis",
            "tail_match",
            "finding",
            "fingerprint_bucket",
        )
        return {
            "pair_limit": self.pair_limit,
            "value_limit": self.value_limit,
            "tail_match_limit": self.tail_match_limit,
            "finding_limit": self.finding_limit,
            "pairs_examined": self.pairs_examined,
            "pairs_skipped": self.pairs_skipped,
            "values_examined": self.values_examined,
            "values_skipped": self.values_skipped,
            "tail_matches_retained": (
                self.tail_matches_retained
            ),
            "tail_matches_skipped_lower_bound": (
                self.tail_matches_skipped_lower_bound
            ),
            "findings_retained": self.findings_retained,
            "findings_skipped": self.findings_skipped,
            "bucket_findings_skipped": (
                self.bucket_findings_skipped
            ),
            "axis_context_available": (
                self.axis_context_available
            ),
            "axis_loading_visits": self.axis_loading_visits,
            "axis_grouping_visits": self.axis_grouping_visits,
            "axis_progression_visits": (
                self.axis_progression_visits
            ),
            "axis_fingerprint_visits": (
                self.axis_fingerprint_visits
            ),
            "axis_recurrence_order_visits": (
                self.axis_recurrence_order_visits
            ),
            "axis_recurrence_group_visits": (
                self.axis_recurrence_group_visits
            ),
            "axis_recurrence_comparison_visits": (
                self.axis_recurrence_comparison_visits
            ),
            "axis_recurrence_mark_visits": (
                self.axis_recurrence_mark_visits
            ),
            "axis_output_visits": self.axis_output_visits,
            "axis_work_skipped_lower_bound": (
                self.axis_work_skipped_lower_bound
            ),
            "axis_work_skipped_is_lower_bound": (
                self.axis_work_skipped_is_lower_bound
            ),
            "axis_state_unit_limit": self.axis_state_unit_limit,
            "axis_peak_state_units": self.axis_peak_state_units,
            "limits_reached": [
                name for name in order
                if name in self._limits_reached
            ],
            "omitted_findings_lower_bound": (
                self.findings_skipped
            ),
        }


_POSITION_VALUE_MIN_CELLS = 6
_DECIMAL_TAIL_MIN_CELLS = 8
_AXIS_PROGRESSION_MIN_CELLS = 4
_AXIS_FINGERPRINT_MIN_UNIQUE = 4
_AXIS_OUTPUT_COLUMN_UNITS = 20
_AXIS_OUTPUT_SUMMARY_UNITS = 32
_AXIS_STATE_UNITS_PER_CELL = 64
_AXIS_RECORD_DTYPE = np.dtype([
    ("column", np.int64),
    ("row", np.int64),
    ("value", np.float64),
])
_AXIS_COLUMN_DTYPE = np.dtype([
    ("summary_index", np.int64),
    ("column", np.int64),
    ("cell_count", np.int64),
    ("is_output", np.bool_),
    ("is_progression", np.bool_),
    ("fingerprint_offset", np.int64),
    ("fingerprint_nbytes", np.int64),
    ("fingerprint_hash", "S32"),
    ("is_recurring", np.bool_),
])


def _position_family_keys(grids):
    keys = tuple(
        key for key, grid in grids.items()
        if len(grid) >= _POSITION_VALUE_MIN_CELLS
    )
    return keys if len(keys) >= 2 else ()


@dataclass
class _CrossSheetCandidateLedger:
    pairs_total: int
    values_total: int
    pairs_resolved: int = 0
    values_resolved: int = 0

    @classmethod
    def from_sizes(cls, sizes):
        pairs_total = 0
        values_total = 0
        for minimum in (
            _POSITION_VALUE_MIN_CELLS,
            _DECIMAL_TAIL_MIN_CELLS,
        ):
            eligible_count = 0
            eligible_size_sum = 0
            for size in sizes:
                if size < minimum:
                    continue
                eligible_count += 1
                eligible_size_sum += size
            pairs_total += (
                eligible_count * (eligible_count - 1) // 2
            )
            if eligible_count > 1:
                values_total += (
                    (eligible_count - 1) * eligible_size_sum
                )
        return cls(
            pairs_total=pairs_total,
            values_total=values_total,
        )

    def resolve(self, value_count):
        self.pairs_resolved += 1
        self.values_resolved += max(0, int(value_count))

    def remaining(self):
        return (
            self.pairs_total - self.pairs_resolved,
            self.values_total - self.values_resolved,
        )


def _decimal_tail_signature(v, min_tail_digits=5, skip_decimal_digits=1):
    """Return a low-order decimal fingerprint for copy-then-edit detection.

    A common manual-edit fingerprint is that the leading integer/decimal digit is
    changed while the long fractional tail is left intact. For example,
    0.808902488 -> 0.908902488 preserves ``08902488`` after the first decimal
    digit. Short displayed decimals are ignored so ordinary one-decimal grids do
    not become tail matches.
    """
    try:
        fv = abs(float(v))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fv):
        return None
    s = f"{fv:.9f}".rstrip("0").rstrip(".")
    if "." not in s:
        return None
    frac = s.split(".", 1)[1]
    if len(frac) < skip_decimal_digits + min_tail_digits:
        return None
    tail = frac[skip_decimal_digits:]
    # Padded/quantized tails such as 00000 or 99999 have little forensic value.
    if len(set(tail)) <= 1:
        return None
    return tail


def _detect_decimal_tail_reuse_for_pair(
    ga,
    gb,
    *,
    min_tail_digits=5,
    skip_decimal_digits=1,
    min_matches=8,
    budget=None,
    with_coverage=False,
):
    """Find one aligned block where values differ but decimal tails are reused.

    This is layout-tolerant: if a table is pasted a few rows lower/upper, matching
    cells still share the same (row_delta, col_delta). Grouping by that offset
    distinguishes a copied block from isolated coincidental tail matches.
    """
    candidate_value_count = len(ga) + len(gb)
    value_visits = 0

    def finish_pair_result(result, **extra_coverage):
        if value_visits > candidate_value_count:
            raise AssertionError(
                "pair value work exceeded its candidate"
            )
        if budget is not None:
            budget.record_values(value_visits)
            budget.skip_values(
                candidate_value_count - value_visits
            )
        coverage = {
            "pair_admitted": True,
            "candidate_value_count": candidate_value_count,
            "value_visits": value_visits,
            **extra_coverage,
        }
        return (result, coverage) if with_coverage else result

    if budget is not None and not budget.begin_pair(
        candidate_value_count
    ):
        coverage = {
            "pair_admitted": False,
            "candidate_value_count": candidate_value_count,
            "value_visits": 0,
        }
        return (None, coverage) if with_coverage else None

    inv = {}
    for kb, vb in gb.items():
        value_visits += 1
        sig = _decimal_tail_signature(
            vb,
            min_tail_digits=min_tail_digits,
            skip_decimal_digits=skip_decimal_digits,
        )
        if sig:
            inv.setdefault(sig, []).append((kb, vb))

    by_offset = {}
    for ka, va in ga.items():
        value_visits += 1
        sig = _decimal_tail_signature(
            va,
            min_tail_digits=min_tail_digits,
            skip_decimal_digits=skip_decimal_digits,
        )
        if not sig:
            continue
        matches = inv.get(sig) or []
        # A very frequent tail is usually a quantization artifact; do not let it
        # create a combinatorial cloud of weak matches.
        if len(matches) > 20:
            continue
        for kb, vb in matches:
            if math.isclose(float(va), float(vb), rel_tol=1e-9, abs_tol=1e-12):
                continue
            if budget is not None and not budget.retain_tail_match():
                return finish_pair_result(None)
            off = (kb[0] - ka[0], kb[1] - ka[1])
            by_offset.setdefault(off, []).append((ka, kb, float(va), float(vb), sig))

    if not by_offset:
        return finish_pair_result(None)
    off, pairs = max(by_offset.items(), key=lambda kv: len(kv[1]))
    if len(pairs) < min_matches:
        return finish_pair_result(None)
    pairs = sorted(pairs, key=lambda p: (p[0][0], p[0][1], p[1][0], p[1][1]))
    result = {
        "offset": off,
        "pairs": pairs,
        "tail_match_count": len(pairs),
        "min_tail_digits": min_tail_digits,
        "skip_decimal_digits": skip_decimal_digits,
    }
    return finish_pair_result(result)


def _decimal_tail_constant_transform(pairs):
    """True if the matched value pairs share a constant additive offset (vb = va + k) or a constant
    ratio (vb = va * r). That is a benign linear/derived relationship between the two sheets (a shift,
    rescale, or baseline correction that incidentally preserves the fractional tail), not the
    irregular per-pair decimal-tail signal this detector targets."""
    vp = [(va, vb) for _ka, _kb, va, vb, _sig in pairs if va is not None and vb is not None]
    if len(vp) < 3:
        return False

    def _constant(vals):
        lo, hi = min(vals), max(vals)
        return (hi - lo) <= 1e-4 * max(abs(lo), abs(hi), 1e-9)

    if _constant([vb - va for va, vb in vp]):
        return True
    ratios = [vb / va for va, vb in vp if va not in (None, 0)]
    return len(ratios) >= 3 and _constant(ratios)


_DT_FIXED_DENOM_MAX_N = 400
_DT_FIXED_DENOM_TOL = 1e-6
_DT_FIXED_DENOM_FRAC = 0.85
_DT_AXIS_MIN_N = 6
_DT_FEWTAIL_MIN_PAIRS = 12
_DT_FEWTAIL_DOMINANCE = 0.80
_DT_PERCOL_MIN_GROUP = 3
_DT_LOGLABEL_RE = re.compile(
    r"\b("
    r"titer|titre|cfu|pfu|growth|log ?2|log ?10|log10|log2|"
    r"nt50|ic50|ec50|dilution|dilut|fold|"
    r"od600|od|absorbance|copy|copies|copy number|viral load|"
    r"qpcr|rt-qpcr|pcr|ct|cq|cycle threshold"
    r")\b",
    re.I,
)


def _dt_is_fractional(v):
    return v is not None and abs(float(v) - round(float(v))) > 1e-6


def _dt_fixed_denominator(pairs):
    """Return a benign reason when most decimal-tail values are k/N rates."""
    vals = [
        float(v)
        for _ka, _kb, va, vb, _s in pairs
        for v in (va, vb)
        if v is not None and _dt_is_fractional(v)
    ]
    if len(vals) < 6:
        return None
    need = max(6, math.ceil(_DT_FIXED_DENOM_FRAC * len(vals)))
    for n in range(2, _DT_FIXED_DENOM_MAX_N + 1):
        hit = sum(
            1
            for v in vals
            if abs(v * n - round(v * n)) < _DT_FIXED_DENOM_TOL * max(1, abs(v) * n)
        )
        if hit >= need:
            return f"fixed_denominator:1/{n}"
    return None


def _dt_progression(seq):
    """Conservative arithmetic/geometric progression test for axis-like values."""
    vals = [float(v) for v in seq if v is not None]
    if len(vals) < _DT_AXIS_MIN_N:
        return False
    if len({round(v, 9) for v in vals}) < _DT_AXIS_MIN_N:
        return False
    diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    increasing = all(d > 1e-12 for d in diffs)
    decreasing = all(d < -1e-12 for d in diffs)
    if not (increasing or decreasing):
        return False

    base = min(abs(d) for d in diffs)
    if base and all(
        abs(d / base - round(d / base)) < 1e-4 * max(1, abs(d / base))
        for d in diffs
    ):
        return True

    if all(v != 0 for v in vals) and (all(v > 0 for v in vals) or all(v < 0 for v in vals)):
        ratios = [vals[i + 1] / vals[i] for i in range(len(vals) - 1)]
        return (max(ratios) - min(ratios)) < 1e-3 * max(abs(max(ratios)), abs(min(ratios)), 1e-9)
    return False


def _dt_axis(pairs):
    a = [va for _ka, _kb, va, _vb, _s in sorted(pairs, key=lambda p: (p[0][0], p[0][1]))]
    b = [vb for _ka, _kb, _va, vb, _s in sorted(pairs, key=lambda p: (p[1][0], p[1][1]))]
    return _dt_progression(a) and _dt_progression(b)


def _dt_few_tails(pairs):
    if len(pairs) < _DT_FEWTAIL_MIN_PAIRS:
        return False
    tails = [str(s) for _ka, _kb, _va, _vb, s in pairs if s is not None]
    if not tails:
        return False
    top = max(Counter(tails).values())
    return top >= _DT_FEWTAIL_DOMINANCE * len(tails)


def _dt_per_column_constant(pairs):
    """Return per-column constant offset/ratio reason, or None."""
    groups = {}
    for ka, _kb, va, vb, _s in pairs:
        if va is None or vb is None:
            continue
        groups.setdefault(ka[1], []).append((float(va), float(vb)))
    groups = {c: g for c, g in groups.items() if len(g) >= _DT_PERCOL_MIN_GROUP}
    if len(groups) < 2:
        return None

    def _const(xs):
        lo, hi = min(xs), max(xs)
        return (hi - lo) <= 1e-4 * max(abs(lo), abs(hi), 1e-9)

    offsets = []
    for c, g in sorted(groups.items()):
        diffs = [vb - va for va, vb in g]
        ratios = [vb / va for va, vb in g if va]
        if _const(diffs):
            offsets.append("c%d:%+.4g" % (c, sum(diffs) / len(diffs)))
        elif len(ratios) == len(g) and _const(ratios):
            offsets.append("c%d:x%.4g" % (c, sum(ratios) / len(ratios)))
        else:
            return None
    return "per_column_constant:[%s]" % ",".join(offsets)


def _dt_label_values(v):
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v if x is not None]
    return [str(v)]


def _dt_label_blob(labels):
    parts = []
    for lc in labels or ():
        if not isinstance(lc, dict):
            continue
        for key in ("column_labels", "row_labels", "nearby_labels", "text"):
            parts.extend(_dt_label_values(lc.get(key)))
    return " ".join(parts)


def _dt_log_dilution_candidate(pairs, labels):
    """Return a note-only reason for likely log/dilution integer shifts."""
    diffs = [
        float(vb) - float(va)
        for _ka, _kb, va, vb, _s in pairs
        if va is not None and vb is not None
    ]
    if len(diffs) < 6:
        return None
    near_int = sum(1 for d in diffs if abs(d - round(d)) < 1e-6)
    if near_int < 0.8 * len(diffs):
        return None
    return (
        "log_or_dilution_integer_shift_candidate"
        if _DT_LOGLABEL_RE.search(_dt_label_blob(labels))
        else None
    )


def _decimal_tail_low_reason(pairs):
    if _decimal_tail_constant_transform(pairs):
        return "constant_transform"
    return _dt_fixed_denominator(pairs) or _dt_per_column_constant(pairs)


def _decimal_tail_note_reason(pairs, labels=None):
    if _dt_axis(pairs):
        return "axis_progression"
    if _dt_few_tails(pairs):
        return "constant_fraction_tail"
    return _dt_log_dilution_candidate(pairs, labels)


def _column_cells(grid, c):
    """Row-ordered [(row, value)] for column ``c`` of a decimal grid."""
    return sorted(((r, v) for (r, cc), v in grid.items() if cc == c), key=lambda t: t[0])


def _is_axis_progression(grid, c, min_n=4, rel_tol=1e-4, geo_tol=1e-3):
    """True when column ``c`` is a swept axis: its values lie on an arithmetic
    (constant step) or geometric (constant ratio) progression in row order.

    Catches dose ladders / serial dilutions (1:3 → geometric), time / frequency /
    voltage sweeps (linear → arithmetic) and integer-step index axes. Gaps from
    dropped integer rows are tolerated by fitting against the row index. ``geo_tol``
    is looser than ``rel_tol`` so a serial dilution stored at 3 significant figures
    (33.3 / 11.1 / 3.70 …) still reads as geometric.

    Blind spot worth noting: a *measurement* column that happens to be an exact
    arithmetic/geometric ramp is indistinguishable from an axis here. That is rare in
    real data, and paperconan's within-column arithmetic/geometric detectors flag such
    a column HIGH independently — so a copied exact-progression column is not silenced
    overall, only this one cross-sheet finding would be downgraded.
    """
    cells = _column_cells(grid, c)
    if len(cells) < min_n:
        return False
    rs = [r for r, _ in cells]
    vs = [v for _, v in cells]
    span = rs[-1] - rs[0]
    if span <= 0:
        return False
    # arithmetic: v linear in row index, non-flat
    step = (vs[-1] - vs[0]) / span
    if abs(step) > 1e-12:
        scale = max(abs(v) for v in vs) or 1.0
        if all(abs(v - (vs[0] + step * (r - rs[0]))) <= rel_tol * scale for r, v in cells):
            return True
    # geometric: same-sign nonzero values that are linear in log space
    if all(v != 0 for v in vs) and (all(v > 0 for v in vs) or all(v < 0 for v in vs)):
        logs = [math.log(abs(v)) for v in vs]
        lstep = (logs[-1] - logs[0]) / span
        if abs(lstep) > 1e-9:
            if all(abs(lg - (logs[0] + lstep * (r - rs[0]))) <= geo_tol for (r, _), lg in zip(cells, logs)):
                return True
    return False


def _is_axis_progression_cells(
    cells, min_n=4, rel_tol=1e-4, geo_tol=1e-3
):
    if len(cells) < min_n:
        return False
    first_row, first_value = cells[0]
    last_row, last_value = cells[-1]
    span = last_row - first_row
    if span <= 0:
        return False

    scale = 0.0
    geometric_possible = True
    first_positive = first_value > 0
    for _row, value in cells:
        scale = max(scale, abs(value))
        if (
            value == 0
            or (value > 0) != first_positive
        ):
            geometric_possible = False
    scale = scale or 1.0

    step = (last_value - first_value) / span
    arithmetic = abs(step) > 1e-12
    log_first = None
    log_step = None
    geometric = False
    if geometric_possible:
        log_first = math.log(abs(first_value))
        log_step = (
            math.log(abs(last_value)) - log_first
        ) / span
        geometric = abs(log_step) > 1e-9
    for row, value in cells:
        if arithmetic and (
            abs(
                value
                - (
                    first_value
                    + step * (row - first_row)
                )
            )
            > rel_tol * scale
        ):
            arithmetic = False
        if geometric and (
            abs(
                math.log(abs(value))
                - (
                    log_first
                    + log_step * (row - first_row)
                )
            )
            > geo_tol
        ):
            geometric = False
    return arithmetic or geometric


def _is_axis_progression_arrays(
    rows,
    values,
    *,
    min_n=4,
    rel_tol=1e-4,
    geo_tol=1e-3,
    with_coverage=False,
):
    value_visits = 0

    def finish(result):
        coverage = {"value_visits": value_visits}
        return (result, coverage) if with_coverage else result

    if len(values) < min_n:
        return finish(False)
    first_row = int(rows[0])
    last_row = int(rows[-1])
    first_value = float(values[0])
    last_value = float(values[-1])
    span = last_row - first_row
    if span <= 0:
        return finish(False)

    scale = 0.0
    max_arithmetic_error = 0.0
    max_geometric_error = 0.0
    first_positive = first_value > 0
    step = (last_value - first_value) / span
    geometric = (
        first_value != 0
        and last_value != 0
        and (last_value > 0) == first_positive
    )
    if geometric:
        log_first = math.log(abs(first_value))
        log_step = (
            math.log(abs(last_value)) - log_first
        ) / span
    else:
        log_first = 0.0
        log_step = 0.0

    for index in range(len(values)):
        value_visits += 1
        row = int(rows[index])
        value = float(values[index])
        scale = max(scale, abs(value))
        max_arithmetic_error = max(
            max_arithmetic_error,
            abs(
                value - (
                    first_value + step * (row - first_row)
                )
            )
        )
        if geometric:
            if value == 0 or (value > 0) != first_positive:
                geometric = False
            else:
                max_geometric_error = max(
                    max_geometric_error,
                    abs(
                        math.log(abs(value))
                        - (
                            log_first
                            + log_step * (row - first_row)
                        )
                    ),
                )
    arithmetic = (
        abs(step) > 1e-12
        and max_arithmetic_error <= rel_tol * (scale or 1.0)
    )
    geometric = (
        geometric
        and abs(log_step) > 1e-9
        and max_geometric_error <= geo_tol
    )
    return finish(arithmetic or geometric)


def _axis_payload_equal(
    payload_view,
    table,
    left_index,
    right_index,
):
    fingerprint_nbytes = int(
        table["fingerprint_nbytes"][left_index]
    )
    if (
        int(table["fingerprint_nbytes"][right_index])
        != fingerprint_nbytes
    ):
        return False
    left_offset = int(
        table["fingerprint_offset"][left_index]
    )
    right_offset = int(
        table["fingerprint_offset"][right_index]
    )
    return (
        payload_view[
            left_offset:left_offset + fingerprint_nbytes
        ]
        == payload_view[
            right_offset:right_offset + fingerprint_nbytes
        ]
    )


def _axis_columns(
    grids,
    recur_min=3,
    *,
    position_keys=None,
    budget=None,
    with_coverage=False,
    _state_limit=None,
):
    """Classify, per (file, sheet), which columns are 'axis-like' so a cross-sheet
    overlap that lands only on them can be recognized as a shared-x-axis artifact.

    A column is axis-like if either:
      (A) its values form an arithmetic/geometric progression (a swept axis), or
      (B) its exact value-set recurs as a column across >= ``recur_min`` distinct
          (file, sheet) grids — i.e. the same axis was reused across many panels.
    """
    if position_keys is None:
        position_keys = _position_family_keys(grids)
    else:
        position_keys = tuple(position_keys)
        if len(position_keys) < 2:
            position_keys = ()
    position_key_set = frozenset(position_keys)
    support_keys = (
        tuple(
            key for key, grid in grids.items()
            if (
                key in position_key_set
                or len(grid) >= _AXIS_FINGERPRINT_MIN_UNIQUE
            )
        )
        if len(position_keys) >= 2
        else ()
    )
    position_cell_count = sum(
        len(grids[key]) for key in position_keys
    )
    support_cell_count = sum(
        len(grids[key]) for key in support_keys
    )
    remaining_fixed_visits = 3 * support_cell_count
    axis_work_skipped_lower_bound = 0
    dynamic_finalization_complete = not bool(support_keys)
    stage_visits = {
        "loading": 0,
        "grouping": 0,
        "progression": 0,
        "fingerprint": 0,
        "recurrence_order": 0,
        "recurrence_group": 0,
        "recurrence_comparison": 0,
        "recurrence_mark": 0,
        "output": 0,
    }
    default_axis_state_limit = (
        _AXIS_STATE_UNITS_PER_CELL
        * support_cell_count
    )
    axis_state_limit = (
        default_axis_state_limit
        if _state_limit is None
        else max(0, int(_state_limit))
    )
    state = StateBudget(axis_state_limit)
    live_leases = []

    def add_fixed_work(count):
        nonlocal remaining_fixed_visits
        remaining_fixed_visits += max(0, int(count))

    def record_skipped_work(count, *, budget_recorded=False):
        nonlocal axis_work_skipped_lower_bound
        count = max(0, int(count))
        axis_work_skipped_lower_bound += count
        if budget is not None and not budget_recorded:
            budget.skip_values(count)

    def admit_stage(stage, count):
        nonlocal remaining_fixed_visits
        count = max(0, int(count))
        if count > remaining_fixed_visits:
            raise AssertionError("axis work ledger underflow")
        remaining_fixed_visits -= count
        if budget is not None and not budget.consume_values(count):
            record_skipped_work(count, budget_recorded=True)
            return False
        stage_visits[stage] += count
        return True

    def admit_dynamic_stage(stage, count):
        count = max(0, int(count))
        if budget is not None and not budget.consume_values(count):
            record_skipped_work(count, budget_recorded=True)
            return False
        stage_visits[stage] += count
        return True

    def skip_remaining_axis_work():
        nonlocal remaining_fixed_visits
        record_skipped_work(remaining_fixed_visits)
        remaining_fixed_visits = 0

    def release_all_axis_leases():
        for lease in reversed(live_leases):
            if not lease.released:
                lease.release()
        live_leases.clear()

    def reserve_axis_state(name, units):
        lease = state.try_reserve(name, units)
        if lease is not None:
            live_leases.append(lease)
        return lease

    def release_axis_state(lease):
        lease.release()
        live_leases.remove(lease)

    def finish_result(result, *, available):
        if not available:
            skip_remaining_axis_work()
        coverage = {
            "participating_summaries": len(position_keys),
            "participating_cells": position_cell_count,
            "recurrence_support_summaries": len(support_keys),
            "recurrence_support_cells": support_cell_count,
            "axis_loading_visits": stage_visits["loading"],
            "axis_grouping_visits": stage_visits["grouping"],
            "axis_progression_visits": (
                stage_visits["progression"]
            ),
            "axis_fingerprint_visits": (
                stage_visits["fingerprint"]
            ),
            "axis_recurrence_order_visits": (
                stage_visits["recurrence_order"]
            ),
            "axis_recurrence_group_visits": (
                stage_visits["recurrence_group"]
            ),
            "axis_recurrence_comparison_visits": (
                stage_visits["recurrence_comparison"]
            ),
            "axis_recurrence_mark_visits": (
                stage_visits["recurrence_mark"]
            ),
            "axis_output_visits": stage_visits["output"],
            "axis_value_visits": sum(stage_visits.values()),
            "axis_work_skipped_lower_bound": (
                axis_work_skipped_lower_bound
            ),
            "axis_work_skipped_is_lower_bound": (
                not available
                and not dynamic_finalization_complete
            ),
            "axis_context_available": bool(available),
            "axis_state_unit_limit": axis_state_limit,
            "axis_peak_state_units": state.peak_units,
        }
        if budget is not None:
            budget.record_axis_coverage(
                available=available,
                loading_visits=stage_visits["loading"],
                grouping_visits=stage_visits["grouping"],
                progression_visits=stage_visits[
                    "progression"
                ],
                fingerprint_visits=stage_visits[
                    "fingerprint"
                ],
                recurrence_order_visits=stage_visits[
                    "recurrence_order"
                ],
                recurrence_group_visits=stage_visits[
                    "recurrence_group"
                ],
                recurrence_comparison_visits=stage_visits[
                    "recurrence_comparison"
                ],
                recurrence_mark_visits=stage_visits[
                    "recurrence_mark"
                ],
                output_visits=stage_visits["output"],
                work_skipped_lower_bound=(
                    axis_work_skipped_lower_bound
                ),
                work_skipped_is_lower_bound=(
                    not available
                    and not dynamic_finalization_complete
                ),
                state_unit_limit=axis_state_limit,
                peak_state_units=state.peak_units,
            )
        return (result, coverage) if with_coverage else result

    def unavailable_result():
        return finish_result({}, available=False)

    def store_axis_fingerprint(
        column_entry,
        values,
        *,
        unique_lease,
        canonical_lease,
        temp_lease,
    ):
        nonlocal fingerprint_bytes_used
        unique = np.unique(values)
        unique_lease.validate_nbytes(unique.nbytes)
        if len(unique) < _AXIS_FINGERPRINT_MIN_UNIQUE:
            return
        canonical = unique.astype("<f8", copy=False)
        canonical_lease.validate_nbytes(canonical.nbytes)
        fingerprint = canonical.tobytes()
        temp_lease.validate_nbytes(len(fingerprint))
        stop = fingerprint_bytes_used + len(fingerprint)
        if stop > len(fingerprint_payload):
            raise AssertionError(
                "axis fingerprint payload overflow"
            )
        fingerprint_payload[fingerprint_bytes_used:stop] = (
            fingerprint
        )
        column_table["fingerprint_offset"][
            column_entry
        ] = fingerprint_bytes_used
        column_table["fingerprint_nbytes"][
            column_entry
        ] = len(fingerprint)
        column_table["fingerprint_hash"][
            column_entry
        ] = hashlib.sha256(fingerprint).digest()
        fingerprint_bytes_used = stop

    try:
        column_table_units = state_units_for_nbytes(
            support_cell_count * _AXIS_COLUMN_DTYPE.itemsize
        )
        column_table_lease = reserve_axis_state(
            "axis_column_table",
            column_table_units,
        )
        fingerprint_payload_lease = reserve_axis_state(
            "axis_fingerprint_payloads",
            support_cell_count,
        )
        output_capacity_lease = reserve_axis_state(
            "axis_output_capacity",
            (
                _AXIS_OUTPUT_COLUMN_UNITS * support_cell_count
                + _AXIS_OUTPUT_SUMMARY_UNITS
                * len(position_keys)
            ),
        )
        if any(
            lease is None
            for lease in (
                column_table_lease,
                fingerprint_payload_lease,
                output_capacity_lease,
            )
        ):
            return unavailable_result()

        column_table = np.zeros(
            support_cell_count,
            dtype=_AXIS_COLUMN_DTYPE,
        )
        column_table_lease.validate_nbytes(
            column_table.nbytes
        )
        fingerprint_payload = bytearray(
            support_cell_count * np.dtype("<f8").itemsize
        )
        fingerprint_payload_lease.validate_nbytes(
            len(fingerprint_payload)
        )
        column_count = 0
        fingerprint_bytes_used = 0

        for summary_index, key in enumerate(support_keys):
            grid = grids[key]
            summary_cells = len(grid)
            record_units = state_units_for_nbytes(
                summary_cells * _AXIS_RECORD_DTYPE.itemsize
            )
            record_lease = reserve_axis_state(
                "axis_records",
                record_units,
            )
            if record_lease is None:
                return unavailable_result()
            if not admit_stage("loading", summary_cells):
                return unavailable_result()

            records = np.empty(
                summary_cells,
                dtype=_AXIS_RECORD_DTYPE,
            )
            record_lease.validate_nbytes(records.nbytes)
            for index, ((row, column), value) in enumerate(
                grid.items()
            ):
                canonical_value = 0.0 if value == 0.0 else value
                records[index] = (
                    column,
                    row,
                    canonical_value,
                )

            order_lease = reserve_axis_state(
                "axis_order",
                summary_cells,
            )
            workspace_lease = reserve_axis_state(
                "axis_sort_workspace",
                4 * summary_cells,
            )
            ordered_lease = reserve_axis_state(
                "axis_ordered_records",
                record_units,
            )
            leases = [
                order_lease,
                workspace_lease,
                ordered_lease,
            ]
            if any(lease is None for lease in leases):
                return unavailable_result()
            if not admit_stage("grouping", summary_cells):
                return unavailable_result()

            order = np.lexsort((
                records["row"],
                records["column"],
            ))
            order_lease.validate_nbytes(order.nbytes)
            ordered = records[order]
            ordered_lease.validate_nbytes(ordered.nbytes)
            release_axis_state(record_lease)
            release_axis_state(order_lease)
            release_axis_state(workspace_lease)

            column_entry_start = column_count
            summary_progression_cells = 0
            start = 0
            while start < len(ordered):
                column = int(ordered["column"][start])
                stop = start + 1
                while (
                    stop < len(ordered)
                    and int(ordered["column"][stop]) == column
                ):
                    stop += 1
                if column_count >= len(column_table):
                    raise AssertionError(
                        "axis column table overflow"
                    )
                cell_count = stop - start
                column_table["summary_index"][
                    column_count
                ] = summary_index
                column_table["column"][column_count] = column
                column_table["cell_count"][
                    column_count
                ] = cell_count
                column_table["is_output"][
                    column_count
                ] = key in position_key_set
                if (
                    key in position_key_set
                    and cell_count
                    >= _AXIS_PROGRESSION_MIN_CELLS
                ):
                    summary_progression_cells += cell_count
                column_count += 1
                start = stop
            column_entry_stop = column_count
            summary_column_count = (
                column_entry_stop - column_entry_start
            )
            add_fixed_work(3 * summary_column_count)

            if key in position_key_set:
                add_fixed_work(summary_progression_cells)
                if not admit_stage(
                    "progression",
                    summary_progression_cells,
                ):
                    return unavailable_result()
                observed_progression_visits = 0
                column_entry = column_entry_start
                start = 0
                while start < len(ordered):
                    column = int(ordered["column"][start])
                    stop = start + 1
                    while (
                        stop < len(ordered)
                        and int(
                            ordered["column"][stop]
                        ) == column
                    ):
                        stop += 1
                    rows = ordered["row"][start:stop]
                    values = ordered["value"][start:stop]
                    if (
                        len(values)
                        >= _AXIS_PROGRESSION_MIN_CELLS
                    ):
                        (
                            is_progression,
                            progression_coverage,
                        ) = _is_axis_progression_arrays(
                            rows,
                            values,
                            with_coverage=True,
                        )
                        observed_progression_visits += (
                            progression_coverage[
                                "value_visits"
                            ]
                        )
                        column_table["is_progression"][
                            column_entry
                        ] = is_progression
                    column_entry += 1
                    start = stop
                if column_entry != column_entry_stop:
                    raise AssertionError(
                        "axis progression columns diverged"
                    )
                if (
                    observed_progression_visits
                    != summary_progression_cells
                ):
                    raise AssertionError(
                        "axis progression work diverged"
                    )

            unique_lease = reserve_axis_state(
                "axis_unique_values",
                summary_cells,
            )
            unique_workspace = reserve_axis_state(
                "axis_unique_workspace",
                5 * summary_cells,
            )
            canonical_lease = reserve_axis_state(
                "axis_canonical_values",
                summary_cells,
            )
            temp_lease = reserve_axis_state(
                "axis_fingerprint_temp",
                summary_cells,
            )
            fingerprint_stage_leases = (
                unique_lease,
                unique_workspace,
                canonical_lease,
                temp_lease,
            )
            if any(
                lease is None
                for lease in fingerprint_stage_leases
            ):
                return unavailable_result()
            if not admit_stage("fingerprint", summary_cells):
                return unavailable_result()

            column_entry = column_entry_start
            start = 0
            while start < len(ordered):
                column = int(ordered["column"][start])
                stop = start + 1
                while (
                    stop < len(ordered)
                    and int(ordered["column"][stop]) == column
                ):
                    stop += 1
                values = ordered["value"][start:stop]
                store_axis_fingerprint(
                    column_entry,
                    values,
                    unique_lease=unique_lease,
                    canonical_lease=canonical_lease,
                    temp_lease=temp_lease,
                )
                column_entry += 1
                start = stop

            if column_entry != column_entry_stop:
                raise AssertionError(
                    "axis fingerprint columns diverged"
                )
            for lease in reversed(fingerprint_stage_leases):
                release_axis_state(lease)
            release_axis_state(ordered_lease)

        fingerprint_order_lease = reserve_axis_state(
            "axis_fingerprint_order",
            column_count,
        )
        fingerprint_order_workspace = reserve_axis_state(
            "axis_fingerprint_order_workspace",
            4 * column_count,
        )
        if any(
            lease is None
            for lease in (
                fingerprint_order_lease,
                fingerprint_order_workspace,
            )
        ):
            return unavailable_result()

        table = column_table[:column_count]
        if not admit_stage("recurrence_order", column_count):
            return unavailable_result()
        fingerprint_order = np.lexsort((
            table["fingerprint_hash"],
            table["fingerprint_nbytes"],
        ))
        fingerprint_order_lease.validate_nbytes(
            fingerprint_order.nbytes
        )
        release_axis_state(fingerprint_order_workspace)
        payload_view = memoryview(fingerprint_payload)

        if not admit_stage("recurrence_group", column_count):
            return unavailable_result()
        position = 0
        while position < column_count:
            first_index = int(fingerprint_order[position])
            fingerprint_nbytes = int(
                table["fingerprint_nbytes"][first_index]
            )
            fingerprint_hash = table["fingerprint_hash"][
                first_index
            ]
            stop = position + 1
            while (
                stop < column_count
                and int(table["fingerprint_nbytes"][
                    int(fingerprint_order[stop])
                ]) == fingerprint_nbytes
                and table["fingerprint_hash"][
                    int(fingerprint_order[stop])
                ] == fingerprint_hash
            ):
                stop += 1
            if fingerprint_nbytes == 0:
                position = stop
                continue

            class_start = position
            while class_start < stop:
                base_index = int(
                    fingerprint_order[class_start]
                )
                match_stop = class_start
                for candidate_position in range(
                    class_start,
                    stop,
                ):
                    candidate_index = int(
                        fingerprint_order[candidate_position]
                    )
                    if not admit_dynamic_stage("recurrence_comparison", 1):
                        return unavailable_result()
                    if _axis_payload_equal(
                        payload_view,
                        table,
                        base_index,
                        candidate_index,
                    ):
                        displaced_index = int(
                            fingerprint_order[match_stop]
                        )
                        fingerprint_order[
                            match_stop
                        ] = candidate_index
                        fingerprint_order[
                            candidate_position
                        ] = displaced_index
                        match_stop += 1
                match_count = match_stop - class_start
                if match_count <= 0:
                    raise AssertionError(
                        "axis fingerprint class made no progress"
                    )
                if match_count >= recur_min:
                    if not admit_dynamic_stage("recurrence_mark", match_count):
                        return unavailable_result()
                    for match_position in range(
                        class_start,
                        match_stop,
                    ):
                        candidate_index = int(
                            fingerprint_order[
                                match_position
                            ]
                        )
                        table["is_recurring"][
                            candidate_index
                        ] = True
                class_start = match_stop
            position = stop

        dynamic_finalization_complete = True
        if not admit_stage("output", column_count):
            return unavailable_result()
        axis = {key: set() for key in position_keys}
        for index in range(column_count):
            if (
                bool(table["is_output"][index])
                and (
                    bool(table["is_progression"][index])
                    or bool(table["is_recurring"][index])
                )
            ):
                summary_index = int(
                    table["summary_index"][index]
                )
                axis[support_keys[summary_index]].add(
                    int(table["column"][index])
                )

        release_axis_state(fingerprint_order_lease)
        release_axis_state(output_capacity_lease)
        release_axis_state(fingerprint_payload_lease)
        release_axis_state(column_table_lease)

        if remaining_fixed_visits != 0:
            raise AssertionError(
                "axis work ledger did not close"
            )
        if not dynamic_finalization_complete:
            raise AssertionError(
                "axis finalization did not close"
            )
        return finish_result(axis, available=True)
    finally:
        release_all_axis_leases()


def _text_cell(sheet, r, c):
    if sheet is None or r < 0 or c < 0 or r >= sheet.nrows or c >= sheet.ncols:
        return ""
    v = sheet.cell(r, c)
    if isinstance(v, str):
        return v.strip()
    return ""


def _label_context_for_matches(sheet, shared, max_labels=40):
    if sheet is None:
        return {"column_labels": [], "row_labels": [], "nearby_labels": [], "text": ""}
    col_labels, row_labels, nearby = [], [], []
    for (r, c), _v in shared[:max_labels]:
        for rr in range(max(0, r - 3), r):
            label = _text_cell(sheet, rr, c)
            if label:
                col_labels.append(label)
        for cc in range(max(0, c - 3), c):
            label = _text_cell(sheet, r, cc)
            if label:
                row_labels.append(label)
        for rr in range(max(0, r - 2), min(sheet.nrows, r + 3)):
            for cc in range(max(0, c - 2), min(sheet.ncols, c + 3)):
                label = _text_cell(sheet, rr, cc)
                if label:
                    nearby.append(label)

    def uniq(vals):
        out, seen = [], set()
        for val in vals:
            key = val.lower()
            if key not in seen:
                seen.add(key)
                out.append(val)
        return out[:max_labels]

    ctx = {
        "column_labels": uniq(col_labels),
        "row_labels": uniq(row_labels),
        "nearby_labels": uniq(nearby),
    }
    ctx["text"] = " ".join(ctx["column_labels"] + ctx["row_labels"] + ctx["nearby_labels"])
    return ctx


def _shared_cross_sheet_context(ctx_a, ctx_b, pattern, fraction):
    text_a = (ctx_a or {}).get("text", "")
    text_b = (ctx_b or {}).get("text", "")
    both_control = bool(
        _CONTROL_BASELINE_LABEL_RE.search(text_a)
        and _CONTROL_BASELINE_LABEL_RE.search(text_b)
    )
    either_axis = bool(_AXIS_CONTEXT_LABEL_RE.search(text_a) or _AXIS_CONTEXT_LABEL_RE.search(text_b))
    non_perfect = pattern != "perfect_dup" and (fraction is None or fraction < 0.9)
    reason = None
    if non_perfect and both_control:
        reason = "matched cells are labelled as shared control/baseline/reference context"
    elif non_perfect and either_axis:
        reason = "matched cells are labelled as shared axis/coordinate context"
    return {
        "shared_control_or_baseline": bool(non_perfect and both_control),
        "shared_axis_or_coordinate": bool(non_perfect and either_axis),
        "context_reason": reason,
    }


def detect_collisions(
    grids, profile="review", sheets=None, budget=None
):
    """Find pairs of tables (sheets and/or flat files) with many bit-identical decimal
    values at the SAME (row, col), including repeated tables with changed cells in another
    sheet of the same workbook or in a separate file.

    `grids` maps (file, sheet) -> grid (from _grid_from_rows). Returns one dict per
    candidate pair, with file_a/file_b set so same-file and cross-file pairs are
    distinguishable.

    Severity is context-aware on two axes:

    - SAME figure id (e.g. exFig.6i ↔ exFig.6k-n): the expected combined-vs-individual
      re-plot, downgraded to "low" with an explanatory `context`.
    - SHARED AXIS: when the bit-identical (row,col) cells concentrate (>=80%) on a
      column that is a swept axis / serial-dilution ladder / index reused across panels,
      AND the rest of the table diverges (pattern != perfect_dup), the overlap is just a
      shared x-axis (dose / time / frequency) — downgraded to "low" with `axis_overlap`.
      A full-table duplicate (perfect_dup) is NOT downgraded by this rule.

    Cross-figure overlaps that survive both checks keep their base severity.
    """
    findings = []
    position_keys = _position_family_keys(grids)
    sizes = tuple(len(grids[key]) for key in position_keys)
    candidate_ledger = _CrossSheetCandidateLedger.from_sizes(
        sizes
    )

    def resolve_pair_family(coverage):
        candidate_ledger.resolve(
            coverage["candidate_value_count"]
        )
        if coverage["pair_admitted"]:
            return True
        if budget is None:
            raise AssertionError(
                "unbudgeted pair helper rejected candidate"
            )
        pairs_remaining, values_remaining = (
            candidate_ledger.remaining()
        )
        budget.skip_pairs(
            pairs_remaining,
            values=values_remaining,
        )
        return False

    axis_cols = _axis_columns(
        grids,
        position_keys=position_keys,
        budget=budget,
    )

    def emit(finding):
        if budget is None or budget.retain_finding():
            findings.append(finding)

    for key_a, key_b in combinations(position_keys, 2):
        (fa, sa), (fb, sb) = key_a, key_b
        ga, gb = grids[key_a], grids[key_b]
        size_a, size_b = len(ga), len(gb)
        smaller = min(size_a, size_b)
        pair_stats, pair_coverage = _cross_sheet_pair_stats(
            ga,
            gb,
            budget=budget,
            with_coverage=True,
        )
        if not resolve_pair_family(pair_coverage):
            break

        same_file = fa == fb
        # label_a / label_b disambiguate sheets when the pair spans two files
        la = sa if same_file else f"{fa}::{sa}"
        lb = sb if same_file else f"{fb}::{sb}"
        scope = "sheets" if same_file else "files"

        fig_a, fig_b = figure_key(sa), figure_key(sb)
        same_figure = bool(
            same_file and fig_a and fig_b and fig_a == fig_b
        )
        context = None
        if same_figure:
            context = (
                f"both sheets belong to the same display item ({fig_a}); "
                "a combined panel and its per-replicate breakdown share data "
                "by design, so this overlap is expected, not a "
                "cross-experiment reuse"
            )

        same_pos = pair_stats.same_position_count
        same_val = pair_stats.shared_value_count
        ctx_fields = dict(
            figure_a=fig_a,
            figure_b=fig_b,
            same_figure=same_figure,
            delta=pair_stats.delta,
        )
        if context:
            ctx_fields["context"] = context

        if same_pos >= max(6, smaller * 0.15):
            shared = pair_stats.shared_cells
            examples = shared[:5]
            label_context_a = _label_context_for_matches(
                (sheets or {}).get(key_a), shared
            )
            label_context_b = _label_context_for_matches(
                (sheets or {}).get(key_b), shared
            )
            fraction_of_smaller = same_pos / smaller
            shared_context = _shared_cross_sheet_context(
                label_context_a,
                label_context_b,
                ctx_fields["delta"]["pattern"],
                fraction_of_smaller,
            )
            pair_axis = (
                axis_cols.get(key_a, set())
                | axis_cols.get(key_b, set())
            )
            on_axis = sum(
                count
                for col, count
                in pair_stats.same_position_columns.items()
                if col in pair_axis
            )
            non_axis_shared = same_pos - on_axis
            axis_overlap = (
                not same_figure
                and ctx_fields["delta"]["pattern"] != "perfect_dup"
                and on_axis >= 0.8 * same_pos
                and non_axis_shared <= 3
            )
            if axis_overlap:
                ctx_fields["axis_overlap"] = True
                axis_note = (
                    "the bit-identical cells fall on a shared x-axis column "
                    "(serial-dilution dose, time/frequency sweep, or an index "
                    "reused across panels), while the measured values differ — "
                    "a shared axis, not cross-experiment data reuse"
                )
                ctx_fields["context"] = axis_note
                ctx_fields["likely_benign"] = axis_note
            sev = "low" if same_figure or axis_overlap else "high"
            emit(dict(
                kind="cross_sheet_position_identical",
                file=fa if same_file else f"{fa} + {fb}",
                file_a=fa,
                file_b=fb,
                same_file=same_file,
                sheet_a=la,
                sheet_b=lb,
                size_a=size_a,
                size_b=size_b,
                same_position_count=same_pos,
                fraction_of_smaller=fraction_of_smaller,
                label_context_a=label_context_a,
                label_context_b=label_context_b,
                shared_context=shared_context,
                examples=[
                    dict(
                        row=key[0] + 1,
                        col=key[1] + 1,
                        value=value,
                    )
                    for key, value in examples
                ],
                severity=sev,
                **ctx_fields,
                rule=(
                    f"{la} and {lb} share {same_pos}/{smaller} "
                    f"({same_pos/smaller*100:.0f}%) decimal values at "
                    f"SAME (row,col) across 2 {scope}"
                ),
            ))
        elif same_val >= max(8, smaller * 0.4):
            emit(dict(
                kind="cross_sheet_value_overlap",
                file=fa if same_file else f"{fa} + {fb}",
                file_a=fa,
                file_b=fb,
                same_file=same_file,
                sheet_a=la,
                sheet_b=lb,
                size_a=size_a,
                size_b=size_b,
                shared_value_count=same_val,
                fraction_of_smaller=same_val / smaller,
                examples=list(pair_stats.shared_value_examples),
                severity="low" if same_figure else "medium",
                **ctx_fields,
                rule=(
                    f"{la} and {lb} share {same_val} bit-identical "
                    f"decimal values ({same_val/smaller*100:.0f}% of "
                    f"smaller) across 2 {scope}"
                ),
            ))

        if smaller < _DECIMAL_TAIL_MIN_CELLS:
            continue

        tail_min_matches = max(
            8, min(20, math.ceil(smaller * 0.03))
        )
        tail_reuse, tail_coverage = (
            _detect_decimal_tail_reuse_for_pair(
                ga,
                gb,
                min_matches=tail_min_matches,
                budget=budget,
                with_coverage=True,
            )
        )
        if not resolve_pair_family(tail_coverage):
            break
        if tail_reuse:
            pairs = tail_reuse["pairs"]
            context_pairs = pairs[:40]
            cells_a = [
                (ka, va)
                for ka, _kb, va, _vb, _sig in context_pairs
            ]
            cells_b = [
                (kb, vb)
                for _ka, kb, _va, vb, _sig in context_pairs
            ]
            label_context_a = _label_context_for_matches(
                (sheets or {}).get(key_a), cells_a
            )
            label_context_b = _label_context_for_matches(
                (sheets or {}).get(key_b), cells_b
            )
            fraction_of_smaller = (
                tail_reuse["tail_match_count"] / smaller
            )
            off_r, off_c = tail_reuse["offset"]
            low_reason = (
                None
                if same_figure
                else _decimal_tail_low_reason(pairs)
            )
            note_reason = None
            if same_figure:
                sev = "low"
            elif low_reason:
                sev = "low"
            elif (
                tail_reuse["tail_match_count"] >= 12
                or fraction_of_smaller >= 0.10
            ):
                sev = "high"
                note_reason = _decimal_tail_note_reason(
                    pairs, (label_context_a, label_context_b)
                )
            else:
                sev = "medium"
                note_reason = _decimal_tail_note_reason(
                    pairs, (label_context_a, label_context_b)
                )
            examples = [
                {
                    "row_a": ka[0] + 1,
                    "col_a": ka[1] + 1,
                    "value_a": va,
                    "row_b": kb[0] + 1,
                    "col_b": kb[1] + 1,
                    "value_b": vb,
                    "decimal_tail": sig,
                }
                for ka, kb, va, vb, sig in pairs[:8]
            ]
            tail_fields = dict(ctx_fields)
            if same_figure and "context" not in tail_fields:
                tail_fields["context"] = context
            tail_benign_reason = low_reason or note_reason
            if tail_benign_reason:
                tail_fields[
                    "tail_benign_reason"
                ] = tail_benign_reason
            emit(dict(
                kind="cross_sheet_decimal_tail_reuse",
                file=fa if same_file else f"{fa} + {fb}",
                file_a=fa,
                file_b=fb,
                same_file=same_file,
                sheet_a=la,
                sheet_b=lb,
                size_a=size_a,
                size_b=size_b,
                tail_match_count=tail_reuse["tail_match_count"],
                fraction_of_smaller=fraction_of_smaller,
                offset_rows=off_r,
                offset_cols=off_c,
                min_tail_digits=tail_reuse[
                    "min_tail_digits"
                ],
                skip_decimal_digits=tail_reuse[
                    "skip_decimal_digits"
                ],
                label_context_a=label_context_a,
                label_context_b=label_context_b,
                examples=examples,
                severity=sev,
                **tail_fields,
                rule=(
                    f"{la} and {lb} share "
                    f"{tail_reuse['tail_match_count']}/{smaller} "
                    "changed decimal cells with the same long "
                    f"fractional tail at offset ({off_r}, {off_c}) "
                    f"across 2 {scope}"
                ),
            ))
    apply_profile_to_findings(findings, profile)
    return findings


def _column_axis_like(a):
    """True if a numeric column is an axis/index whose recurrence across panels is mundane: a
    (near-)constant column, a perfect ARITHMETIC progression (time/dose grid), or a perfect
    GEOMETRIC progression (serial-dilution axis) — the latter is legitimately shared across
    dose-response panels and must not read as a cross-experiment duplication."""
    if len(a) < 2:
        return True
    if len({round(float(v), 9) for v in a}) <= 1:
        return True                                   # constant
    source_ulp_tolerance = max_ulp_tolerance(a)
    diffs = np.diff(a)
    if (
        np.allclose(
            diffs,
            diffs[0],
            atol=source_ulp_tolerance,
            rtol=1e-9,
        )
        and abs(diffs[0]) > source_ulp_tolerance
    ):
        return True                                   # arithmetic ladder
    if np.all(np.abs(a) > 1e-12):                     # geometric ladder (serial dilution)
        ratios = a[1:] / a[:-1]
        if np.allclose(ratios, ratios[0], atol=1e-9, rtol=1e-9) and abs(ratios[0] - 1) > 1e-9:
            return True
    return False


def _numeric_ratio(value):
    if isinstance(value, int):
        return value, 1
    return float(value).as_integer_ratio()


def _fingerprint_example_value(value):
    if isinstance(value, int) and abs(value) > _MAX_EXACT_FLOAT_INT:
        return value
    return float(value)


class _BoundedDistinctValues:
    def __init__(self, limit):
        self.limit = (
            None if limit is None else max(0, int(limit))
        )
        self.values = set()
        self.overflowed = False

    def add(self, value):
        if value in self.values:
            return
        if (
            self.limit is not None
            and len(self.values) >= self.limit
        ):
            self.overflowed = True
            return
        self.values.add(value)


def _pattern_error(left, right):
    if left == right:
        return 0.0
    error = abs(left - right)
    return error if math.isfinite(error) else math.inf


def _stream_column_fingerprint(
    file,
    sheet,
    source,
    *,
    r0,
    r1,
    col_idx,
    label,
    min_column_length,
    distinct_limit,
):
    digest = hashlib.blake2b(digest_size=20)
    exact_distinct = _BoundedDistinctValues(distinct_limit)
    rounded_distinct = _BoundedDistinctValues(distinct_limit)
    sample = []
    length = 0
    all_int = True
    requires_exact_qualification = False

    exact_constant = True
    exact_first = None
    exact_previous = None
    exact_first_difference = None
    exact_arithmetic = True
    exact_all_nonzero = True
    exact_first_ratio = None
    exact_geometric = True

    float_constant = True
    float_first_rounded = None
    float_previous = None
    float_first_difference = None
    float_difference_error = 0.0
    float_all_nonzero = True
    float_first_ratio = None
    float_ratio_error = 0.0
    float_source_ulp_tolerance = 0.0

    for row_idx in range(r0, r1):
        value = source.exact_numeric(row_idx, col_idx)
        if value is None:
            continue
        length += 1
        if len(sample) < 5:
            sample.append(value)
        numerator, denominator = _numeric_ratio(value)
        exact_value = (numerator, denominator)
        exact_distinct.add(exact_value)
        digest.update(
            f"{numerator}/{denominator};".encode("ascii")
        )
        all_int = all_int and denominator == 1
        is_wide_integer = (
            isinstance(value, int)
            and abs(value) > _MAX_EXACT_FLOAT_INT
        )
        requires_exact_qualification = (
            requires_exact_qualification
            or is_wide_integer
        )

        fraction = Fraction(numerator, denominator)
        if exact_first is None:
            exact_first = fraction
        elif fraction != exact_first:
            exact_constant = False
        if fraction == 0:
            exact_all_nonzero = False
        if exact_previous is not None:
            difference = fraction - exact_previous
            if exact_first_difference is None:
                exact_first_difference = difference
            elif difference != exact_first_difference:
                exact_arithmetic = False
            if exact_previous != 0 and fraction != 0:
                ratio = fraction / exact_previous
                if exact_first_ratio is None:
                    exact_first_ratio = ratio
                elif ratio != exact_first_ratio:
                    exact_geometric = False
        exact_previous = fraction

        if not requires_exact_qualification:
            numeric = float(value)
            rounded_value = round(numeric, 9)
            rounded_distinct.add(rounded_value)
            float_source_ulp_tolerance = max(
                float_source_ulp_tolerance,
                scalar_ulp_tolerance(numeric),
            )
            if float_first_rounded is None:
                float_first_rounded = rounded_value
            elif rounded_value != float_first_rounded:
                float_constant = False
            if abs(numeric) <= 1e-12:
                float_all_nonzero = False
            if float_previous is not None:
                difference = numeric - float_previous
                if float_first_difference is None:
                    float_first_difference = difference
                else:
                    float_difference_error = max(
                        float_difference_error,
                        _pattern_error(
                            difference, float_first_difference
                        ),
                    )
                if (
                    abs(float_previous) > 1e-12
                    and abs(numeric) > 1e-12
                ):
                    ratio = numeric / float_previous
                    if float_first_ratio is None:
                        float_first_ratio = ratio
                    else:
                        float_ratio_error = max(
                            float_ratio_error,
                            _pattern_error(
                                ratio, float_first_ratio
                            ),
                        )
            float_previous = numeric

    if length < min_column_length:
        return None, None

    if requires_exact_qualification:
        axis_like = (
            length < 2
            or exact_constant
            or (
                exact_first_difference is not None
                and exact_first_difference != 0
                and exact_arithmetic
            )
            or (
                exact_all_nonzero
                and exact_first_ratio is not None
                and exact_first_ratio != 1
                and exact_geometric
            )
        )
        qualification_distinct = exact_distinct
    else:
        difference_tolerance = (
            float_source_ulp_tolerance
            + 1e-9 * abs(float_first_difference or 0.0)
        )
        arithmetic = (
            float_first_difference is not None
            and abs(float_first_difference) > difference_tolerance
            and float_difference_error
            <= (
                difference_tolerance
                + 1e-9 * abs(float_first_difference)
            )
        )
        geometric = (
            float_all_nonzero
            and float_first_ratio is not None
            and abs(float_first_ratio - 1) > 1e-9
            and float_ratio_error
            <= 1e-9 + 1e-9 * abs(float_first_ratio)
        )
        axis_like = (
            length < 2
            or float_constant
            or arithmetic
            or geometric
        )
        qualification_distinct = rounded_distinct
    if axis_like:
        return None, None

    required_distinct = max(6, length // 2)
    if qualification_distinct.overflowed:
        return None, InputLimitation(
            scope="sheet",
            reason="column_fingerprint_distinct_limit",
            sheet=sheet,
            details={
                "detector": "cross_sheet_column_duplicate",
                "column": col_idx + 1,
                "rows": f"{r0 + 1}-{r1}",
                "numeric_cells": length,
                "limit": max(0, int(distinct_limit)),
            },
        )
    if len(qualification_distinct.values) < required_distinct:
        return None, None
    if exact_distinct.overflowed:
        return None, InputLimitation(
            scope="sheet",
            reason="column_fingerprint_distinct_limit",
            sheet=sheet,
            details={
                "detector": "cross_sheet_column_duplicate",
                "column": col_idx + 1,
                "rows": f"{r0 + 1}-{r1}",
                "numeric_cells": length,
                "limit": max(0, int(distinct_limit)),
            },
        )

    return ColumnFingerprint(
        file=file,
        sheet=sheet,
        col_idx=col_idx,
        label=label,
        length=length,
        digest=digest.hexdigest(),
        all_int=all_int,
        distinct=len(exact_distinct.values),
        sample=tuple(sample),
    ), None


def _iter_column_intervals_in_order(blocks):
    previous_start = None
    ordered = True
    for _, _, start, stop in blocks:
        if stop <= start:
            continue
        if previous_start is not None and start < previous_start:
            ordered = False
            break
        previous_start = start
    if ordered:
        for _, _, start, stop in blocks:
            if stop > start:
                yield start, stop
        return

    previous_key = None
    while True:
        next_key = None
        for block_index, (_, _, start, stop) in enumerate(blocks):
            if stop <= start:
                continue
            key = (start, stop, block_index)
            if previous_key is not None and key <= previous_key:
                continue
            if next_key is None or key < next_key:
                next_key = key
        if next_key is None:
            return
        previous_key = next_key
        yield next_key[0], next_key[1]


def _iter_merged_column_intervals(blocks):
    merged_start = None
    merged_stop = None
    for start, stop in _iter_column_intervals_in_order(blocks):
        if merged_start is None:
            merged_start, merged_stop = start, stop
        elif start <= merged_stop:
            merged_stop = max(merged_stop, stop)
        else:
            yield merged_start, merged_stop
            merged_start, merged_stop = start, stop
    if merged_start is not None:
        yield merged_start, merged_stop


def _fingerprint_column_counts(blocks, column_limit):
    column_limit = (
        None
        if column_limit is None
        else max(0, int(column_limit))
    )
    selected_count = 0
    columns_total = 0
    for start, stop in _iter_merged_column_intervals(blocks):
        interval_size = stop - start
        columns_total += interval_size
        if column_limit is None:
            selected_count += interval_size
        elif selected_count < column_limit:
            selected_count += min(
                interval_size,
                column_limit - selected_count,
            )
    return selected_count, columns_total


def _selected_fingerprint_columns(blocks, column_limit):
    column_limit = (
        None
        if column_limit is None
        else max(0, int(column_limit))
    )
    selected = []
    columns_total = 0
    for start, stop in _iter_merged_column_intervals(blocks):
        interval_size = stop - start
        remaining = (
            interval_size
            if column_limit is None
            else column_limit - len(selected)
        )
        if remaining > 0:
            selected.extend(
                range(start, start + min(interval_size, remaining))
            )
        columns_total += interval_size
    return tuple(selected), columns_total


def _column_fingerprints(
    file,
    sheet,
    source,
    blocks,
    min_column_length,
    distinct_limit=None,
    column_limit=None,
    retained_limit=None,
    *,
    selected_columns=None,
    columns_total=None,
    with_metrics=False,
):
    column_limit = (
        None
        if column_limit is None
        else max(0, int(column_limit))
    )
    if (selected_columns is None) != (columns_total is None):
        raise AssertionError(
            "selected columns and total must be supplied together"
        )
    if selected_columns is None:
        selected_columns, columns_total = (
            _selected_fingerprint_columns(
                blocks,
                column_limit,
            )
        )
    else:
        selected_columns = tuple(selected_columns)
        columns_total = max(0, int(columns_total))
        if columns_total < len(selected_columns):
            raise AssertionError(
                "selected columns exceed declared total"
            )
    columns_used = len(selected_columns)
    fingerprint_limit = (
        columns_used
        if retained_limit is None
        else max(0, int(retained_limit))
    )

    best = {}
    retained_column_heap = []
    qualified_columns = bytearray(columns_used)
    fingerprints_required = 0
    distinct_overflows = {}
    for r0, r1, c0, c1 in blocks:
        selected_start = bisect_left(selected_columns, c0)
        selected_stop = bisect_left(selected_columns, c1)
        if selected_start == selected_stop:
            continue
        header_row = None
        for row_idx in range(r0 - 1, max(-1, r0 - 5), -1):
            if row_idx < 0:
                continue
            if any(
                (
                    (value := source.cell(
                        row_idx,
                        selected_columns[column_pos],
                    ))
                    is not None
                    and not is_num(value)
                )
                for column_pos in range(
                    selected_start,
                    selected_stop,
                )
            ):
                header_row = row_idx
                break
        for column_pos in range(selected_start, selected_stop):
            col_idx = selected_columns[column_pos]
            label_value = (
                source.cell(header_row, col_idx)
                if header_row is not None
                else None
            )
            fingerprint, limitation = _stream_column_fingerprint(
                file=file,
                sheet=sheet,
                source=source,
                r0=r0,
                r1=r1,
                col_idx=col_idx,
                label=(
                    str(label_value).strip()
                    if label_value is not None
                    else ""
                ),
                min_column_length=min_column_length,
                distinct_limit=distinct_limit,
            )
            if limitation is not None:
                example = {
                    "column": limitation.details["column"],
                    "rows": limitation.details["rows"],
                    "numeric_cells": limitation.details[
                        "numeric_cells"
                    ],
                }
                current = distinct_overflows.get(col_idx)
                example_key = (
                    example["rows"],
                    example["numeric_cells"],
                )
                if (
                    current is None
                    or example_key
                    < (current["rows"], current["numeric_cells"])
                ):
                    distinct_overflows[col_idx] = example
            if fingerprint is None:
                continue
            if not qualified_columns[column_pos]:
                qualified_columns[column_pos] = 1
                fingerprints_required += 1
            current = best.get(col_idx)
            if current is not None:
                if fingerprint.length > current.length:
                    best[col_idx] = fingerprint
                continue
            if fingerprint_limit <= 0:
                continue
            if len(best) < fingerprint_limit:
                best[col_idx] = fingerprint
                heapq.heappush(retained_column_heap, -col_idx)
                continue
            highest_retained = -retained_column_heap[0]
            if col_idx < highest_retained:
                heapq.heapreplace(retained_column_heap, -col_idx)
                del best[highest_retained]
                best[col_idx] = fingerprint

    limitations = []
    if distinct_overflows:
        examples = [
            distinct_overflows[col_idx]
            for col_idx in sorted(distinct_overflows)[
                :_COLUMN_FINGERPRINT_EXAMPLE_LIMIT
            ]
        ]
        limitations.append(InputLimitation(
            scope="sheet",
            reason="column_fingerprint_distinct_limit",
            sheet=sheet,
            details={
                "detector": "cross_sheet_column_duplicate",
                "affected_columns": len(distinct_overflows),
                "examples": examples,
                "limit": max(0, int(distinct_limit)),
            },
        ))
    columns_skipped = columns_total - columns_used
    if columns_skipped:
        limitations.append(InputLimitation(
            scope="sheet",
            reason="column_fingerprint_column_limit",
            sheet=sheet,
            details={
                "detector": "cross_sheet_column_duplicate",
                "columns_total": columns_total,
                "columns_used": columns_used,
                "columns_skipped": columns_skipped,
                "limit": column_limit,
            },
        ))
    result = (
        tuple(best[col_idx] for col_idx in sorted(best)),
        limitations,
    )
    if with_metrics:
        return (*result, {
            "column_fingerprints": fingerprints_required,
        })
    return result


_SUMMARY_DIMENSIONS = (
    "summaries",
    "grid_cells",
    "label_cells",
    "label_bytes",
    "column_fingerprints",
)


class CrossSheetSummaryReservation:
    def __init__(self, budget):
        self.budget = budget
        self._reserved = {
            dimension: 0 for dimension in _SUMMARY_DIMENSIONS
        }
        self._closed = False
        self._validated_metrics = None

    @property
    def closed(self):
        return self._closed

    def reserve_capacity(self, dimension, count, *, rejection=None):
        if self._closed:
            raise AssertionError("summary reservation is closed")
        count = max(0, int(count))
        available = self.budget.available_metadata()[dimension]
        if count > available:
            details = {
                "skipped_items": max(1, count),
            }
            if rejection:
                details.update(rejection)
            self.reject({dimension: details})
            return False
        self._reserved[dimension] += count
        self.budget._reserved[dimension] += count
        return True

    def reserve_fingerprint_candidates(self, count):
        count = max(0, int(count))
        return self.reserve_capacity(
            "column_fingerprints",
            count,
            rejection={
                "candidate_columns_skipped": count,
                "candidate_columns_may_qualify": True,
            },
        )

    def amount(self, dimension):
        return self._reserved[dimension]

    @staticmethod
    def _normalize_metrics(metrics):
        return {
            dimension: (
                1
                if dimension == "summaries"
                else max(0, int(metrics[dimension]))
            )
            for dimension in _SUMMARY_DIMENSIONS
        }

    def validate_metrics(self, metrics):
        if self._closed:
            raise AssertionError("summary reservation is closed")
        actual_metrics = self._normalize_metrics(metrics)
        exceeded = {}
        for dimension in _SUMMARY_DIMENSIONS:
            actual = actual_metrics[dimension]
            reserved = self._reserved[dimension]
            if actual > reserved:
                exceeded[dimension] = {
                    "skipped_items": max(1, actual),
                }
        if exceeded:
            self.reject(exceeded)
            return False
        self._validated_metrics = actual_metrics
        return True

    def validate_metric(
        self, dimension, required, *, required_is_lower_bound=False
    ):
        if self._closed:
            raise AssertionError("summary reservation is closed")
        if dimension not in _SUMMARY_DIMENSIONS:
            raise AssertionError(
                f"unknown summary dimension: {dimension}"
            )
        actual = (
            1
            if dimension == "summaries"
            else max(0, int(required))
        )
        if actual > self._reserved[dimension]:
            details = {"skipped_items": max(1, actual)}
            if required_is_lower_bound:
                details["skipped_items_is_lower_bound"] = True
            self.reject({dimension: details})
            return False
        return True

    def commit(self, metrics):
        if self._closed:
            raise AssertionError("summary reservation is closed")
        actual_metrics = self._normalize_metrics(metrics)
        if actual_metrics != self._validated_metrics:
            raise AssertionError(
                "summary metrics changed after validation"
            )
        self.budget._commit_reservation(
            self,
            actual_metrics,
        )
        self._closed = True
        return True

    def reject(self, dimensions):
        if self._closed:
            return
        self.budget._reject_reservation(self, dimensions)
        self._validated_metrics = None
        self._closed = True

    def rollback(self):
        if self._closed:
            return
        self.budget._release_reservation(self)
        self._validated_metrics = None
        self._closed = True


@dataclass
class CrossSheetSummaryBudget:
    summary_limit: int
    grid_cell_limit: int
    label_cell_limit: int
    label_byte_limit: int
    column_fingerprint_limit: int
    summaries_considered: int = 0
    summaries_retained: int = 0
    grid_cells_retained: int = 0
    label_cells_retained: int = 0
    label_bytes_retained: int = 0
    column_fingerprints_retained: int = 0
    summaries_skipped: int = 0
    _exhausted: dict = None

    def __post_init__(self):
        self.summary_limit = max(0, int(self.summary_limit))
        self.grid_cell_limit = max(0, int(self.grid_cell_limit))
        self.label_cell_limit = max(0, int(self.label_cell_limit))
        self.label_byte_limit = max(0, int(self.label_byte_limit))
        self.column_fingerprint_limit = max(
            0, int(self.column_fingerprint_limit)
        )
        if self._exhausted is None:
            self._exhausted = {}
        self._reserved = {
            dimension: 0 for dimension in _SUMMARY_DIMENSIONS
        }

    def _limits(self):
        return {
            "summaries": self.summary_limit,
            "grid_cells": self.grid_cell_limit,
            "label_cells": self.label_cell_limit,
            "label_bytes": self.label_byte_limit,
            "column_fingerprints": (
                self.column_fingerprint_limit
            ),
        }

    def retained_metadata(self):
        return {
            "summaries": self.summaries_retained,
            "grid_cells": self.grid_cells_retained,
            "label_cells": self.label_cells_retained,
            "label_bytes": self.label_bytes_retained,
            "column_fingerprints": (
                self.column_fingerprints_retained
            ),
        }

    def reserved_metadata(self):
        return dict(self._reserved)

    def available_metadata(self):
        retained = self.retained_metadata()
        limits = self._limits()
        return {
            dimension: max(
                0,
                limits[dimension]
                - retained[dimension]
                - self._reserved[dimension],
            )
            for dimension in _SUMMARY_DIMENSIONS
        }

    def remaining_metadata(self):
        return self.available_metadata()

    def _record_rejection(self, dimensions):
        self.summaries_skipped += 1
        retained = self.retained_metadata()
        limits = self._limits()
        for dimension, raw in dimensions.items():
            details = (
                {"skipped_items": raw}
                if isinstance(raw, int)
                else dict(raw)
            )
            skipped_items = max(
                1, int(details.pop("skipped_items", 1))
            )
            item = self._exhausted.setdefault(dimension, {
                "limit": limits[dimension],
                "retained": retained[dimension],
                "skipped_sheets": 0,
                "skipped_items": 0,
            })
            item["retained"] = retained[dimension]
            item["skipped_sheets"] += 1
            item["skipped_items"] += skipped_items
            for key, value in details.items():
                if (
                    key.endswith("_skipped")
                    and isinstance(value, int)
                ):
                    item[key] = item.get(key, 0) + value
                else:
                    item[key] = value

    def start_summary(self):
        self.summaries_considered += 1
        if self.available_metadata()["summaries"] < 1:
            self._record_rejection({"summaries": 1})
            return None
        reservation = CrossSheetSummaryReservation(self)
        if not reservation.reserve_capacity("summaries", 1):
            raise AssertionError("summary slot reservation diverged")
        return reservation

    def _release_reservation(self, reservation):
        for dimension, count in reservation._reserved.items():
            if count > self._reserved[dimension]:
                raise AssertionError("summary reservation underflow")
            self._reserved[dimension] -= count
            reservation._reserved[dimension] = 0

    def _commit_reservation(self, reservation, metrics):
        self._release_reservation(reservation)
        self.summaries_retained += 1
        self.grid_cells_retained += int(metrics["grid_cells"])
        self.label_cells_retained += int(metrics["label_cells"])
        self.label_bytes_retained += int(metrics["label_bytes"])
        self.column_fingerprints_retained += int(
            metrics["column_fingerprints"]
        )

    def _reject_reservation(self, reservation, dimensions):
        self._release_reservation(reservation)
        self._record_rejection(dimensions)

    def limitation_metadata(self):
        retained = self.retained_metadata()
        unavailable_pairs = (
            self.summaries_considered
            * (self.summaries_considered - 1)
            // 2
            - self.summaries_retained
            * (self.summaries_retained - 1)
            // 2
        )
        return {
            "summaries_considered": self.summaries_considered,
            "summaries_retained": self.summaries_retained,
            "summaries_skipped": self.summaries_skipped,
            "summary_pairs_unavailable": unavailable_pairs,
            "exhausted_dimensions": [
                dimension for dimension in _SUMMARY_DIMENSIONS
                if dimension in self._exhausted
            ],
            "dimensions": {
                dimension: {
                    **self._exhausted[dimension],
                    "retained": retained[dimension],
                }
                for dimension in _SUMMARY_DIMENSIONS
                if dimension in self._exhausted
            },
        }

    def coverage_limitations(self):
        reasons = {
            "summaries": "cross_sheet_summary_count_limit",
            "grid_cells": "cross_sheet_grid_cell_limit",
            "label_cells": "cross_sheet_label_cell_limit",
            "label_bytes": "cross_sheet_label_byte_limit",
            "column_fingerprints": (
                "cross_sheet_column_fingerprint_limit"
            ),
        }
        metadata = self.limitation_metadata()
        return [
            {
                "reason": reasons[dimension],
                "dimension": dimension,
                **metadata["dimensions"][dimension],
                "summary_pairs_unavailable": metadata[
                    "summary_pairs_unavailable"
                ],
                "omitted_findings_lower_bound": 0,
            }
            for dimension in metadata["exhausted_dimensions"]
        ]


def _bounded_sparse_label_context(
    source,
    *,
    row_limit,
    retained_cell_limit=None,
    retained_byte_limit=None,
):
    cell_limit = (
        None
        if retained_cell_limit is None
        else max(0, int(retained_cell_limit))
    )
    byte_limit = (
        None
        if retained_byte_limit is None
        else max(0, int(retained_byte_limit))
    )
    text = {}
    retained_bytes = 0
    cells_required = 0
    bytes_required = 0
    cells_required_is_lower_bound = False
    bytes_required_is_lower_bound = False
    for (row_idx, col_idx), value in source._text.items():
        if row_idx >= row_limit or not isinstance(value, str):
            continue
        cells_required += 1
        if (
            cell_limit is not None
            and cells_required > cell_limit
        ):
            cells_required_is_lower_bound = True
            bytes_required_is_lower_bound = True
            break
        remaining_bytes = (
            None
            if byte_limit is None
            else max(0, byte_limit - bytes_required)
        )
        payload_bytes = 0
        for char in value:
            codepoint = ord(char)
            if codepoint <= 0x7F:
                width = 1
            elif codepoint <= 0x7FF:
                width = 2
            elif 0xD800 <= codepoint <= 0xDFFF:
                char.encode("utf-8")
                raise AssertionError("unreachable UTF-8 surrogate")
            elif codepoint <= 0xFFFF:
                width = 3
            else:
                width = 4
            if (
                remaining_bytes is not None
                and payload_bytes + width > remaining_bytes
            ):
                payload_bytes = remaining_bytes + 1
                bytes_required_is_lower_bound = True
                cells_required_is_lower_bound = True
                break
            payload_bytes += width
        bytes_required += payload_bytes
        if bytes_required_is_lower_bound:
            break
        if (
            byte_limit is not None
            and retained_bytes + payload_bytes > byte_limit
        ):
            continue
        text[(row_idx, col_idx)] = value
        retained_bytes += payload_bytes
    return (
        SparseLabelContext(
            nrows=source.nrows,
            ncols=source.ncols,
            text=text,
        ),
        dict({
            "label_cells": cells_required,
            "label_bytes": bytes_required,
        }, **(
            {"label_cells_is_lower_bound": True}
            if cells_required_is_lower_bound
            else {}
        ), **(
            {"label_bytes_is_lower_bound": True}
            if bytes_required_is_lower_bound
            else {}
        )),
    )


def build_cross_sheet_summary(
    file,
    sheet,
    source,
    *,
    blocks=None,
    collision_max_rows=200,
    collision_max_cells=200000,
    min_column_length=12,
    budget=None,
    column_distinct_limit=None,
    column_limit=None,
) -> tuple[CrossSheetSummary | None, list[InputLimitation]]:
    reservation = (
        budget.start_summary() if budget is not None else None
    )
    if budget is not None and reservation is None:
        return None, []

    try:
        if blocks is None:
            blocks = find_numeric_blocks(source)
        if reservation is not None:
            available = budget.available_metadata()
            for dimension in (
                "grid_cells",
                "label_cells",
                "label_bytes",
            ):
                if not reservation.reserve_capacity(
                    dimension, available[dimension]
                ):
                    raise AssertionError(
                        f"{dimension} reservation diverged"
                    )

        grid, grid_meta = _grid_from_rows(
            source,
            max_rows=collision_max_rows,
            max_cells=collision_max_cells,
            retained_cell_limit=(
                reservation.amount("grid_cells")
                if reservation is not None else None
            ),
            with_coverage=True,
        )
        if (
            reservation is not None
            and not reservation.validate_metric(
                "grid_cells", grid_meta["cells_used"]
            )
        ):
            return None, []
        label_row_limit = min(
            source.nrows, collision_max_rows + 3
        )
        labels, label_metrics = _bounded_sparse_label_context(
            source,
            row_limit=label_row_limit,
            retained_cell_limit=(
                reservation.amount("label_cells")
                if reservation is not None else None
            ),
            retained_byte_limit=(
                reservation.amount("label_bytes")
                if reservation is not None else None
            ),
        )
        if reservation is not None:
            if not reservation.validate_metric(
                "label_cells",
                label_metrics["label_cells"],
                required_is_lower_bound=label_metrics.get(
                    "label_cells_is_lower_bound", False
                ),
            ):
                return None, []
            if not reservation.validate_metric(
                "label_bytes",
                label_metrics["label_bytes"],
                required_is_lower_bound=label_metrics.get(
                    "label_bytes_is_lower_bound", False
                ),
            ):
                return None, []
            candidate_count, columns_total = (
                _fingerprint_column_counts(
                    blocks,
                    column_limit,
                )
            )
            if not reservation.reserve_fingerprint_candidates(
                candidate_count
            ):
                return None, []
        selected_columns, selected_columns_total = (
            _selected_fingerprint_columns(
                blocks,
                column_limit,
            )
        )
        if reservation is None:
            columns_total = selected_columns_total
        elif (
            len(selected_columns) != candidate_count
            or selected_columns_total != columns_total
        ):
            raise AssertionError(
                "fingerprint candidate count changed after admission"
            )
        (
            columns,
            column_limitations,
            column_metrics,
        ) = _column_fingerprints(
            file,
            sheet,
            source,
            blocks,
            min_column_length,
            distinct_limit=column_distinct_limit,
            column_limit=column_limit,
            selected_columns=selected_columns,
            columns_total=columns_total,
            retained_limit=(
                reservation.amount("column_fingerprints")
                if reservation is not None else None
            ),
            with_metrics=True,
        )
        if (
            reservation is not None
            and not reservation.validate_metric(
                "column_fingerprints",
                column_metrics["column_fingerprints"],
            )
        ):
            return None, []
        metrics = {
            "grid_cells": grid_meta["cells_used"],
            **label_metrics,
            **column_metrics,
        }
        if (
            reservation is not None
            and not reservation.validate_metrics(metrics)
        ):
            return None, []
        summary = CrossSheetSummary(
            file=file,
            sheet=sheet,
            grid=grid,
            labels=labels,
            columns=columns,
        )
        limitations = list(column_limitations)
        if grid_meta["row_limited"]:
            limitations.append(InputLimitation(
                scope="sheet",
                reason="collision_row_limit",
                sheet=sheet,
                details={
                    "rows_total": grid_meta["rows_total"],
                    "rows_used": grid_meta["rows_used"],
                },
            ))
        if grid_meta["cell_limited"]:
            limitations.append(InputLimitation(
                scope="sheet",
                reason="collision_cell_limit",
                sheet=sheet,
                details={
                    "cells_used": grid_meta["cells_used"],
                    "max_cells": max(
                        0, int(collision_max_cells)
                    ),
                },
            ))
        if reservation is not None:
            if not reservation.commit(metrics):
                raise AssertionError(
                    "validated summary commit was rejected"
                )
        return summary, limitations
    finally:
        if reservation is not None and not reservation.closed:
            reservation.rollback()


def detect_cross_sheet_column_duplicates(
    grid_sheets, profile="review", min_len=12, budget=None
):
    """B1 — full-column duplication ACROSS different (file, sheet) panels, including the
    integer / 1-decimal columns that `detect_collisions` misses (it grids only >=3-decimal
    values). Two panels that should be independent measurements carrying a byte-identical
    ordered column is a cross-experiment reuse fingerprint (e.g. a comet-assay 'No IR' column
    reproduced across two different figures). Same-figure panels are downgraded to low
    (a combined plot and its per-replicate breakdown legitimately share a column)."""
    if len(grid_sheets) < 2:
        return []                                     # a cross-panel duplicate needs >=2 panels
    if hasattr(grid_sheets, "items"):
        summaries = [
            build_cross_sheet_summary(
                file,
                sheet,
                source,
                min_column_length=min_len,
            )[0]
            for (file, sheet), source in grid_sheets.items()
        ]
    else:
        summaries = list(grid_sheets)

    buckets = {}
    for summary in summaries:
        for column in summary.columns:
            if column.length >= min_len:
                key = (column.length, column.digest)
                buckets.setdefault(key, []).append(column)

    findings = []
    total_pairs = 0
    for group in buckets.values():
        if not group:
            continue
        first = group[0]
        if first.all_int and (
            first.length < 25
            or first.distinct < max(
                12, int(0.7 * first.length)
            )
        ):
            continue
        panel_counts = Counter(
            (column.file, column.sheet) for column in group
        )
        total_pairs += (
            len(group) * (len(group) - 1) // 2
            - sum(
                count * (count - 1) // 2
                for count in panel_counts.values()
            )
        )
    candidate_index = 0
    stopped = False
    for group in buckets.values():
        panels = {(column.file, column.sheet) for column in group}
        if len(panels) < 2:
            continue                                  # same-panel identical cols are identical_column's job
        first = group[0]
        n = first.length
        all_int = first.all_int
        # all-integer sequences recur far more benignly (counts, indices) → require length + variety
        if all_int and (
            n < 25
            or first.distinct < max(12, int(0.7 * n))
        ):
            continue
        bucket_findings_seen = 0
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                left = group[i]
                right = group[j]
                fa, sa_name, la = left.file, left.sheet, left.label
                fb, sb_name, lb = right.file, right.sheet, right.label
                if (fa, sa_name) == (fb, sb_name):
                    continue                          # different columns, same sheet → identical_column
                if (
                    budget is not None
                    and not budget.consume_pair(0)
                ):
                    budget.skip_pairs(
                        max(
                            0,
                            total_pairs - candidate_index - 1,
                        )
                    )
                    stopped = True
                    break
                candidate_index += 1
                bucket_findings_seen += 1
                if bucket_findings_seen > 10:
                    if budget is not None:
                        budget.skip_bucket_findings(1)
                    continue
                fig_a, fig_b = figure_key(sa_name), figure_key(sb_name)
                same_figure = fig_a is not None and fig_a == fig_b
                same_file = fa == fb
                scope = "sheets" if same_file else "files"
                sev = "low" if (same_figure or all_int) else "high"
                finding = dict(
                    kind="cross_sheet_column_duplicate",
                    file=fa if same_file else f"{fa} + {fb}",
                    file_a=fa, file_b=fb, same_file=same_file,
                    sheet_a=sa_name, sheet_b=sb_name,
                    col_a=la, col_b=lb,
                    size_a=n, size_b=n,
                    same_position_count=n,
                    fraction_of_smaller=1.0,
                    figure_a=fig_a, figure_b=fig_b, same_figure=same_figure,
                    delta={"pattern": "column_duplicate"},
                    examples=[
                        {"value": _fingerprint_example_value(value)}
                        for value in first.sample
                    ],
                    severity=sev,
                    rule=(f"column '{la}' ({sa_name}) and column '{lb}' ({sb_name}) match to 6 decimal "
                          f"places over all {n} values across 2 {scope}"),
                )
                if budget is None or budget.retain_finding():
                    findings.append(finding)
            if stopped:
                break
        if stopped:
            break
    apply_profile_to_findings(findings, profile)
    return findings


def detect_recurring_row_vectors(grid_sheets, profile="review",
                                 min_k=4, max_k=8, max_rows=300, max_findings=20):
    """B2 — a fixed ordered numeric tuple recurring as a contiguous row-slice across >=3 places
    spanning >=2 figure namespaces. Six independent mice cannot yield the identical six-value
    vector in several arms; a specific high-information tuple reappearing across unrelated figures
    is a copy fingerprint. Guarded hard (this is the most FP-prone pass): >=3 distinct values, no
    arithmetic/geometric/round-number ladders, >=3 occurrences in >=2 figure namespaces, and
    all-integer tuples need k>=5 with >=4 distinct values."""
    # A finding needs >=2 distinct figure namespaces; skip the (expensive, FP-prone) window
    # build entirely when the corpus can never satisfy that (single sheet, or plainly-named
    # sheets whose figure_key is None).
    if len({figure_key(s) for (_f, s) in grid_sheets if figure_key(s) is not None}) < 2:
        return []
    index = RecurringRowIndex()
    for (fname, sname), sheet in grid_sheets.items():
        index.add_sheet(
            fname,
            sname,
            sheet,
            blocks=find_numeric_blocks(sheet),
            figure_id=figure_key(sname),
            min_k=min_k,
            max_k=max_k,
            max_rows=max_rows,
        )
    findings, _meta = index.findings(
        profile=profile,
        max_findings=max_findings,
    )
    return findings


@dataclass(frozen=True)
class _FractionReusePairStats:
    common: int
    shared: int
    integer_differences: int
    high_precision: int
    fraction_representatives: tuple[float, ...]
    difference_representatives: tuple[int, ...]


def _add_bounded_representative(values, value, limit):
    if len(values) < limit:
        values.add(value)


def _fraction_reuse_pair_stats(sheet, block_a, block_b):
    ar0, ar1, ac0, ac1 = block_a
    br0, br1, bc0, bc1 = block_b
    row_count = min(ar1 - ar0, br1 - br0)
    col_count = min(ac1 - ac0, bc1 - bc0)
    common = shared = integer_differences = high_precision = 0
    fractions = set()
    differences = set()
    for row_offset in range(row_count):
        for col_offset in range(col_count):
            left = sheet.exact_numeric(
                ar0 + row_offset, ac0 + col_offset
            )
            right = sheet.exact_numeric(
                br0 + row_offset, bc0 + col_offset
            )
            if left is None or right is None:
                continue
            common += 1
            x = float(left)
            y = float(right)
            if not bool(integer_shift_close((x,), (y,))[0]):
                continue
            shared += 1
            rounded_difference = round(y - x)
            if abs(rounded_difference) >= 1:
                integer_differences += 1
                _add_bounded_representative(
                    differences, rounded_difference, 2
                )
            if _sig_frac_digits(x) >= 3:
                high_precision += 1
                _add_bounded_representative(
                    fractions,
                    round(x - round(x), 6),
                    5,
                )
    return _FractionReusePairStats(
        common=common,
        shared=shared,
        integer_differences=integer_differences,
        high_precision=high_precision,
        fraction_representatives=tuple(fractions),
        difference_representatives=tuple(differences),
    )


def detect_within_sheet_fraction_reuse(
    grid_sheets,
    profile="review",
    min_cells=10,
    *,
    pair_budget=None,
    cell_budget=None,
    with_coverage=False,
):
    """B3 — two numeric blocks in the SAME sheet whose positionally-corresponding cells reproduce
    each other's HIGH-PRECISION decimal fractions while their integer parts differ by whole numbers
    (e.g. two dose-response matrices where every cell shares the 5-decimal fraction but the value
    was shifted by an integer). detect_relations only compares columns within one block and
    detect_collisions only compares distinct sheets, so this matrix-to-matrix within-sheet reuse
    has no other detector. The precision + integer-shift + coverage requirements make chance
    coincidence negligible."""
    pair_limit = (
        None
        if pair_budget is None
        else max(0, int(pair_budget))
    )
    cell_limit = (
        None
        if cell_budget is None
        else max(0, int(cell_budget))
    )
    findings = []
    limitations = []
    for (fname, sname), sheet in grid_sheets.items():
        blocks = find_numeric_blocks(sheet)
        total_pairs = len(blocks) * (len(blocks) - 1) // 2
        pairs_examined = 0
        cells_examined = 0
        limits_reached = []
        best = None                                            # keep only the strongest pair per sheet
        stop = False
        for i in range(len(blocks)):
            for j in range(i + 1, len(blocks)):
                ba, bb = blocks[i], blocks[j]
                if (
                    pair_limit is not None
                    and pairs_examined >= pair_limit
                ):
                    limits_reached.append("pair")
                    stop = True
                    break
                potential_cells = (
                    min(ba[1] - ba[0], bb[1] - bb[0])
                    * min(ba[3] - ba[2], bb[3] - bb[2])
                )
                if (
                    potential_cells >= min_cells
                    and cell_limit is not None
                    and cells_examined + potential_cells > cell_limit
                ):
                    limits_reached.append("cell")
                    stop = True
                    break
                pairs_examined += 1
                if potential_cells < min_cells:
                    continue
                cells_examined += potential_cells
                pair = _fraction_reuse_pair_stats(sheet, ba, bb)
                if pair.common < min_cells:
                    continue
                if (
                    pair.shared >= max(
                        min_cells, int(round(0.8 * pair.common))
                    )
                    and pair.high_precision >= max(
                        6, int(round(0.5 * pair.common))
                    )
                    and pair.integer_differences >= 3
                    and len(pair.difference_representatives) >= 2
                    and len(pair.fraction_representatives) >= 5
                    and (
                        best is None
                        or pair.shared > best[0]
                    )
                ):
                    best = (pair.shared, ba, bb, pair.common)
            if stop:
                break
        pairs_skipped = total_pairs - pairs_examined
        if pairs_skipped > 0 and limits_reached:
            limitations.append({
                "file": fname,
                "sheet": sname,
                "pair_limit": pair_limit,
                "cell_limit": cell_limit,
                "pairs_examined": pairs_examined,
                "cells_examined": cells_examined,
                "pairs_skipped": pairs_skipped,
                "limits_reached": limits_reached,
            })
        if best is not None:
            shared, ba, bb, ncommon = best
            findings.append(dict(
                kind="within_table_fraction_reuse",
                file=fname, file_a=fname, file_b=fname, same_file=True,
                sheet_a=sname, sheet_b=sname,
                size_a=ncommon, size_b=ncommon,
                same_position_count=shared,
                fraction_of_smaller=shared / ncommon,
                # both blocks live in ONE sheet, so there is no "two figures" to compare — leave
                # figure_a/b unset (None) rather than equal-but-not-same_figure (contradictory).
                figure_a=None, figure_b=None, same_figure=False,
                delta={"pattern": "fraction_reuse"},
                block_a=f"rows {ba[0]+1}-{ba[1]}, cols {ba[2]+1}-{ba[3]}",
                block_b=f"rows {bb[0]+1}-{bb[1]}, cols {bb[2]+1}-{bb[3]}",
                severity="high",
                rule=(f"two blocks in '{sname}' share identical decimal fractions on "
                      f"{shared}/{ncommon} positionally-corresponding cells but differ "
                      f"by whole numbers")))
    apply_profile_to_findings(findings, profile)
    if with_coverage:
        return findings, limitations
    return findings


def _load_provenance(in_dir, paper):
    """Resolve scan provenance: an explicit `paper` override wins; otherwise read a
    paperconan_source.json sidecar left by `fetch`; otherwise None."""
    if paper:
        return paper
    sidecar = os.path.join(in_dir, "paperconan_source.json")
    if os.path.isfile(sidecar):
        try:
            data = read_sidecar(
                sidecar,
                byte_limit=int(os.environ.get(
                    "PAPERCONAN_SOURCE_SIDECAR_MAX_BYTES",
                    str(2 * 1024 * 1024),
                )),
                entry_limit=int(os.environ.get(
                    "PAPERCONAN_SOURCE_SIDECAR_ENTRY_LIMIT",
                    "10000",
                )),
                name_byte_limit=int(os.environ.get(
                    "PAPERCONAN_SOURCE_SIDECAR_NAME_BYTES",
                    str(1024 * 1024),
                )),
                normalize_name=lambda value: value,
                retain_managed_names=False,
            )
        except SidecarLimitError:
            return None
        if not isinstance(data, dict):
            return None
        return data
    return None


# Per-file memory guard: workbooks above this size expand to many GB of Python objects
# when fully materialized, so they are skipped (recorded as oversized) before loading.
# Coarse byte backstop (generous — the precise guard is the cell-count cap below).
_MAX_FILE_MB = float(os.environ.get("PAPERCONAN_MAX_FILE_MB", "200"))
_MAX_FILE_BYTES = int(_MAX_FILE_MB * 1024 * 1024)
# Precise memory guard: the columnar substrate stores numeric cells in a dense float64
# array (~8 bytes/cell) instead of ~100-200 bytes/cell as Python objects, so a given cell
# budget now bounds far less RAM. Skip a sheet whose cell count exceeds this, checked from
# the sheet dimensions BEFORE materializing. Default 10M cells ≈ an 80MB numeric array.
_MAX_CELLS = int(os.environ.get("PAPERCONAN_MAX_CELLS", "10000000"))
# Sparse cells retain Python payloads and coordinate keys, so they need
# independent count and payload-byte bounds in addition to dense geometry.
_MAX_SPARSE_CELLS = int(
    os.environ.get("PAPERCONAN_MAX_SPARSE_CELLS", "250000")
)
_MAX_SPARSE_BYTES = int(
    os.environ.get(
        "PAPERCONAN_MAX_SPARSE_BYTES",
        str(64 * 1024 * 1024),
    )
)
# Exact column distinctness is retained only up to this fixed detector budget.
# Longer high-cardinality columns are skipped with structured coverage metadata
# rather than growing Python sets in proportion to accepted sheet size.
_COLUMN_FINGERPRINT_DISTINCT_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_COLUMN_FINGERPRINT_DISTINCT_LIMIT",
        "50000",
    )
)
# At most this many physical columns per sheet are fingerprinted, selected in
# ascending column order. Wider sheets receive exact structured coverage.
_COLUMN_FINGERPRINT_MAX_COLUMNS = int(
    os.environ.get(
        "PAPERCONAN_COLUMN_FINGERPRINT_MAX_COLUMNS",
        "512",
    )
)
_COLUMN_FINGERPRINT_EXAMPLE_LIMIT = 5
# Scan-wide retained cross-sheet summary state. A later sheet is retained only
# when its complete summary fits every remaining dimension.
_CROSS_SHEET_SUMMARY_LIMIT = int(
    os.environ.get("PAPERCONAN_CROSS_SHEET_SUMMARY_LIMIT", "2000")
)
_CROSS_SHEET_GRID_CELL_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_CROSS_SHEET_GRID_CELL_LIMIT",
        "2000000",
    )
)
_CROSS_SHEET_LABEL_CELL_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_CROSS_SHEET_LABEL_CELL_LIMIT",
        "500000",
    )
)
_CROSS_SHEET_LABEL_BYTE_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_CROSS_SHEET_LABEL_BYTE_LIMIT",
        str(32 * 1024 * 1024),
    )
)
_CROSS_SHEET_COLUMN_FINGERPRINT_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_CROSS_SHEET_COLUMN_FINGERPRINT_LIMIT",
        "200000",
    )
)
# Scan-wide cross-sheet comparison work. Candidate pairs count separately for
# each detector family; value work counts logical grid-value examinations.
_CROSS_SHEET_PAIR_BUDGET = int(
    os.environ.get("PAPERCONAN_CROSS_SHEET_PAIR_BUDGET", "1000000")
)
_CROSS_SHEET_VALUE_BUDGET = int(
    os.environ.get(
        "PAPERCONAN_CROSS_SHEET_VALUE_BUDGET",
        "50000000",
    )
)
_CROSS_SHEET_TAIL_MATCH_BUDGET = int(
    os.environ.get(
        "PAPERCONAN_CROSS_SHEET_TAIL_MATCH_BUDGET",
        "1000000",
    )
)
_CROSS_SHEET_FINDING_BUDGET = int(
    os.environ.get(
        "PAPERCONAN_CROSS_SHEET_FINDING_BUDGET",
        "10000",
    )
)
# Wide blocks (dense correlation matrices) can make relation, equal-pair, and row-pair
# coupling detector paths expensive in compute time and output size. Skip those three paths
# above this width while the column-wise detectors still run. 0 disables the skip.
_MAX_BLOCK_COLS = int(os.environ.get("PAPERCONAN_MAX_BLOCK_COLS", "120"))
# Dense detector paths are admitted only when a complete detector candidate
# fits all three bounds. Work is counted as logical numeric-cell visits;
# retained state uses float64-equivalent 8-byte units and also admits the
# per-sheet wide-integer block index. Rejections are disclosed before
# proportional state or candidate work starts.
_DENSE_BLOCK_MAX_ROWS = int(
    os.environ.get("PAPERCONAN_DENSE_BLOCK_MAX_ROWS", "100000")
)
_DENSE_BLOCK_CELL_WORK_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_DENSE_BLOCK_CELL_WORK_LIMIT",
        "10000000",
    )
)
_DENSE_BLOCK_STATE_CELL_LIMIT = int(
    os.environ.get(
        "PAPERCONAN_DENSE_BLOCK_STATE_CELL_LIMIT",
        "2000000",
    )
)
# Output cap: each finding embeds a table-snippet as evidence, so a paper with thousands of
# findings balloons scan.json to many GB. Stop collecting blocks once this many have findings.
_MAX_REPORT_BLOCKS = int(os.environ.get("PAPERCONAN_MAX_REPORT_BLOCKS", "2000"))
# Per-finding evidence cap: each finding embeds selected rows and columns as evidence.
# On a dense matrix a single block can be hundreds of rows × cols, and that copy is duplicated
# across thousands of findings — ballooning the scan dict / scan.json to many GB and OOMing the
# worker. Bound each evidence snippet to this many rows × cols unless preserving every
# highlighted cell requires more. Small blocks are emitted whole and stay byte-identical.
_MAX_EV_ROWS = int(os.environ.get("PAPERCONAN_MAX_EVIDENCE_ROWS", "50"))
_MAX_EV_COLS = int(os.environ.get("PAPERCONAN_MAX_EVIDENCE_COLS", "30"))
# Per-block finding cap: the pairwise detectors are O(col²), so a single dense, highly
# correlated block (a correlation matrix, an expression panel with many proportional columns)
# can emit thousands of findings. Each carries its own embedded evidence snippet, so the count —
# not just the per-snippet size — is what balloons scan.json / report.html past 1 GB (GH #15).
# Keep at most this many findings per block, retaining the highest-severity ones, and record how
# many were dropped in the block's `findings_omitted` field (never a silent truncation). 0 disables.
_MAX_FINDINGS_PER_BLOCK = int(os.environ.get("PAPERCONAN_MAX_FINDINGS_PER_BLOCK", "150"))
# Directory-wide cap across block and cross-sheet finding families. When trimming is needed,
# retain higher-severity findings first and preserve stable emission order within ties.
_MAX_TOTAL_FINDINGS = int(os.environ.get("PAPERCONAN_MAX_TOTAL_FINDINGS", "5000"))
# Directory-wide recurrence budgets. Window work and retained unique vectors
# are independent controls so a large corpus cannot trade bounded CPU for
# unbounded Python state.
_RECURRING_ROW_VECTOR_BUDGET = int(
    os.environ.get("PAPERCONAN_RECURRING_ROW_VECTOR_BUDGET", "3000000")
)
_RECURRING_ROW_VECTOR_UNIQUE_BUDGET = int(
    os.environ.get(
        "PAPERCONAN_RECURRING_ROW_VECTOR_UNIQUE_BUDGET",
        "100000",
    )
)
_RECURRING_ROW_VECTOR_FINALIZATION_CANDIDATE_BUDGET = int(
    os.environ.get(
        "PAPERCONAN_RECURRING_ROW_VECTOR_FINALIZATION_CANDIDATE_BUDGET",
        "10000",
    )
)
_RECURRING_ROW_VECTOR_FINALIZATION_PAIR_BUDGET = int(
    os.environ.get(
        "PAPERCONAN_RECURRING_ROW_VECTOR_FINALIZATION_PAIR_BUDGET",
        "200000",
    )
)
_RECURRING_ROW_VECTOR_FINALIZATION_CELL_BUDGET = int(
    os.environ.get(
        "PAPERCONAN_RECURRING_ROW_VECTOR_FINALIZATION_CELL_BUDGET",
        "1000000",
    )
)
_RECURRING_ROW_VECTOR_MAX_FINDINGS = 20
_FRACTION_REUSE_PAIR_BUDGET = int(
    os.environ.get("PAPERCONAN_FRACTION_REUSE_PAIR_BUDGET", "10000")
)
_FRACTION_REUSE_CELL_BUDGET = int(
    os.environ.get(
        "PAPERCONAN_FRACTION_REUSE_CELL_BUDGET",
        "1000000",
    )
)

# Severity rank for deterministic, highest-first truncation when a block is over budget.
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class ScanBudgetState:
    coverage: ScanCoverage
    recurring_index: RecurringRowIndex
    profile: str
    evidence: bool
    cross_sheet_summary_budget: CrossSheetSummaryBudget | None = None
    cross_sheet_work_budget: CrossSheetWorkBudget | None = None
    findings_kept: int = 0
    findings_omitted: int = 0
    findings_omitted_is_lower_bound: bool = False
    report_blocks_kept: int = 0
    include_runtime: bool = True
    defer_evidence: bool = False


@dataclass
class FileScanResult:
    report_blocks: list[dict]
    digit_reports: list[dict]
    decimal_reports: list[dict]
    summaries: list[CrossSheetSummary]
    within_sheet_findings: list[dict]
    stats: dict
    errors: list[dict]


@dataclass
class _SheetScanResult:
    report_blocks: list[dict]
    digit_reports: list[dict]
    decimal_reports: list[dict]
    summaries: list[CrossSheetSummary]
    within_sheet_findings: list[dict]
    stats: dict


def _empty_file_scan_result(file_stat, errors):
    return FileScanResult(
        report_blocks=[],
        digit_reports=[],
        decimal_reports=[],
        summaries=[],
        within_sheet_findings=[],
        stats={"files": [file_stat], "sheets": []},
        errors=errors,
    )


def _elapsed_ms(start) -> float | None:
    if start is None:
        return None
    return round((time.perf_counter() - start) * 1000, 3)


def _add_elapsed_ms(stat, start):
    if stat is None or start is None:
        return
    elapsed = _elapsed_ms(start)
    current = stat.get("elapsed_ms")
    stat["elapsed_ms"] = round((current or 0.0) + elapsed, 3)


def _cap_block_findings(groups, cap):
    """Trim a block's findings to at most `cap`, keeping the highest-severity ones.

    `groups` maps a BLOCK_FINDING_GROUPS key to its list of findings; each list is
    trimmed IN PLACE. Selection is by severity (high > medium > low); ties keep the
    original detector/emission order, so output stays deterministic. Returns the number
    of findings dropped. `cap is None` means unlimited (no trimming); `cap == 0` drops all."""
    if cap is None:
        return 0
    cap = max(0, cap)
    total = sum(len(v) for v in groups.values())
    if total <= cap:
        return 0
    flat = [(name, idx, f)
            for name, lst in groups.items()
            for idx, f in enumerate(lst)]
    # Stable sort by severity keeps original order within a severity band.
    flat.sort(key=lambda t: _SEVERITY_RANK.get((t[2].get("severity") or "low").lower(), 3))
    keep = {(name, idx) for name, idx, _ in flat[:cap]}
    omitted = 0
    for name, lst in groups.items():
        kept = [f for i, f in enumerate(lst) if (name, i) in keep]
        omitted += len(lst) - len(kept)
        lst[:] = kept
    return omitted


def _apply_global_finding_budget(report_blocks, cross_sheet_findings, cap):
    if cap <= 0:
        return 0

    entries = []
    for block in report_blocks:
        for group_name in BLOCK_FINDING_GROUPS:
            for finding_index, finding in enumerate(
                block.get(group_name) or []
            ):
                entries.append(
                    ("block", block, group_name, finding_index, finding)
                )
    for finding_index, finding in enumerate(cross_sheet_findings):
        entries.append(
            ("cross_sheet", None, None, finding_index, finding)
        )

    if len(entries) <= cap:
        return 0

    ranked = sorted(
        range(len(entries)),
        key=lambda index: _SEVERITY_RANK.get(
            str(entries[index][4].get("severity") or "low").lower(),
            3,
        ),
    )
    kept_entry_indices = set(ranked[:cap])

    entry_index = 0
    for block in report_blocks:
        omitted_from_block = 0
        for group_name in BLOCK_FINDING_GROUPS:
            group = block.get(group_name) or []
            kept_group = []
            for finding in group:
                if entry_index in kept_entry_indices:
                    kept_group.append(finding)
                else:
                    omitted_from_block += 1
                entry_index += 1
            group[:] = kept_group
        if omitted_from_block:
            block["findings_omitted"] = (
                int(block.get("findings_omitted") or 0)
                + omitted_from_block
            )

    kept_cross_sheet = []
    for finding in cross_sheet_findings:
        if entry_index in kept_entry_indices:
            kept_cross_sheet.append(finding)
        entry_index += 1
    cross_sheet_findings[:] = kept_cross_sheet
    return len(entries) - cap


def _attach_deferred_evidence(
    report_blocks, coverage, runtime_stats=None
):
    blocks_by_path = {}
    for block in report_blocks:
        path = block.get("_evidence_path")
        if path is not None:
            blocks_by_path.setdefault(path, []).append(block)

    try:
        for path, path_blocks in blocks_by_path.items():
            retained_blocks = [
                block for block in path_blocks
                if any(block.get(group) for group in BLOCK_FINDING_GROUPS)
            ]
            if not retained_blocks:
                continue
            path_runtime = (
                runtime_stats.get(path)
                if runtime_stats is not None
                else None
            )
            file_start = (
                time.perf_counter()
                if path_runtime is not None
                else None
            )
            try:
                blocks_by_sheet = {}
                for block in retained_blocks:
                    blocks_by_sheet.setdefault(
                        block["sheet"], []
                    ).append(block)

                def attach_sheet_blocks(sheet_name, sheet):
                    for block in blocks_by_sheet.get(sheet_name, ()):
                        sheet_stat = (
                            path_runtime["sheets"].get(sheet_name)
                            if path_runtime is not None
                            else None
                        )
                        sheet_start = (
                            time.perf_counter()
                            if sheet_stat is not None
                            else None
                        )
                        try:
                            r0, r1, c0, c1 = (
                                block["_evidence_context"]
                            )
                            header = block["block"]["header"]
                            evidence_truncated = False
                            for group_name in BLOCK_FINDING_GROUPS:
                                evidence_truncated = _attach_evidence(
                                    block.get(group_name) or [],
                                    sheet,
                                    r0,
                                    r1,
                                    c0,
                                    c1,
                                    header,
                                ) or evidence_truncated
                            if evidence_truncated:
                                coverage.add_limitation(
                                    "block",
                                    "evidence_limit",
                                    file=block["file"],
                                    sheet=sheet_name,
                                    rows=block["block"]["rows"],
                                    cols=block["block"]["cols"],
                                    max_rows=_MAX_EV_ROWS,
                                    max_cols=_MAX_EV_COLS,
                                )
                        finally:
                            _add_elapsed_ms(sheet_stat, sheet_start)

                ext = os.path.splitext(path)[1].lower()
                if ext in {".pdf", ".docx"}:
                    entry_iterator = iter(_iter_extracted_sheets(path))
                    while True:
                        try:
                            entry = next(entry_iterator)
                        except StopIteration:
                            break
                        sheet_name, sheet, _limitations = entry
                        if (
                            sheet is not None
                            and sheet_name in blocks_by_sheet
                        ):
                            attach_sheet_blocks(sheet_name, sheet)
                        del sheet
                        del _limitations
                        del sheet_name
                        del entry
                    del entry_iterator
                else:
                    load_result = load_table_result(path)
                    sheets = load_result.sheets
                    try:
                        for sheet_name, sheet_blocks in (
                            blocks_by_sheet.items()
                        ):
                            sheet = sheets.get(sheet_name)
                            if sheet is None:
                                continue
                            if not isinstance(sheet, Sheet):
                                sheet = Sheet.from_rows(
                                    sheet,
                                    max_cells=_MAX_CELLS,
                                    max_sparse_cells=(
                                        _MAX_SPARSE_CELLS
                                    ),
                                    max_sparse_bytes=(
                                        _MAX_SPARSE_BYTES
                                    ),
                                )
                            attach_sheet_blocks(sheet_name, sheet)
                            del sheet
                    finally:
                        del sheets
                        del load_result
            finally:
                _add_elapsed_ms(
                    (
                        path_runtime["file"]
                        if path_runtime is not None
                        else None
                    ),
                    file_start,
                )
    finally:
        for block in report_blocks:
            block.pop("_evidence_path", None)
            block.pop("_evidence_context", None)


_WIDE_INTEGER_BLOCK_DETECTORS = [
    "relations",
    "equal_pairs",
    "row_pairs",
    "arithmetic_progression",
    "within_column",
    "dispersed_repeats",
    "identical_after_rounding",
    "grim_grimmer",
]


def _wide_integer_index_state_required(
    block_count, coordinate_count, *, ordered
):
    block_count = max(0, int(block_count))
    coordinate_count = max(0, int(coordinate_count))
    coordinate_units = (
        6 * coordinate_count
        if ordered
        else 10 * coordinate_count
    )
    return 12 * block_count + coordinate_units + 2


def _wide_integer_counts_by_block(
    sheet,
    blocks,
    *,
    state_limit=None,
    with_coverage=False,
    _state_tracker=None,
):
    coordinates = sheet._wide_ints
    block_count = len(blocks)
    coordinate_count = len(coordinates)
    limit = max(0, int(
        _DENSE_BLOCK_STATE_CELL_LIMIT
        if state_limit is None
        else state_limit
    ))
    ordered_hint = getattr(sheet, "_wide_ints_ordered", None)
    ordered = (
        bool(ordered_hint)
        if ordered_hint is not None
        else False
    )
    coordinate_visits = 0
    state_required = _wide_integer_index_state_required(
        block_count,
        coordinate_count,
        ordered=ordered,
    )
    if state_required > limit:
        metadata = {
            "coordinates_total": coordinate_count,
            "coordinate_visits": coordinate_visits,
            "event_cells": 0,
            "python_event_records": 0,
            "block_count_cells": 0,
            "column_index_cells": 0,
            "fenwick_cells": 0,
            "coordinate_copy_cells": 0,
            "state_unit_limit": limit,
            "state_units_required": state_required,
            "peak_state_units": 0,
            "state_exhausted": True,
        }
        return (None, metadata) if with_coverage else None

    tracker = _state_tracker or _DenseStateTracker()
    try:
        block_counts = np.zeros(block_count, dtype=np.int64)
        tracker.retain("block_counts", block_counts)

        coordinate_array = None
        if ordered:
            def coordinate_columns():
                nonlocal coordinate_visits
                for _row, col in coordinates:
                    coordinate_visits += 1
                    yield col

            column_values = np.fromiter(
                coordinate_columns(),
                dtype=np.int64,
                count=coordinate_count,
            )
        else:
            coordinate_array = np.empty(
                (coordinate_count, 2), dtype=np.int64
            )
            for index, coordinate in enumerate(coordinates):
                coordinate_visits += 1
                coordinate_array[index] = coordinate
            tracker.retain(
                "coordinate_copy", coordinate_array
            )
            column_values = coordinate_array[:, 1].copy()
        tracker.retain("coordinate_columns", column_values)
        tracker.retain_units(
            "column_unique_workspace", 4 * coordinate_count
        )
        columns = np.unique(column_values)
        tracker.retain("columns", columns)
        tracker.release("column_unique_workspace")
        del column_values
        tracker.release("coordinate_columns")

        tree = np.zeros(len(columns) + 1, dtype=np.int64)
        tracker.retain("fenwick", tree)
        events = np.empty((2 * block_count, 2), dtype=np.int64)
        tracker.retain("events", events)
        for block_index, (r0, r1, _c0, _c1) in enumerate(
            blocks
        ):
            events[2 * block_index] = (r0, -block_index - 1)
            events[2 * block_index + 1] = (
                r1,
                block_index + 1,
            )
        tracker.retain_units(
            "event_sort_workspace", 4 * block_count
        )
        event_order = np.lexsort((events[:, 1], events[:, 0]))
        tracker.retain("event_order", event_order)
        tracker.release("event_sort_workspace")

        coordinate_copy_cells = (
            0
            if coordinate_array is None
            else int(coordinate_array.size)
        )
        if ordered:
            coordinate_iterator = iter(coordinates)
        else:
            tracker.retain_units(
                "coordinate_sort_workspace",
                4 * coordinate_count,
            )
            coordinate_order = np.lexsort((
                coordinate_array[:, 1],
                coordinate_array[:, 0],
            ))
            tracker.retain(
                "coordinate_order", coordinate_order
            )
            tracker.release("coordinate_sort_workspace")
            coordinate_iterator = (
                (
                    int(coordinate_array[position, 0]),
                    int(coordinate_array[position, 1]),
                )
                for position in coordinate_order
            )
        current = next(coordinate_iterator, None)

        def add(column):
            index = bisect_left(columns, column) + 1
            while index < len(tree):
                tree[index] += 1
                index += index & -index

        def prefix(index):
            total = 0
            while index > 0:
                total += int(tree[index])
                index -= index & -index
            return total

        for event_position in event_order:
            row = int(events[event_position, 0])
            token = int(events[event_position, 1])
            if token < 0:
                block_index = -token - 1
                sign = -1
            else:
                block_index = token - 1
                sign = 1
            _r0, _r1, c0, c1 = blocks[block_index]
            while current is not None and current[0] < row:
                coordinate_visits += 1
                add(current[1])
                current = next(coordinate_iterator, None)
            left = bisect_left(columns, c0)
            right = bisect_left(columns, c1)
            block_counts[block_index] += sign * (
                prefix(right) - prefix(left)
            )

        metadata = {
            "coordinates_total": coordinate_count,
            "coordinate_visits": coordinate_visits,
            "event_cells": int(events.size),
            "python_event_records": 0,
            "block_count_cells": int(block_counts.size),
            "column_index_cells": int(columns.size),
            "fenwick_cells": int(tree.size),
            "coordinate_copy_cells": coordinate_copy_cells,
            "state_unit_limit": limit,
            "state_units_required": state_required,
            "peak_state_units": tracker.peak_units,
            "state_exhausted": False,
        }
        return (
            (block_counts, metadata)
            if with_coverage
            else block_counts
        )
    finally:
        tracker.release_all()


def _analyze_numeric_blocks(
    sheet, *, file_name, sheet_name, blocks, state
):
    def dense_resources(family):
        return _DenseFamilyResources(
            family=family,
            max_rows=_DENSE_BLOCK_MAX_ROWS,
            work_limit=_DENSE_BLOCK_CELL_WORK_LIMIT,
            state_limit=_DENSE_BLOCK_STATE_CELL_LIMIT,
        )

    report_blocks = []
    wide_integer_counts = None
    wide_integer_index_exhausted = False
    if sheet._wide_ints:
        (
            wide_integer_counts,
            wide_integer_index_meta,
        ) = _wide_integer_counts_by_block(
            sheet,
            blocks,
            state_limit=max(
                0, _DENSE_BLOCK_STATE_CELL_LIMIT
            ),
            with_coverage=True,
        )
        wide_integer_index_exhausted = (
            wide_integer_counts is None
        )
        if wide_integer_index_exhausted:
            state.coverage.add_limitation(
                "sheet",
                "wide_integer_block_index_limit",
                file=file_name,
                sheet=sheet_name,
                state_unit_limit=wide_integer_index_meta[
                    "state_unit_limit"
                ],
                state_units_required=wide_integer_index_meta[
                    "state_units_required"
                ],
                peak_state_units=wide_integer_index_meta[
                    "peak_state_units"
                ],
                blocks_total=len(blocks),
                detector_blocks_skipped=len(blocks),
                wide_integer_cells=len(sheet._wide_ints),
                affected_blocks_lower_bound=0,
                detectors=_WIDE_INTEGER_BLOCK_DETECTORS,
            )
            state.findings_omitted_is_lower_bound = True
    for block_index, (r0, r1, c0, c1) in enumerate(blocks):
        if state.report_blocks_kept >= _MAX_REPORT_BLOCKS:
            state.coverage.mark_blocks_skipped(
                len(blocks) - block_index,
                scope="sheet",
                reason="report_block_limit",
                file=file_name,
                sheet=sheet_name,
            )
            break
        state.coverage.mark_block_analyzed()
        header = header_for(sheet, r0, c0, c1)
        block_cap = (
            _MAX_FINDINGS_PER_BLOCK
            if _MAX_FINDINGS_PER_BLOCK > 0
            else None
        )
        collector = BoundedFindingCollector(
            BLOCK_FINDING_GROUPS,
            cap=block_cap,
            severity_rank=_SEVERITY_RANK,
        )
        wide_block = (
            _MAX_BLOCK_COLS and (c1 - c0) > _MAX_BLOCK_COLS
        )
        if wide_block:
            state.coverage.add_limitation(
                "block",
                "wide_block_detector_limit",
                file=file_name,
                sheet=sheet_name,
                rows=f"{r0 + 1}-{r1}",
                cols=f"{c0 + 1}-{c1}",
                detectors=["relations", "equal_pairs", "row_pairs"],
                max_cols=_MAX_BLOCK_COLS,
            )
        wide_integer_count = (
            int(wide_integer_counts[block_index])
            if wide_integer_counts is not None
            else 0
        )
        wide_integer_limited = (
            wide_integer_index_exhausted
            or wide_integer_count > 0
        )
        if wide_integer_count:
            state.coverage.add_limitation(
                "block",
                "wide_integer_detector_limit",
                file=file_name,
                sheet=sheet_name,
                rows=f"{r0 + 1}-{r1}",
                cols=f"{c0 + 1}-{c1}",
                affected_cells=wide_integer_count,
                detectors=_WIDE_INTEGER_BLOCK_DETECTORS,
            )
            state.findings_omitted_is_lower_bound = True
        dense_sessions = {
            family: dense_resources(family)
            for family in (
                "relations",
                "equal_pairs",
                "arithmetic_progression",
                "within_column",
                "dispersed_repeats",
                "identical_after_rounding",
            )
        }
        row_pair_dimension_limited = (
            not wide_block
            and (
                (r1 - r0) > _ROW_PAIR_MAX_ROWS
                or (c1 - c0) > _ROW_PAIR_MAX_COLS
            )
        )
        if row_pair_dimension_limited:
            state.coverage.add_limitation(
                "block",
                "row_pair_dimension_limit",
                file=file_name,
                sheet=sheet_name,
                rows=r1 - r0,
                cols=c1 - c0,
                max_rows=_ROW_PAIR_MAX_ROWS,
                max_cols=_ROW_PAIR_MAX_COLS,
            )
        rel = (
            detect_relations(
                sheet,
                r0,
                r1,
                c0,
                c1,
                header,
                _resources=dense_sessions["relations"],
                _finding_sink=collector,
            )
            if (
                not wide_block
                and not wide_integer_limited
            )
            else []
        )
        ap = (
            detect_arithmetic_progression(
                sheet,
                r0,
                r1,
                c0,
                c1,
                header,
                _resources=dense_sessions[
                    "arithmetic_progression"
                ],
                _finding_sink=collector,
            )
            if (
                not wide_integer_limited
            )
            else []
        )
        eq = (
            detect_equal_pairs(
                sheet,
                r0,
                r1,
                c0,
                c1,
                header,
                _resources=dense_sessions["equal_pairs"],
                _finding_sink=collector,
            )
            if (
                not wide_block
                and not wide_integer_limited
            )
            else []
        )
        row_pair_meta = {"findings_omitted": 0}
        if (
            wide_block
            or wide_integer_limited
            or row_pair_dimension_limited
        ):
            rp = []
        else:
            row_pair_result = detect_row_pair_digit_coupling(
                sheet,
                r0,
                r1,
                c0,
                c1,
                header,
                with_coverage=True,
                _finding_sink=collector,
            )
            if isinstance(row_pair_result, tuple):
                rp, row_pair_meta = row_pair_result
            else:
                rp = row_pair_result
        row_pair_omitted = int(
            row_pair_meta["findings_omitted"]
        )
        if row_pair_omitted > 0:
            state.coverage.add_limitation(
                "block",
                "row_pair_finding_limit",
                file=file_name,
                sheet=sheet_name,
                rows=f"{r0 + 1}-{r1}",
                cols=f"{c0 + 1}-{c1}",
                limit=_ROW_PAIR_MAX_FINDINGS_PER_BLOCK,
                omitted_findings=row_pair_omitted,
            )
            state.findings_omitted += row_pair_omitted
        wc = (
            detect_within_column_patterns(
                sheet,
                r0,
                r1,
                c0,
                c1,
                header,
                _resources=dense_sessions["within_column"],
                _finding_sink=collector,
            )
            if (
                not wide_integer_limited
            )
            else []
        )
        if not wide_integer_limited:
            wc += detect_dispersed_repeats(
                sheet,
                r0,
                r1,
                c0,
                c1,
                header,
                _resources=dense_sessions["dispersed_repeats"],
                _finding_sink=collector,
            )
        iar = (
            detect_identical_after_rounding(
                sheet,
                r0,
                r1,
                c0,
                c1,
                header,
                _resources=dense_sessions[
                    "identical_after_rounding"
                ],
                _finding_sink=collector,
            )
            if (
                not wide_integer_limited
            )
            else []
        )
        dense_results = []
        for session in dense_sessions.values():
            result = session.result()
            if result.limits_reached:
                dense_results.append(result)
        if dense_results and not wide_integer_limited:
            state.coverage.add_limitation(
                "block",
                "dense_block_detector_limit",
                file=file_name,
                sheet=sheet_name,
                rows=f"{r0 + 1}-{r1}",
                cols=f"{c0 + 1}-{c1}",
                max_rows=max(0, _DENSE_BLOCK_MAX_ROWS),
                cell_work_limit=max(
                    0, _DENSE_BLOCK_CELL_WORK_LIMIT
                ),
                state_cell_limit=max(
                    0, _DENSE_BLOCK_STATE_CELL_LIMIT
                ),
                detectors=[
                    {
                        "family": result.family,
                        "candidates_total": (
                            result.candidates_total
                        ),
                        "candidates_examined": (
                            result.candidates_examined
                        ),
                        "candidates_skipped": (
                            result.candidates_skipped
                        ),
                        "work_required": result.work_required,
                        "work_examined": result.work_examined,
                        "work_skipped": result.work_skipped,
                        "work_skipped_lower_bound": (
                            result.work_skipped_lower_bound
                        ),
                        "state_required": result.state_required,
                        "state_required_lower_bound": (
                            result.state_required_lower_bound
                        ),
                        "peak_state_units": (
                            result.peak_state_units
                        ),
                        "limits_reached": list(
                            result.limits_reached
                        ),
                    }
                    for result in dense_results
                ],
            )
            state.findings_omitted_is_lower_bound = True
        gg = (
            detect_grim_grimmer(
                sheet,
                r0,
                r1,
                c0,
                c1,
                header,
                _finding_sink=collector,
            )
            if not wide_integer_limited
            else []
        )
        groups = collector.materialize()
        block_cap_omitted = collector.omitted
        if not (
            any(groups.values())
            or row_pair_omitted
            or block_cap_omitted
        ):
            continue

        sheet_context = " ".join([
            file_name,
            sheet_name,
            *[str(value) for value in header],
        ])
        state.findings_omitted += block_cap_omitted
        if block_cap_omitted:
            state.coverage.add_limitation(
                "block",
                "finding_limit",
                file=file_name,
                sheet=sheet_name,
                rows=f"{r0 + 1}-{r1}",
                cols=f"{c0 + 1}-{c1}",
                omitted_findings=block_cap_omitted,
                limit=block_cap,
            )
        state.findings_kept += sum(
            len(group) for group in groups.values()
        )
        evidence_truncated = False
        for group in groups.values():
            if state.evidence and not state.defer_evidence:
                evidence_truncated = _attach_evidence(
                    group, sheet, r0, r1, c0, c1, header
                ) or evidence_truncated
            _attach_benign(group)
            apply_profile_to_findings(
                group,
                state.profile,
                sheet_context=sheet_context,
            )
        if evidence_truncated:
            state.coverage.add_limitation(
                "block",
                "evidence_limit",
                file=file_name,
                sheet=sheet_name,
                rows=f"{r0 + 1}-{r1}",
                cols=f"{c0 + 1}-{c1}",
                max_rows=_MAX_EV_ROWS,
                max_cols=_MAX_EV_COLS,
            )
        report_block = {
            "file": file_name,
            "sheet": sheet_name,
            "block": {
                "rows": f"{r0 + 1}-{r1}",
                "cols": f"{c0 + 1}-{c1}",
                "header": header,
            },
            "relations": groups["relations"],
            "progressions": groups["progressions"],
            "equal_pairs": groups["equal_pairs"],
            "row_pairs": groups["row_pairs"],
            "within_col": groups["within_col"],
            "identical_after_rounding": groups[
                "identical_after_rounding"
            ],
            "grim": groups["grim"],
            "findings_omitted": (
                row_pair_omitted + block_cap_omitted
            ),
        }
        if state.evidence and state.defer_evidence:
            report_block["_evidence_context"] = (r0, r1, c0, c1)
        report_blocks.append(report_block)
        state.report_blocks_kept += 1
    return report_blocks


def _process_loaded_sheet(
    sheet, *, file_name, sheet_name, sheet_start, state
):
    blocks = find_numeric_blocks(sheet)
    report_blocks = _analyze_numeric_blocks(
        sheet,
        file_name=file_name,
        sheet_name=sheet_name,
        blocks=blocks,
        state=state,
    )

    (
        within_sheet_findings,
        fraction_reuse_limitations,
    ) = detect_within_sheet_fraction_reuse(
        {(file_name, sheet_name): sheet},
        profile=state.profile,
        pair_budget=_FRACTION_REUSE_PAIR_BUDGET,
        cell_budget=_FRACTION_REUSE_CELL_BUDGET,
        with_coverage=True,
    )
    for limitation in fraction_reuse_limitations:
        state.coverage.add_limitation(
            "sheet",
            "fraction_reuse_work_limit",
            **limitation,
        )
    if state.cross_sheet_work_budget is not None:
        retained_within_sheet_findings = []
        for finding in within_sheet_findings:
            if state.cross_sheet_work_budget.retain_finding():
                retained_within_sheet_findings.append(finding)
        within_sheet_findings = retained_within_sheet_findings

    label = f"{file_name}::{sheet_name}"
    digit_reports = []
    digit_report = detect_last_digit(
        sheet.iter_numeric_values(), label=label
    )
    if digit_report:
        digit_reports.append(digit_report)
    decimal_reports = []
    decimal_report = detect_repeated_decimals(
        sheet.iter_numeric_values(), label=label
    )
    if decimal_report:
        decimal_reports.append(decimal_report)

    recurring_meta = state.recurring_index.add_sheet(
        file_name,
        sheet_name,
        sheet,
        blocks=blocks,
        figure_id=figure_key(sheet_name),
    )
    recurring_windows_skipped = recurring_meta.get(
        "windows_skipped", 0
    )
    recurring_windows_lower_bound = bool(
        recurring_meta.get(
            "windows_skipped_is_lower_bound", False
        )
    )
    recurring_budget_exhausted = bool(
        recurring_meta.get(
            "budget_exhausted",
            recurring_windows_skipped > 0,
        )
    )
    if recurring_budget_exhausted:
        state.coverage.add_limitation(
            "sheet",
            "recurring_row_vector_budget",
            file=file_name,
            sheet=sheet_name,
            windows_skipped=recurring_windows_skipped,
            **(
                {"windows_skipped_is_lower_bound": True}
                if recurring_windows_lower_bound
                else {}
            ),
            limit=state.recurring_index.initial_budget,
        )
        if recurring_windows_lower_bound:
            state.findings_omitted_is_lower_bound = True

    summary, summary_limitations = build_cross_sheet_summary(
        file_name,
        sheet_name,
        sheet,
        blocks=blocks,
        budget=state.cross_sheet_summary_budget,
        column_distinct_limit=(
            _COLUMN_FINGERPRINT_DISTINCT_LIMIT
        ),
        column_limit=_COLUMN_FINGERPRINT_MAX_COLUMNS,
    )
    for limitation in summary_limitations:
        reason = (
            "collision_grid_cell_limit"
            if limitation.reason == "collision_cell_limit"
            else limitation.reason
        )
        state.coverage.add_limitation(
            limitation.scope,
            reason,
            file=file_name,
            sheet=limitation.sheet,
            **limitation.details,
        )

    stats = {
        "file": file_name,
        "sheet": sheet_name,
        "n_rows": sheet.nrows,
        "n_cols": sheet.ncols,
        "numeric_cells": sum(1 for _ in sheet.iter_numeric_values()),
        "n_blocks": len(blocks),
        "elapsed_ms": _elapsed_ms(sheet_start),
    }
    return _SheetScanResult(
        report_blocks=report_blocks,
        digit_reports=digit_reports,
        decimal_reports=decimal_reports,
        summaries=[] if summary is None else [summary],
        within_sheet_findings=within_sheet_findings,
        stats=stats,
    )


def _process_file(path, *, input_dir, state) -> FileScanResult:
    """Process one source file.

    `input_dir` is reserved for stable file-relative orchestration and
    provenance without changing this plan-mandated interface.
    """
    report_blocks = []
    digit_reports = []
    decimal_reports = []
    summaries = []
    within_sheet_findings = []
    errors = []
    sheet_stats = []
    file_start = (
        time.perf_counter() if state.include_runtime else None
    )
    file_name = os.path.basename(path)
    file_stat = {
        "file": file_name,
        "path": os.path.relpath(path, start=input_dir),
    }
    structural_rejection_reasons = {
        "cell_limit",
        "sparse_cell_limit",
        "sparse_payload_limit",
    }

    try:
        fsize = os.path.getsize(path)
    except OSError:
        fsize = 0
    if fsize > _MAX_FILE_BYTES:
        msg = (f"oversized: {fsize / 1048576:.1f}MB exceeds {_MAX_FILE_MB:.0f}MB cap "
               f"(set PAPERCONAN_MAX_FILE_MB to raise) — skipped to bound memory")
        print(f"  skipping {file_name}: {msg}", file=sys.stderr)
        errors.append({"file": file_name, "error": msg})
        state.coverage.mark_file_failed(
            file_name, "file_size_limit", max_bytes=_MAX_FILE_BYTES
        )
        file_stat["error"] = msg
        file_stat["oversized"] = True
        file_stat["elapsed_ms"] = _elapsed_ms(file_start)
        return _empty_file_scan_result(file_stat, errors)

    def process_sheet_entry(
        sheet_name, loaded_sheet, input_limitations
    ):
        sheet_start = (
            time.perf_counter() if state.include_runtime else None
        )
        rejected_limitation = None
        for limitation in input_limitations:
            details = limitation.to_dict()
            scope = details.pop("scope")
            reason = details.pop("reason")
            details.pop("file", None)
            limitation_sheet = details.pop("sheet", None)
            if (
                loaded_sheet is None
                and scope == "sheet"
                and limitation_sheet == sheet_name
                and reason in structural_rejection_reasons
                and rejected_limitation is None
            ):
                rejected_limitation = (reason, details)
                continue
            state.coverage.add_limitation(
                scope,
                reason,
                file=file_name,
                sheet=limitation_sheet,
                **details,
            )
        if loaded_sheet is None:
            limitation_reason, limitation_details = (
                rejected_limitation
                or ("cell_limit", {"max_cells": _MAX_CELLS})
            )
            if limitation_reason == "cell_limit":
                msg = (
                    f"oversized sheet exceeds {_MAX_CELLS} dense cells "
                    "(set PAPERCONAN_MAX_CELLS to raise) - "
                    "skipped to bound memory"
                )
            else:
                msg = (
                    "sheet exceeds retained sparse-cell budget "
                    f"({limitation_reason}) - skipped to bound memory"
                )
            errors.append({
                "file": file_name,
                "sheet": sheet_name,
                "error": msg,
            })
            state.coverage.mark_sheet_skipped(
                file_name,
                sheet_name,
                limitation_reason,
                **limitation_details,
            )
            skipped_stat = {
                "file": file_name,
                "sheet": sheet_name,
                "skipped": True,
                "elapsed_ms": _elapsed_ms(sheet_start),
            }
            if limitation_reason == "cell_limit":
                skipped_stat["oversized"] = True
            sheet_stats.append(skipped_stat)
            return

        sheet = (
            loaded_sheet
            if isinstance(loaded_sheet, Sheet)
            else Sheet.from_rows(loaded_sheet)
        )
        blocks_before = state.coverage.blocks_analyzed
        sheet_result = _process_loaded_sheet(
            sheet,
            file_name=file_name,
            sheet_name=sheet_name,
            sheet_start=sheet_start,
            state=state,
        )
        if state.evidence and state.defer_evidence:
            for report_block in sheet_result.report_blocks:
                report_block["_evidence_path"] = path
        blocks_analyzed = (
            state.coverage.blocks_analyzed - blocks_before
        )
        if blocks_analyzed:
            state.coverage.mark_sheet_succeeded()
        elif sheet_result.stats["numeric_cells"] == 0:
            state.coverage.mark_sheet_skipped(
                file_name,
                sheet_name,
                "no_numeric_data",
            )
        elif sheet_result.stats["n_blocks"] == 0:
            state.coverage.mark_sheet_skipped(
                file_name,
                sheet_name,
                "no_qualifying_numeric_block",
            )
        else:
            state.coverage.mark_sheet_unanalyzed()
        report_blocks.extend(sheet_result.report_blocks)
        digit_reports.extend(sheet_result.digit_reports)
        decimal_reports.extend(sheet_result.decimal_reports)
        summaries.extend(sheet_result.summaries)
        within_sheet_findings.extend(
            sheet_result.within_sheet_findings
        )
        sheet_stats.append(sheet_result.stats)
        del sheet_result
        del sheet

    ext = os.path.splitext(path)[1].lower()
    extracted_input = ext in {".pdf", ".docx"}
    try:
        if extracted_input:
            entry_iterator = iter(_iter_extracted_sheets(path))
            sheet_count = 0
            while True:
                try:
                    entry = next(entry_iterator)
                except StopIteration:
                    break
                sheet_name, loaded_sheet, input_limitations = entry
                sheet_count += 1
                process_sheet_entry(
                    sheet_name,
                    loaded_sheet,
                    input_limitations,
                )
                del loaded_sheet
                del input_limitations
                del sheet_name
                del entry
            file_stat["n_sheets"] = sheet_count
            del entry_iterator
        else:
            load_result = load_table_result(path)
            sheets = load_result.sheets
            rejected_names = {
                name for name, value in sheets.items()
                if value is None
            }
            deferred = {name: [] for name in rejected_names}
            global_limitations = []
            for limitation in load_result.limitations:
                if (
                    limitation.scope == "sheet"
                    and limitation.sheet in rejected_names
                ):
                    deferred[limitation.sheet].append(limitation)
                else:
                    global_limitations.append(limitation)
            for limitation in global_limitations:
                details = limitation.to_dict()
                scope = details.pop("scope")
                reason = details.pop("reason")
                details.pop("file", None)
                state.coverage.add_limitation(
                    scope, reason, file=file_name, **details
                )
            file_stat["n_sheets"] = len(sheets)
            for sheet_name, loaded_sheet in sheets.items():
                process_sheet_entry(
                    sheet_name,
                    loaded_sheet,
                    deferred.get(sheet_name, ()),
                )
            del sheets
            del load_result
    except Exception as exc:
        if (
            isinstance(exc, ValueError)
            and str(exc).startswith(
                "details contains reserved key:"
            )
        ):
            raise
        print(f"  failed to read {file_name}: {exc}", file=sys.stderr)
        errors.append({"file": file_name, "error": str(exc)})
        state.coverage.mark_file_failed(file_name, "parse_error")
        file_stat["error"] = str(exc)
        file_stat["elapsed_ms"] = _elapsed_ms(file_start)
        if not sheet_stats:
            return _empty_file_scan_result(file_stat, errors)
    else:
        state.coverage.mark_file_succeeded()

    file_stat["elapsed_ms"] = _elapsed_ms(file_start)
    return FileScanResult(
        report_blocks=report_blocks,
        digit_reports=digit_reports,
        decimal_reports=decimal_reports,
        summaries=summaries,
        within_sheet_findings=within_sheet_findings,
        stats={"files": [file_stat], "sheets": sheet_stats},
        errors=errors,
    )


def scan_dir(in_dir, out_dir, *, write_md=False, write_html=True, paper=None,
             profile="review", write_json=True, evidence=True,
             diagnostic_on_empty=False, include_runtime=False):
    profile = normalize_profile(profile)
    if write_html:
        evidence = True
    files = discover_supported_inputs(in_dir)
    if not files and not diagnostic_on_empty:
        raise PaperconanInputError(
            f"no .xlsx / .xls / .xlsm / .xlsb / .csv / .tsv / .pdf / .docx files in {in_dir}\n"
            f"(paperconan reads .xlsx via openpyxl, legacy .xls / .xlsm / .xlsb via calamine, "
            f".csv / .tsv, and tables inside .pdf / .docx)"
        )

    coverage = ScanCoverage(files_discovered=len(files))
    state = ScanBudgetState(
        coverage=coverage,
        recurring_index=RecurringRowIndex(
            budget=_RECURRING_ROW_VECTOR_BUDGET,
            unique_budget=_RECURRING_ROW_VECTOR_UNIQUE_BUDGET,
            finalization_candidate_budget=(
                _RECURRING_ROW_VECTOR_FINALIZATION_CANDIDATE_BUDGET
            ),
            finalization_pair_budget=(
                _RECURRING_ROW_VECTOR_FINALIZATION_PAIR_BUDGET
            ),
            finalization_cell_budget=(
                _RECURRING_ROW_VECTOR_FINALIZATION_CELL_BUDGET
            ),
        ),
        profile=profile,
        evidence=evidence,
        cross_sheet_summary_budget=CrossSheetSummaryBudget(
            summary_limit=_CROSS_SHEET_SUMMARY_LIMIT,
            grid_cell_limit=_CROSS_SHEET_GRID_CELL_LIMIT,
            label_cell_limit=_CROSS_SHEET_LABEL_CELL_LIMIT,
            label_byte_limit=_CROSS_SHEET_LABEL_BYTE_LIMIT,
            column_fingerprint_limit=(
                _CROSS_SHEET_COLUMN_FINGERPRINT_LIMIT
            ),
        ),
        cross_sheet_work_budget=CrossSheetWorkBudget(
            pair_limit=_CROSS_SHEET_PAIR_BUDGET,
            value_limit=_CROSS_SHEET_VALUE_BUDGET,
            tail_match_limit=_CROSS_SHEET_TAIL_MATCH_BUDGET,
            finding_limit=_CROSS_SHEET_FINDING_BUDGET,
        ),
        include_runtime=include_runtime,
        defer_evidence=evidence,
    )
    report_blocks = []
    digit_reports = []
    decimal_reports = []
    summaries = []
    within_sheet_fraction_findings = []
    scan_errors = []
    scan_stats = {"files": [], "sheets": []}
    deferred_runtime_stats = {} if include_runtime else None
    scan_start = time.perf_counter() if include_runtime else None

    for path in files:
        result = _process_file(path, input_dir=in_dir, state=state)
        report_blocks.extend(result.report_blocks)
        digit_reports.extend(result.digit_reports)
        decimal_reports.extend(result.decimal_reports)
        summaries.extend(result.summaries)
        within_sheet_fraction_findings.extend(
            result.within_sheet_findings
        )
        scan_errors.extend(result.errors)
        scan_stats["files"].extend(result.stats["files"])
        scan_stats["sheets"].extend(result.stats["sheets"])
        if deferred_runtime_stats is not None:
            deferred_runtime_stats[path] = {
                "file": (
                    result.stats["files"][0]
                    if result.stats["files"]
                    else None
                ),
                "sheets": {
                    item["sheet"]: item
                    for item in result.stats["sheets"]
                },
            }
        del result

    _demote_dense_sheets(report_blocks, profile=profile)
    _demote_reused_progressions(report_blocks, profile=profile)

    summary_budget = state.cross_sheet_summary_budget
    if summary_budget is not None:
        summary_limitations = summary_budget.coverage_limitations()
        for limitation in summary_limitations:
            details = dict(limitation)
            reason = details.pop("reason")
            coverage.add_limitation("scan", reason, **details)
        if summary_limitations:
            state.findings_omitted_is_lower_bound = True

    grids = {
        (summary.file, summary.sheet): summary.grid
        for summary in summaries
    }
    label_contexts = {
        (summary.file, summary.sheet): summary.labels
        for summary in summaries
    }
    cross_sheet_findings = detect_collisions(
        grids,
        profile=profile,
        sheets=label_contexts,
        budget=state.cross_sheet_work_budget,
    )
    cross_sheet_findings += detect_cross_sheet_column_duplicates(
        summaries,
        profile=profile,
        budget=state.cross_sheet_work_budget,
    )
    cross_sheet_findings += within_sheet_fraction_findings
    recurring_findings, recurring_meta = state.recurring_index.findings(
        profile=profile,
        max_findings=_RECURRING_ROW_VECTOR_MAX_FINDINGS,
    )
    unique_meta = state.recurring_index.unique_budget_metadata()
    if unique_meta["budget_exhausted"]:
        coverage.add_limitation(
            "scan",
            "recurring_row_unique_vector_limit",
            limit=unique_meta["limit"],
            vectors_retained=unique_meta["vectors_retained"],
            skipped_new_vector_windows=unique_meta[
                "skipped_new_vector_windows"
            ],
            skipped_new_vectors_lower_bound=unique_meta[
                "skipped_new_vectors_lower_bound"
            ],
        )
    recurring_omitted = recurring_meta["findings_omitted"]
    finalization_limitation = recurring_meta.get(
        "finalization_limitation"
    )
    if finalization_limitation is not None:
        coverage.add_limitation(
            "scan",
            "recurring_row_vector_finalization_limit",
            **finalization_limitation,
        )
        state.findings_omitted_is_lower_bound = True
        state.findings_omitted += recurring_omitted
    elif recurring_omitted > 0:
        coverage.add_limitation(
            "scan",
            "recurring_row_vector_finding_limit",
            limit=_RECURRING_ROW_VECTOR_MAX_FINDINGS,
            omitted_findings=recurring_omitted,
        )
        state.findings_omitted += recurring_omitted
    for finding in recurring_findings:
        if (
            state.cross_sheet_work_budget is None
            or state.cross_sheet_work_budget.retain_finding()
        ):
            cross_sheet_findings.append(finding)

    work_budget = state.cross_sheet_work_budget
    if work_budget is not None:
        work_limitation = work_budget.limitation_metadata()
        if work_limitation["limits_reached"]:
            coverage.add_limitation(
                "scan",
                "cross_sheet_work_limit",
                **work_limitation,
            )
            state.findings_omitted += work_limitation[
                "findings_skipped"
            ]
            if any(
                dimension in work_limitation["limits_reached"]
                for dimension in ("pair", "value", "tail_match")
            ):
                state.findings_omitted_is_lower_bound = True
    _attach_benign(cross_sheet_findings)
    if _MAX_TOTAL_FINDINGS > 0:
        global_omitted = _apply_global_finding_budget(
            report_blocks,
            cross_sheet_findings,
            _MAX_TOTAL_FINDINGS,
        )
        if global_omitted:
            coverage.add_limitation(
                "scan",
                "global_finding_limit",
                limit=_MAX_TOTAL_FINDINGS,
                omitted_findings=global_omitted,
            )
            state.findings_omitted += global_omitted
    if evidence:
        _attach_deferred_evidence(
            report_blocks,
            coverage,
            runtime_stats=deferred_runtime_stats,
        )

    if digit_reports:
        adj, sig = benjamini_hochberg([d["p"] for d in digit_reports], alpha=0.05)
        for d, a, s in zip(digit_reports, adj, sig):
            d["p_adj"] = a
            d["fdr_significant"] = bool(s)

    coverage_output = coverage.to_dict()
    if any(
        item.get("reason") == "recurring_row_vector_budget"
        for item in coverage_output["limitations"]
    ):
        coverage_output["truncated"] = True

    out = dict(schema_version=2,
               scan_status=coverage.status,
               coverage=coverage_output,
               tool="paperconan",
               tool_version=_version(),
               scanned_at=(
                   datetime.datetime.now(
                       datetime.timezone.utc
                   ).isoformat(timespec="seconds")
                   if include_runtime
                   else None
               ),
               profile=profile,
               input_dir=in_dir,
               paper=_load_provenance(in_dir, paper),
               n_files=len(files),
               n_blocks_with_findings=len(report_blocks),
               findings_omitted=state.findings_omitted,
               scan_errors=scan_errors,
               scan_stats={**scan_stats,
                           "elapsed_ms": _elapsed_ms(scan_start)},
               relations_blocks=report_blocks,
               digit_distribution=digit_reports,
               decimal_endings=decimal_reports,
               cross_sheet_findings=cross_sheet_findings)
    if state.findings_omitted_is_lower_bound:
        out["findings_omitted_is_lower_bound"] = True
    os.makedirs(out_dir, exist_ok=True)
    if write_json:
        with open(os.path.join(out_dir, "scan.json"), "w") as fh:
            json.dump(out, fh, indent=2, default=str)
    if write_md:
        write_markdown_report(out, os.path.join(out_dir, "REPORT.md"))
    if write_html:
        from ._html import write_html_report
        write_html_report(out, os.path.join(out_dir, "report.html"))
    return out


def _markdown_status_value(value):
    if isinstance(value, dict):
        items = sorted(
            value.items(),
            key=lambda item: _markdown_status_sort_key(item[0]),
        )
        return "{" + ", ".join(
            f"{_markdown_status_value(key)}: {_markdown_status_value(item)}"
            for key, item in items
        ) + "}"
    if isinstance(value, (list, tuple)):
        left, right = ("[", "]") if isinstance(value, list) else ("(", ")")
        return left + ", ".join(
            _markdown_status_value(item) for item in value
        ) + right
    text = str(value)
    if text == "":
        return '""'
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _markdown_status_sort_key(value):
    return (
        _markdown_status_value(value),
        type(value).__name__,
        str(value),
    )


def _markdown_inline_code(value):
    text = _markdown_status_value(value)
    longest_run = 0
    current_run = 0
    for char in text:
        if char == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    fence = "`" * (longest_run + 1)
    has_non_space = any(char != " " for char in text)
    pad = (
        " "
        if has_non_space
        and (text.startswith(("`", " ")) or text.endswith(("`", " ")))
        else ""
    )
    return f"{fence}{pad}{text}{pad}{fence}"


def _markdown_scan_status(out):
    status = out.get("scan_status")
    coverage = out.get("coverage") or {}
    limitations = coverage.get("limitations") or []
    lines = ["## Scan status\n"]

    if status is None:
        lines.append(
            "**Legacy scan.** Detailed coverage status is unavailable for this archived scan.\n"
        )
        return lines

    normalized = str(status).strip().lower()
    if normalized == "complete":
        lines.append(
            "**Scan complete.** Detailed coverage metadata reports no scan limitations.\n"
        )
        return lines
    if normalized == "failed":
        lines.append(
            "**Scan failed.** No input table reached numeric scanning. "
            "This report does not represent a completed scan.\n"
        )
    elif normalized == "partial":
        lines.append(
            "**Scan partial.** Findings below reflect completed numeric scanning, "
            "but the listed coverage limitations apply.\n"
        )
    else:
        lines.append(
            f"**Scan status:** {_markdown_inline_code(normalized)}. "
            "Detailed coverage status is unavailable for this scan.\n"
        )

    if limitations:
        lines.append("### Coverage limitations\n")
        for limitation in limitations:
            if isinstance(limitation, dict):
                reason_value = limitation.get("reason")
                if "reason" not in limitation or reason_value is None:
                    reason_value = "unspecified"
                reason = _markdown_inline_code(reason_value)
                detail_keys = (
                    ["scope"] if "scope" in limitation else []
                ) + sorted(
                    (key for key in limitation if key not in {"reason", "scope"}),
                    key=_markdown_status_sort_key,
                )
                details = " · ".join(
                    f"{_markdown_inline_code(_markdown_status_value(key).replace('_', ' ').strip())}: "
                    f"{_markdown_inline_code(limitation[key])}"
                    for key in detail_keys
                    if limitation.get(key) is not None
                )
            else:
                reason = _markdown_inline_code(limitation)
                details = ""
            suffix = f" · {details}" if details else ""
            lines.append(f"- {reason}{suffix}")
        lines.append("")
    return lines


def _markdown_cross_sheet_example(example):
    if not isinstance(example, dict):
        return (
            "    shared value: "
            f"{_markdown_inline_code(example)}"
        )
    if not example:
        return "    example: empty example object"
    if {"row", "col", "value"}.issubset(example):
        return (
            "    example: row "
            f"{_markdown_inline_code(example['row'])}, col "
            f"{_markdown_inline_code(example['col'])}, value "
            f"{_markdown_inline_code(example['value'])}"
        )
    labels = {
        "row_a": "row A",
        "col_a": "col A",
        "value_a": "value A",
        "row_b": "row B",
        "col_b": "col B",
        "value_b": "value B",
        "decimal_tail": "decimal tail",
        "start_col": "start col",
        "end_col": "end col",
    }
    parts = [
        f"{_markdown_inline_code(labels.get(key, key.replace('_', ' ')))}="
        f"{_markdown_inline_code(example[key])}"
        for key in sorted(
            example,
            key=_markdown_status_sort_key,
        )
    ]
    return "    example: " + ", ".join(parts)


def write_markdown_report(out, path):
    raw_scan_status = out.get("scan_status")
    scan_status = (
        str(raw_scan_status).strip().lower()
        if raw_scan_status is not None else None
    )
    lines = ["# Paper data audit report\n"]
    lines.extend(_markdown_scan_status(out))
    scanned_at = out.get("scanned_at")
    elapsed_ms = (out.get("scan_stats") or {}).get("elapsed_ms")
    if scanned_at is not None:
        lines.append(
            f"- Scanned at: {_markdown_inline_code(scanned_at)}"
        )
    if elapsed_ms is not None:
        lines.append(
            f"- Elapsed: {_markdown_inline_code(f'{elapsed_ms} ms')}"
        )
    lines.extend([
        f"- Input: `{out['input_dir']}`",
        f"- Files scanned: {out['n_files']}",
        f"- Blocks with findings: {out['n_blocks_with_findings']}\n",
    ])

    high = []
    medium = []
    def push(b, r):
        sev = r.get("severity", "low")
        row = dict(file=b["file"], sheet=b["sheet"], block_rows=b["block"]["rows"],
                   kind=r["kind"], rule=r.get("rule", ""), n=r.get("n", r.get("n_cells", "?")))
        (high if sev == "high" else medium).append(row)

    for b in out["relations_blocks"]:
        for r in b["relations"]:
            push(b, r)
        for r in b["equal_pairs"]:
            push(b, r)
        for r in b.get("row_pairs", []):
            push(b, r)
        for r in b["progressions"]:
            push(b, r)
        for r in b.get("within_col", []):
            push(b, r)
        for r in b.get("identical_after_rounding", []):
            push(b, r)
        for r in b.get("grim", []):
            push(b, r)

    csf = out.get("cross_sheet_findings", [])
    has_findings = bool(
        high
        or medium
        or csf
        or any(
            d["fdr_significant"] if "fdr_significant" in d else d.get("p", 1) < 1e-6
            for d in out["digit_distribution"]
        )
        or any(d.get("top") for d in out["decimal_endings"])
    )
    if not has_findings:
        if scan_status == "failed":
            lines.append("No findings are listed because numeric scanning did not start.\n")
        elif scan_status == "partial":
            lines.append(
                "No findings were recorded in the completed portion of this partial scan.\n"
            )
        elif scan_status is None:
            lines.append(
                "No findings were recorded in this legacy scan; "
                "detailed coverage is unavailable.\n"
            )
        elif scan_status == "complete":
            lines.append("Nothing flagged in this dataset.\n")
        else:
            lines.append(
                "No findings were recorded; detailed coverage status "
                "is unavailable for this scan.\n"
            )

    if csf:
        lines.append(
            f"## Cross-table statistical signals ({len(csf)})\n"
        )
        for cf in csf:
            sev = cf.get("severity", "?")
            lines.append(f"- **[{cf['kind']}]** ({sev}) `{cf['file']}` — {cf['rule']}")
            for ex in cf.get("examples", [])[:3]:
                lines.append(_markdown_cross_sheet_example(ex))
        lines.append("")

    lines.append(f"## High-severity findings ({len(high)})\n")
    for r in high[:40]:
        lines.append(f"- **[{r['kind']}]** `{r['file']}::{r['sheet']}` rows {r['block_rows']}, n={r['n']}  \n  → `{r['rule']}`")
    if len(high) > 40:
        lines.append(f"- … and {len(high) - 40} more (see scan.json)")
    lines.append("")

    lines.append(f"## Medium-severity findings ({len(medium)})\n")
    for r in medium[:30]:
        lines.append(f"- [{r['kind']}] `{r['file']}::{r['sheet']}` rows {r['block_rows']}, n={r['n']} → `{r['rule']}`")
    if len(medium) > 30:
        lines.append(f"- … and {len(medium) - 30} more (see scan.json)")
    lines.append("")

    # last-digit chi-square (BH-FDR-significant, falling back to raw p for old scans)
    def _digit_sig(d):
        return d["fdr_significant"] if "fdr_significant" in d else d["p"] < 1e-6
    sig_digits = sorted([d for d in out["digit_distribution"] if _digit_sig(d)],
                        key=lambda d: d.get("p_adj", d["p"]))
    lines.append(f"## Last-digit χ² anomalies ({len(sig_digits)} sheets, BH-FDR q ≤ 0.05)\n")
    for d in sig_digits[:20]:
        top = ", ".join([f"{k}×{v}" for k, v in d["top"]])
        qv = f" q={d['p_adj']:.1e}" if "p_adj" in d else ""
        lines.append(f"- `{d['label']}` n={d['n']} χ²={d['chi2']:.1f} p={d['p']:.1e}{qv} top: {top}")
    lines.append("")

    # decimal endings
    sig_dec = [d for d in out["decimal_endings"] if d["top"]]
    lines.append(f"## Over-represented two-decimal endings ({len(sig_dec)} sheets)\n")
    for d in sig_dec[:20]:
        top = ", ".join([f".{e}×{c}" for e, c in d["top"][:5]])
        lines.append(f"- `{d['label']}` n={d['n']}, unique={d['n_unique']}, top: {top}")
    lines.append("")

    with open(path, "w") as fh:
        fh.write("\n".join(lines))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "fetch":
        from .fetch._cli import fetch_main
        sys.exit(fetch_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        import json
        from ._adjudicated_html import write_adjudicated_report

        rp = argparse.ArgumentParser(
            prog="paperconan report",
            description="Render an adjudicated HTML report from scan.json and verdict.json",
        )
        rp.add_argument("scan_json", help="Path to paperconan scan.json")
        rp.add_argument("--verdict", required=True, help="Path to verdict JSON")
        rp.add_argument("--out", required=True, help="Output HTML path")
        rargs = rp.parse_args(sys.argv[2:])
        with open(rargs.scan_json, encoding="utf-8") as fh:
            scan = json.load(fh)
        with open(rargs.verdict, encoding="utf-8") as fh:
            verdict = json.load(fh)
        write_adjudicated_report(scan, verdict, rargs.out)
        print(f"wrote {rargs.out}")
        return
    ap = argparse.ArgumentParser(
        description=(
            "Surface statistical signals and data inconsistencies in a paper's "
            "supplementary source data (xlsx/csv/tsv or tables inside pdf/docx)"
        )
    )
    ap.add_argument("in_dir", help="Directory with the paper's source data (*.xlsx/*.csv/*.tsv, or *.pdf/*.docx supplements)")
    ap.add_argument("--out", default=None, help="Output directory (default: <in_dir>/audit)")
    ap.add_argument("--md", action="store_true",
                    help="Also write REPORT.md (default: only scan.json + report.html)")
    ap.add_argument("--no-html", action="store_true",
                    help="Skip the HTML report (only scan.json, plus REPORT.md if --md)")
    ap.add_argument("--doi", default=None,
                    help="Record this paper DOI as scan.json provenance "
                         "(overrides any paperconan_source.json sidecar)")
    ap.add_argument("--title", default=None, help="Record this paper title as provenance")
    ap.add_argument("--profile", choices=("review", "forensic", "triage"),
                    default="review",
                    help="False-positive handling profile: review (default), forensic, or triage")
    ap.add_argument(
        "--runtime-metadata",
        action="store_true",
        help="Record wall-clock timestamp and elapsed times",
    )
    ap.add_argument("--version", action="version", version=f"paperconan {_version()}")
    args = ap.parse_args()
    out_dir = args.out or os.path.join(args.in_dir, "audit")
    write_html = not args.no_html
    paper = None
    if args.doi or args.title:
        paper = {"doi": args.doi, "title": args.title}
    try:
        res = scan_dir(args.in_dir, out_dir, write_md=args.md, write_html=write_html,
                       paper=paper, profile=args.profile, diagnostic_on_empty=True,
                       include_runtime=args.runtime_metadata)
    except PaperconanInputError as e:
        sys.exit(str(e))
    if res["scan_status"] == "failed":
        print("scan failed: no input table reached numeric scanning", file=sys.stderr)
        raise SystemExit(1)
    outputs = [f"{out_dir}/scan.json"]
    if write_html:
        outputs.append(f"{out_dir}/report.html")
    if args.md:
        outputs.append(f"{out_dir}/REPORT.md")
    print("wrote " + ", ".join(outputs))
    print(f"  files: {res['n_files']}, blocks with findings: {res['n_blocks_with_findings']}")
    print(f"  digit anomaly sheets: {len(res['digit_distribution'])}, decimal anomaly sheets: {len(res['decimal_endings'])}")
    if write_html:
        print(f"\n  → open {out_dir}/report.html in a browser to review findings")


if __name__ == "__main__":
    main()
