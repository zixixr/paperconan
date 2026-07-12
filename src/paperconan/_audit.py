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
import csv as _csv
import ctypes
import datetime
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
from ._numeric import integer_shift_close, relation_close
from ._profiles import apply_profile_to_findings, normalize_profile
from ._sheet import Sheet, _MAX_EXACT_FLOAT_INT
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


def _move_numeric_rows_in_place(numeric, old_cols, new_cols, used_rows, used_cols):
    if not used_rows or not used_cols or old_cols == new_cols:
        return
    rows = (
        range(used_rows)
        if new_cols < old_cols
        else range(used_rows - 1, -1, -1)
    )
    address = numeric.ctypes.data
    row_bytes = used_cols * numeric.itemsize
    for row in rows:
        source = address + row * old_cols * numeric.itemsize
        target = address + row * new_cols * numeric.itemsize
        if source != target:
            ctypes.memmove(target, source, row_bytes)


def _resize_numeric_in_place(
    numeric, target_rows, target_cols, used_rows, used_cols
):
    old_rows, old_cols = numeric.shape
    if (old_rows, old_cols) == (target_rows, target_cols):
        return
    if target_cols < old_cols:
        _move_numeric_rows_in_place(
            numeric, old_cols, target_cols, used_rows, used_cols
        )
    numeric.resize((target_rows, target_cols), refcheck=False)
    if target_cols > old_cols:
        _move_numeric_rows_in_place(
            numeric, old_cols, target_cols, used_rows, used_cols
        )
    if used_rows and target_cols > used_cols:
        numeric[:used_rows, used_cols:target_cols] = np.nan
    if target_rows > used_rows:
        numeric[used_rows:target_rows, :] = np.nan


def _fill_sheet_from_rows(rows_iter, mr, mc, loaded):
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
        return None, declared
    remaining = _MAX_CELLS - loaded
    numeric = np.full((mr, mc), np.nan, dtype=float) if (mr and mc) else np.empty((0, 0))
    text = {}
    ints = set()
    wide_ints = {}
    r = 0                                        # rows consumed (== final nrows)
    max_w = 0                                    # max row width seen (== final ncols)
    for row in rows_iter:
        width = len(row)
        projected_rows = r + 1
        projected_width = max(max_w, width)
        cells = _dense_cells(projected_rows, projected_width)
        if loaded + cells > _MAX_CELLS:
            return None, cells
        if projected_rows > numeric.shape[0] or projected_width > numeric.shape[1]:
            target_rows = max(numeric.shape[0], projected_rows)
            target_cols = max(numeric.shape[1], projected_width)
            if _dense_cells(target_rows, target_cols) > remaining:
                target_rows, target_cols = projected_rows, projected_width
            if _dense_cells(target_rows, target_cols) > remaining:
                return None, cells
            _resize_numeric_in_place(
                numeric, target_rows, target_cols, r, max_w
            )
        for c, v in enumerate(row):
            if is_num(v):
                if isinstance(v, int) and abs(v) > _MAX_EXACT_FLOAT_INT:
                    wide_ints[(r, c)] = v
                else:
                    numeric[r, c] = float(v)
                    if isinstance(v, int) and not isinstance(v, bool):
                        ints.add((r, c))
            elif v is not None:
                text[(r, c)] = v
        max_w = projected_width
        r += 1
    cells = _dense_cells(r, max_w)
    # Trim to the geometry Sheet.from_rows would produce: nrows == rows consumed,
    # ncols == max(len(row)). (numeric may be larger if the reader over-declared.)
    n_rows, n_cols = r, max_w
    _resize_numeric_in_place(numeric, n_rows, n_cols, n_rows, n_cols)
    text = {(rr, cc): val for (rr, cc), val in text.items()
            if rr < n_rows and cc < n_cols}
    ints = {(rr, cc) for (rr, cc) in ints if rr < n_rows and cc < n_cols}
    wide_ints = {(rr, cc): val for (rr, cc), val in wide_ints.items()
                 if rr < n_rows and cc < n_cols}
    return Sheet(
        numeric.shape[0],
        numeric.shape[1],
        numeric,
        text,
        ints,
        wide_ints,
    ), cells


def _load_workbook_openpyxl(path):
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
                out[s] = None
                continue
            sheet, cells = _fill_sheet_from_rows(
                ws.iter_rows(values_only=True), mr, mc, loaded
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


def _load_workbook_calamine_scoped(path):
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

        sheet, cells = _fill_sheet_from_rows(normalized_rows(), h, w, loaded)
        out[name] = sheet
        if sheet is not None:
            if wide_ooxml_integer:
                return _CALAMINE_OPENPYXL_FALLBACK
            loaded += cells
    return out


def _load_workbook_calamine(path):
    """Return dict of sheet_name -> Sheet via python-calamine (a fast Rust reader),
    producing a Sheet byte-identical to _load_workbook_openpyxl. Same _MAX_CELLS
    per-sheet + cumulative guard, same oversized->None, same trim-to-max-width."""
    result = _load_workbook_calamine_scoped(path)
    if result is _CALAMINE_OPENPYXL_FALLBACK:
        return _load_workbook_openpyxl(path)
    return result


def _try_load_workbook_calamine(path):
    """Return a detached error signal after Calamine exception state unwinds."""
    try:
        return _load_workbook_calamine(path)
    except Exception:
        return _CALAMINE_READER_ERROR


def load_workbook_rows(path):
    """Return dict of sheet_name -> Sheet. Uses python-calamine (a fast Rust xlsx
    reader) when installed. OOXML inputs fall back to the openpyxl reference path
    after Calamine errors; legacy inputs remain on Calamine because openpyxl cannot
    read them. Both successful paths produce a byte-identical Sheet."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".xlsx", ".xlsm"}:
        return _load_workbook_calamine(path)
    result = _try_load_workbook_calamine(path)
    if result is _CALAMINE_READER_ERROR:
        return _load_workbook_openpyxl(path)
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


def load_csv_rows(path, delimiter):
    """Load a delimited text file as {sheet_name: Sheet|None}, mirroring load_workbook_rows.
    A flat file has no sheets, so it becomes a single sheet named after the file stem.
    Oversized (> _MAX_CELLS) -> {stem: None}; otherwise the rows are wrapped in a Sheet."""
    stem = os.path.splitext(os.path.basename(path))[0]
    rows = []
    oversized = False
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            rows = []
            row_count = 0
            max_width = 0
            oversized = False
            with open(path, newline="", encoding=enc) as fh:
                for r in _csv.reader(fh, delimiter=delimiter):
                    row_count += 1
                    max_width = max(max_width, len(r))
                    if _dense_cells(row_count, max_width) > _MAX_CELLS:
                        oversized = True
                        break
                    rows.append([_coerce_cell(c) for c in r])
            break
        except UnicodeDecodeError:
            continue
    if oversized:
        return {stem: None}
    return {stem: Sheet.from_rows(rows)}


def _load_table_sheets(path):
    """Dispatch by extension to a {sheet_name: Sheet|None} loader."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tsv":
        return load_csv_rows(path, delimiter="\t")
    if ext == ".csv":
        return load_csv_rows(path, delimiter=",")
    if ext == ".pdf":
        from ._extract import load_pdf_tables
        return {k: (None if v is None else Sheet.from_rows(v)) for k, v in load_pdf_tables(path).items()}
    if ext == ".docx":
        from ._extract import load_docx_tables
        return {k: (None if v is None else Sheet.from_rows(v)) for k, v in load_docx_tables(path).items()}
    return load_workbook_rows(path)


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
            with_metadata=True,
        )
        sheets = {
            name: None if rows is None else Sheet.from_rows(rows)
            for name, rows in extracted.tables.items()
        }
        limitations = list(extracted.limitations)
    else:
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


def load_table(path) -> dict[str, Sheet | None]:
    """Dispatch by extension to a {sheet_name: Sheet|None} loader."""
    return load_table_result(path).sheets


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
    all_indices = list(range(start, end))
    if len(all_indices) <= limit:
        return all_indices
    selected = sorted({
        index for index in highlights
        if start <= index < end
    })
    target = max(limit, len(selected))
    for index in all_indices:
        if len(selected) >= target:
            break
        if index not in selected:
            selected.append(index)
    return sorted(selected)


def _block_evidence(sheet, r0, r1, c0, c1, header, highlight_cols, highlight_rows=None):
    """Slice a numeric block (with 1 row of context above/below if available) into a
    JSON-friendly evidence dict that the HTML renderer can show as a table.

    The emitted snippet uses deterministic row and column selections bounded by
    _MAX_EV_ROWS × _MAX_EV_COLS unless the highlighted cells themselves exceed
    those bounds. This stops a dense block from being copied whole into every
    finding while preserving every retained highlight. Small blocks are emitted
    whole and stay byte-identical (no `truncated` key)."""
    truncated = False

    # --- column selection ----------------------------------------------------
    col_indices = list(range(c0, c1))
    if len(col_indices) > _MAX_EV_COLS:
        truncated = True
        col_indices = _bounded_evidence_indices(
            c0,
            c1,
            highlight_cols,
            max(0, _MAX_EV_COLS),
        )

    # --- row selection -------------------------------------------------------
    r_start = max(0, r0 - 1)
    r_end = min(sheet.nrows, r1 + 1)
    row_indices = list(range(r_start, r_end))
    if len(row_indices) > _MAX_EV_ROWS:
        truncated = True
        row_indices = _bounded_evidence_indices(
            r_start,
            r_end,
            [
                row_number - 1
                for row_number in (highlight_rows or [])
            ],
            max(0, _MAX_EV_ROWS),
        )

    data_rows = []
    for r in row_indices:
        vals = [
            _cell_value(sheet.cell(r, c))
            for c in col_indices
        ]
        data_rows.append({
            "row_idx": r + 1,
            "is_context": r < r0 or r >= r1,
            "values": vals,
        })
    out = {
        "headers": [
            header[c - c0]
            for c in col_indices
        ],
        "col_offset": col_indices[0] if col_indices else c0,
        "highlight_cols": list(highlight_cols),
        "highlight_rows": list(highlight_rows) if highlight_rows else [],
        "rows": data_rows,
    }
    if truncated:
        out["truncated"] = True
        out["col_indices"] = col_indices
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
        for ex in f.get("example_cells", []) or []:
            try:
                hi_rows.append(int(ex[0]))
                hi_cols.append(int(ex[1]) - 1)
            except (TypeError, ValueError, IndexError):
                pass
        # De-duplicate (order-preserving): a column/row referenced by both an *_idx
        # field and one or more example_cells should highlight once, not N times.
        hi_cols = list(dict.fromkeys(hi_cols))
        hi_rows = list(dict.fromkeys(hi_rows))
        evidence = _block_evidence(
            sheet,
            r0,
            r1,
            c0,
            c1,
            header,
            highlight_cols=hi_cols,
            highlight_rows=hi_rows,
        )
        f["evidence"] = evidence
        truncated = truncated or bool(evidence.get("truncated"))
    return truncated


# ---------- detectors ----------

def _isclose_rowwise(actual, expected, rtol=1e-10):
    return relation_close(actual, expected, rtol=rtol)


def _allclose_rowwise(actual, expected, rtol=1e-10):
    return bool(np.all(_isclose_rowwise(actual, expected, rtol=rtol)))


def _numeric_pairs(sheet, r0, r1, ca, cb):
    pairs = []
    for row in range(r0, r1):
        left = sheet.exact_numeric(row, ca)
        right = sheet.exact_numeric(row, cb)
        if left is not None and right is not None:
            pairs.append((row, left, right))
    return pairs


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


def detect_relations(sheet, r0, r1, c0, c1, header):
    findings = []
    cols = [(c, col_array(sheet, r0, r1, c)) for c in range(c0, c1)]
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            ci, ai = cols[i]
            cj, aj = cols[j]
            pairs = _numeric_pairs(sheet, r0, r1, ci, cj)
            if len(pairs) < 4:
                continue
            exact_x = [pair[1] for pair in pairs]
            exact_y = [pair[2] for pair in pairs]
            if all(a == b for a, b in zip(exact_x, exact_y)):
                findings.append(dict(
                    kind="identical_column",
                    col_a=header[ci - c0],
                    col_b=header[cj - c0],
                    col_a_idx=ci,
                    col_b_idx=cj,
                    n=len(pairs),
                    severity="high",
                    col_a_sample=_sample_exact(exact_x),
                    col_b_sample=_sample_exact(exact_y),
                    rule=f"col[{cj}] == col[{ci}]",
                ))
                continue
            if all(isinstance(value, int) for value in exact_x + exact_y):
                offsets = {b - a for a, b in zip(exact_x, exact_y)}
                if len(offsets) == 1 and next(iter(offsets)) != 0:
                    offset = next(iter(offsets))
                    findings.append(dict(
                        kind="constant_offset",
                        col_a=header[ci - c0],
                        col_b=header[cj - c0],
                        col_a_idx=ci,
                        col_b_idx=cj,
                        n=len(pairs),
                        offset=offset,
                        severity="high",
                        col_a_sample=_sample_exact(exact_x),
                        col_b_sample=_sample_exact(exact_y),
                        rule=f"col[{cj}] = col[{ci}] + {offset}",
                    ))
                    continue
            mask = ~np.isnan(ai) & ~np.isnan(aj)
            n = int(mask.sum())
            if n < 4:
                continue
            x, y = ai[mask], aj[mask]
            # Compact value peek for downstream LLM triage (bounded <=8 each, ~tiny).
            sa, sb = _sample(x), _sample(y)
            # B4 retains its existing scale-relative run policy. Whole-column identity,
            # transforms, and integer shifts use their dedicated policies below.
            tol = 1e-9 * max(float(np.max(np.abs(x))), float(np.max(np.abs(y))), 1e-300)
            # constant offset
            diff = y - x
            mean_diff = float(np.mean(diff))
            if mean_diff != 0 and np.all(relation_close(y, x + mean_diff)):
                findings.append(dict(kind="constant_offset", col_a=header[ci - c0], col_b=header[cj - c0],
                                     col_a_idx=ci, col_b_idx=cj, n=n, offset=mean_diff,
                                     severity="high",
                                     col_a_sample=sa, col_b_sample=sb,
                                     rule=f"col[{cj}] = col[{ci}] + {mean_diff:.6g}"))
                continue
            # constant ratio
            ratio_emitted = False
            if np.all(x != 0):
                ratio = y / x
                mean_ratio = float(np.mean(ratio))
                ratio_tol = 1e-9 * max(abs(mean_ratio), 1e-300)
                if (
                    np.std(ratio) < ratio_tol
                    and abs(mean_ratio - 1) > 1e-9
                    and abs(mean_ratio) > 1e-9
                    and np.all(relation_close(y, mean_ratio * x))
                ):
                    findings.append(dict(kind="constant_ratio", col_a=header[ci - c0], col_b=header[cj - c0],
                                         col_a_idx=ci, col_b_idx=cj, n=n, ratio=mean_ratio,
                                         severity="high",
                                         col_a_sample=sa, col_b_sample=sb,
                                         rule=f"col[{cj}] = col[{ci}] * {mean_ratio:.6g}"))
                    ratio_emitted = True
            # mirror: x + y == constant
            csum = x + y
            if n >= 5:
                K = float(np.mean(csum))
                if K != 0 and np.all(relation_close(csum, np.full_like(csum, K))):
                    findings.append(dict(kind="sum_constant", col_a=header[ci - c0], col_b=header[cj - c0],
                                         col_a_idx=ci, col_b_idx=cj, n=n, sum=K,
                                         severity="high",
                                         col_a_sample=sa, col_b_sample=sb,
                                         rule=f"col[{ci}] + col[{cj}] = {K:.6g}"))
            # exact linear (non-identical)
            if n >= 5 and np.ptp(x) > 0:
                lo = int(np.argmin(x))
                hi = int(np.argmax(x))
                dx = x[hi] - x[lo]
                try:
                    _fit_slope, _fit_intercept, r, _p, _se = stats.linregress(x, y)
                except ValueError:
                    continue
                if dx == 0:
                    continue
                slope = (y[hi] - y[lo]) / dx
                intercept = y[lo] - slope * x[lo]
                fitted = slope * x + intercept
                if np.std(y) > 0 and np.all(relation_close(y, fitted, rtol=1e-7)) and abs(r) > 0.99:
                    # A scale-relatively zero intercept means the fit is y = slope*x: the
                    # identity (slope~=1, caught by identical_column) or a pure scaling. When a
                    # constant_ratio already captured that scaling, a second exact_linear finding
                    # is redundant (same relationship, b==0 to round-off) and only inflates the
                    # count — suppress it. exact_linear is reserved for a non-zero
                    # intercept (an affine offset constant_ratio cannot express), and still fires
                    # when no constant_ratio covered the pair (e.g. a zero in x skips its guard).
                    intercept_is_zero = abs(intercept) < tol
                    is_identity = abs(slope - 1) < 1e-9 and intercept_is_zero
                    redundant_scaling = intercept_is_zero and ratio_emitted
                    if not (is_identity or redundant_scaling):
                        findings.append(dict(kind="exact_linear", col_a=header[ci - c0], col_b=header[cj - c0],
                                             col_a_idx=ci, col_b_idx=cj, n=n,
                                             slope=float(slope), intercept=float(intercept),
                                             severity="high",
                                             col_a_sample=sa, col_b_sample=sb,
                                             rule=f"col[{cj}] = {slope:.4g} * col[{ci}] + {intercept:.4g}"))
            # B4: partial constant offset — a long CONSECUTIVE run where y = x + k for a fixed
            # non-zero k, while the rest of the column diverges (the whole-column case is
            # constant_offset above). A contiguous block shifted by a fixed amount is a
            # copy-then-shift fingerprint; two independent columns do not hold a fixed offset
            # over a long contiguous run. Guarded to non-trivial offsets and long runs only.
            if n >= 24:
                # Scale-relative run detection on the raw diff (a fixed decimal round would be
                # inert on small-magnitude data — the exact regime `tol` above was written for).
                best_len = cur_len = 1
                best_val = float(diff[0])
                for t in range(1, len(diff)):
                    if abs(diff[t] - diff[t - 1]) < tol:
                        cur_len += 1
                    else:
                        if cur_len > best_len:
                            best_len, best_val = cur_len, float(diff[t - 1])
                        cur_len = 1
                if cur_len > best_len:
                    best_len, best_val = cur_len, float(diff[-1])
                run_floor = max(20, int(round(0.5 * n)))
                col_hp = sum(1 for v in x if _sig_frac_digits(v) >= 2) >= 0.6 * len(x)
                # The benign case to exclude is a run shifted by a small WHOLE number on
                # low-precision data (e.g. B = A + 5). Test that scale-relatively (tol), so a
                # A small-magnitude offset like 3e-14 is not mistaken for "integer 0".
                off_is_small_integer = abs(best_val - round(best_val)) < tol and abs(round(best_val)) >= 1
                non_trivial_offset = (not off_is_small_integer) or col_hp
                if (best_len >= run_floor and best_len < n
                        and abs(best_val) > tol and non_trivial_offset):
                    findings.append(dict(kind="partial_constant_offset",
                                         col_a=header[ci - c0], col_b=header[cj - c0],
                                         col_a_idx=ci, col_b_idx=cj, n=n,
                                         run_length=int(best_len), offset=float(best_val),
                                         severity="high",
                                         col_a_sample=sa, col_b_sample=sb,
                                         rule=(f"col[{cj}] = col[{ci}] + {best_val:.6g} over a run of "
                                               f"{int(best_len)}/{n} consecutive rows")))
                    continue
            # integer difference with shared decimal fractions (B5), else small discrete diff set
            # B5: y and x reproduce each other's HIGH-PRECISION decimal fractions row-wise while
            # differing only by whole numbers that VARY across rows (a constant integer offset is
            # already caught above as constant_offset). Independent measurements do not reproduce
            # another column's 4+-decimal fractions on several rows — a copy-then-shift fingerprint
            # (e.g. 178.7615 vs 112.7615, 169.8687 vs 115.8687). The precision requirement lets this
            # fire from n>=5 without the false positives a bare small-diff-set floor would admit.
            if n >= 5:
                # Per-row tolerance for the integer-difference test: representation noise at each
                # row's OWN magnitude, not the column-wide max. A single extreme value (an inf /
                # placeholder like a 1e99 fold-change for a zero-denominator row) must not inflate
                # the tolerance so that every row's diff reads as a whole number — that produced
                # spurious whole-sheet integer_diff_shared_fraction findings (M2-1).
                diff_is_int = integer_shift_close(x, y)
                frac_x = x - np.round(x)                       # signed distance to nearest integer
                hp_rows = diff_is_int & (np.abs(frac_x) > 1e-6)
                hp_fracs = [float(v) for v in frac_x[hp_rows] if _sig_frac_digits(v) >= 4]
                distinct_hp = len({round(v, 6) for v in hp_fracs})
                int_diffs = np.unique(np.round(diff[diff_is_int]))
                n_real_frac = int(hp_rows.sum())        # rows sharing a non-.0 fraction
                if (int(diff_is_int.sum()) >= max(5, int(round(0.8 * n)))
                        and distinct_hp >= 3
                        and len(int_diffs) >= 2):
                    findings.append(dict(kind="integer_diff_shared_fraction",
                                         col_a=header[ci - c0], col_b=header[cj - c0],
                                         col_a_idx=ci, col_b_idx=cj, n=n,
                                         n_shared_fraction=n_real_frac,
                                         n_high_precision=distinct_hp,
                                         severity="high",
                                         col_a_sample=sa, col_b_sample=sb,
                                         rule=(f"col[{cj}] and col[{ci}] share the same decimal fraction on "
                                               f"{n_real_frac}/{n} rows ({distinct_hp} distinct high-precision "
                                               f"fractions) but differ by whole numbers")))
                    continue
            # small discrete diff set
            if n >= 8:
                diff_rounded = np.round(diff, 4)
                uniq = np.unique(diff_rounded)
                if 2 <= len(uniq) <= min(6, n // 3):
                    findings.append(dict(kind="small_diff_set", col_a=header[ci - c0], col_b=header[cj - c0],
                                         col_a_idx=ci, col_b_idx=cj, n=n,
                                         unique_diffs=[float(x) for x in uniq],
                                         severity="medium",
                                         col_a_sample=sa, col_b_sample=sb,
                                         rule=f"col[{cj}] - col[{ci}] only takes {len(uniq)} discrete values"))
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


def detect_row_pair_digit_coupling(
    sheet, r0, r1, c0, c1, header, min_n=10, *, with_coverage=False
):
    """Detect paired rows that preserve low-order digits across many cells.

    This targets source-data layouts where replicate/condition rows are aligned by
    measurement column. The statistical signal is: row B differs from row A in
    value, but the first decimal digit and often the ones digit are preserved
    across many paired cells, with differences frequently landing on coarse
    multiples of 10.
    """
    findings = []
    n_rows = r1 - r0
    n_cols = c1 - c0
    if n_rows < 2 or n_cols < min_n:
        return findings
    if n_rows > _ROW_PAIR_MAX_ROWS or n_cols > _ROW_PAIR_MAX_COLS:
        return findings

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
            findings.append(dict(
                kind="row_pair_digit_coupling",
                row_a=label_a,
                row_b=label_b,
                row_a_idx=ra,
                row_b_idx=rb,
                n=n,
                changed=changed,
                same_decimal1=same_decimal1,
                same_decimal1_frac=frac_decimal1,
                same_ones=same_ones,
                same_ones_decimal1=same_ones_decimal1,
                same_ones_decimal1_frac=frac_ones_decimal1,
                coarse_10_diff=coarse_10_diff,
                coarse_10_diff_frac=frac_coarse_10,
                top_diffs=[{"diff": float(d), "count": int(c)} for d, c in top_diffs],
                examples=examples,
                example_cells=[(ra + 1, ex["col"]) for ex in examples[:4]]
                              + [(rb + 1, ex["col"]) for ex in examples[:4]],
                severity=severity,
                rule=(f"rows {ra + 1} and {rb + 1}: first decimal digit matches "
                      f"{same_decimal1}/{n}; ones+decimal matches "
                      f"{same_ones_decimal1}/{n}; coarse 10-step differences "
                      f"{coarse_10_diff}/{n}"),
            ))

    findings.sort(key=lambda f: (
        0 if f["severity"] == "high" else 1,
        -f["same_decimal1_frac"],
        -f["same_ones_decimal1_frac"],
        -f["coarse_10_diff_frac"],
        -f["n"],
    ))
    omitted = max(0, len(findings) - _ROW_PAIR_MAX_FINDINGS_PER_BLOCK)
    kept = findings[:_ROW_PAIR_MAX_FINDINGS_PER_BLOCK]
    if with_coverage:
        return kept, {"findings_omitted": omitted}
    return kept


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


def detect_arithmetic_progression(sheet, r0, r1, c0, c1, header):
    findings = []
    for c in range(c0, c1):
        a = col_array(sheet, r0, r1, c)
        a = a[~np.isnan(a)]
        if len(a) < 5:
            continue
        diffs = np.diff(a)
        tol = 1e-9 * max(float(np.max(np.abs(a))), 1e-300)   # scale-relative (see detect_relations)
        if np.allclose(diffs, diffs[0], atol=tol, rtol=1e-9) and abs(diffs[0]) > tol:
            sev = "medium" if abs(diffs[0] - round(diffs[0])) < 1e-9 else "high"
            findings.append(dict(kind="arithmetic_progression", col=header[c - c0], col_idx=c,
                                 block_c0=c0,
                                 n=int(len(a)), step=float(diffs[0]), first=float(a[0]),
                                 severity=sev,
                                 rule=f"col[{c}] = arithmetic progression, step={diffs[0]:.6g}"))
    return findings


def detect_within_column_patterns(sheet, r0, r1, c0, c1, header, min_n=6):
    """Detect within-column anomalies:
       - many identical values in one column (Su Jiacao: '13 中 8 个相同')
       - many values sharing same last-2 decimals (Su Jiacao: '13 中 11 个末两位相同')
       - too many .0 / .5 endings (Su Jiacao: '71 个中 51 个末位 0 或 5')
       - missing last digits (Su Jiacao: '70 个数据中末位完全没有 3 或 7')
    """
    findings = []
    for c in range(c0, c1):
        a = col_array(sheet, r0, r1, c)
        a_clean = a[~np.isnan(a)]
        n = len(a_clean)
        if n < min_n:
            continue
        col_name = header[c - c0] if c - c0 < len(header) else f"col{c}"

        # Cheap column descriptors shared by the within-col detectors below, so a
        # downstream prefilter can decide precisely (categorical/integer column,
        # low-cardinality, value peek) instead of guessing from the column name alone.
        vals_rounded = np.round(a_clean, 4)
        counts = Counter(vals_rounded.tolist())
        n_distinct = int(len(counts))
        all_integer = bool(np.all(np.abs(a_clean - np.round(a_clean)) < 1e-9))
        value_sample = [float(v) for v, _ in counts.most_common(8)]
        enrich = dict(n_distinct=n_distinct, all_integer=all_integer, value_sample=value_sample)

        # 1) duplicate values within the column
        top_val, top_count = counts.most_common(1)[0]
        if top_count >= max(4, n // 2) and n - top_count >= 1:
            findings.append(dict(kind="within_col_value_duplication",
                                 col=col_name, col_idx=c, n=n,
                                 dup_value=float(top_val), dup_count=int(top_count),
                                 frac_repeat=top_count / n, **enrich,
                                 severity="high",
                                 rule=f"col[{c}] has value {top_val} repeated {top_count}/{n} times"))

        # 2) last-2-decimal repetition within column
        endings = [trailing_decimal_digits(v, 2) for v in a_clean]
        endings = [e for e in endings if e is not None]
        if len(endings) >= max(min_n, 8):
            ec = Counter(endings)
            top_end, top_end_count = ec.most_common(1)[0]
            if top_end_count >= max(5, 2 * len(endings) // 3):
                findings.append(dict(kind="within_col_decimal_repetition",
                                     col=col_name, col_idx=c, n=len(endings),
                                     ending=top_end, count=int(top_end_count),
                                     frac_repeat=top_end_count / len(endings), **enrich,
                                     severity="high",
                                     rule=f"col[{c}]: {top_end_count}/{len(endings)} values share last-2 decimals '.{top_end}'"))

        # 3) too many .0 / .5 last decimal (rounded to half/int)
        last1 = [last_significant_digit(v) for v in a_clean]
        last1 = [d for d in last1 if d is not None]
        if len(last1) >= max(min_n, 10):
            zeros_fives = sum(1 for d in last1 if d in ("0", "5"))
            if zeros_fives >= max(7, 0.7 * len(last1)):
                findings.append(dict(kind="rounded_to_half_or_int",
                                     col=col_name, col_idx=c, n=len(last1),
                                     count_05=int(zeros_fives),
                                     severity="medium",
                                     rule=f"col[{c}]: {zeros_fives}/{len(last1)} values end in 0 or 5"))

        # 4) missing last-digit (3 or 7 completely absent in a large column)
        if len(last1) >= 20:
            present = set(last1)
            missing = [d for d in "123456789" if d not in present]
            if missing and len(present) <= 6:
                findings.append(dict(kind="missing_last_digits",
                                     col=col_name, col_idx=c, n=len(last1),
                                     missing=missing,
                                     severity="medium",
                                     rule=f"col[{c}]: last digits {missing} never appear in {len(last1)} values"))
    return findings


def detect_dispersed_repeats(sheet, r0, r1, c0, c1, header, min_n=30):
    """Many DISTINCT high-precision values each repeated across DISPERSED rows.

    Complements within_col_value_duplication (single dominant value). Targets a
    continuous, high-precision column whose exact-duplicate mass far exceeds the
    near-zero birthday expectation, where repeats are scattered across the table
    (not adjacent fill-down / technical replicates). Thresholds are conservative
    defaults pinned by tests; not env-tunable.
    """
    findings = []

    def _dec_places(v):
        s = f"{v:.10f}".rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0

    for c in range(c0, c1):
        rows_vals = []
        for r in range(r0, r1):
            v = sheet.cell(r, c)
            if is_num(v) and not (isinstance(v, float) and np.isnan(v)):
                rows_vals.append((r, float(v)))
        n = len(rows_vals)
        if n < min_n:
            continue
        vals = [v for _, v in rows_vals]

        # quick reject: pure-integer columns (counts / codes)
        if all(abs(v - round(v)) < 1e-9 for v in vals):
            continue

        # Strip a dominant boundary/censor value FIRST (e.g. 600s ceiling), so it
        # neither drags down the precision fraction nor counts as a "repeat".
        cnt_all = Counter(round(v, 6) for v in vals)
        top_v, top_c = cnt_all.most_common(1)[0]
        boundary = top_v if top_c > 0.25 * n else None
        core = [(r, v) for (r, v) in rows_vals
                if boundary is None or round(v, 6) != boundary]
        m = len(core)
        if m < min_n:
            continue
        core_vals = [v for _, v in core]

        # Gate 1 — continuity / high precision (computed on core)
        frac_hi_prec = sum(1 for v in core_vals if _dec_places(v) >= 2) / m
        if frac_hi_prec < 0.6:
            continue
        distinct = len({round(v, 6) for v in core_vals})
        if distinct < 50 or distinct / m < 0.3:
            continue

        # Gate 1b — birthday / effective-support gate: the recording precision must
        # be fine ENOUGH relative to the value range that exact collisions are
        # near-zero-expected. A coarse column (e.g. 2 decimals over [0,1] -> only
        # ~100 possible values) collides naturally and must NOT fire.
        dps = sorted(_dec_places(v) for v in core_vals)
        med_dp = dps[len(dps) // 2]
        support = (max(core_vals) - min(core_vals)) * (10 ** med_dp)
        if support < 20 * m:
            continue

        # Gate 2 + 3 — dispersed exact-duplicate groups
        positions = defaultdict(list)
        for r, v in core:
            positions[round(v, 6)].append(r)
        block_h = r1 - r0
        dispersed = []
        dup_cells = 0
        for val, rs in positions.items():
            if len(rs) < 2:
                continue
            rs_sorted = sorted(rs)
            span = rs_sorted[-1] - rs_sorted[0]
            non_adjacent = any(b - a > 1 for a, b in zip(rs_sorted, rs_sorted[1:]))
            if span >= 0.5 * block_h and non_adjacent:
                dispersed.append((val, rs_sorted))
                dup_cells += len(rs_sorted)

        if len(dispersed) >= 10 and dup_cells >= 0.15 * m:
            dispersed.sort(key=lambda kv: -len(kv[1]))
            example_cells = []
            for _, rs in dispersed[:3]:
                for rr in rs[:8]:
                    example_cells.append((rr + 1, c + 1))
            col_name = header[c - c0] if c - c0 < len(header) else f"col{c}"
            core_arr = np.round(np.array([v for _, v in core]), 4)
            counts = Counter(core_arr.tolist())
            findings.append(dict(
                kind="within_col_dispersed_repeats",
                col=col_name, col_idx=c, n=m,
                n_repeat_groups=len(dispersed), dup_cells=dup_cells,
                frac_repeat=dup_cells / m,
                n_distinct=int(len(counts)), all_integer=False,
                value_sample=[float(v) for v, _ in counts.most_common(8)],
                example_cells=example_cells,
                severity="medium",
                rule=(f"col[{c}]: {len(dispersed)} distinct high-precision values "
                      f"each recur across dispersed rows ({dup_cells}/{m} cells)")))
    return findings


def detect_identical_after_rounding(sheet, r0, r1, c0, c1, header):
    """Detect pairs/groups of cells that differ at higher precision but match at lower (e.g.
       4.2735 vs 4.2812 — both round to 4.3). Kang Tiebang ED6h/6j signal."""
    findings = []
    cells = []
    for r in range(r0, r1):
        for c in range(c0, c1):
            v = sheet.cell(r, c)
            if is_num(v) and abs(v) > 1e-9:
                cells.append((r, c, float(v)))
    if len(cells) < 20:
        return findings
    # Bucket cells by 1-decimal rounded value
    from collections import defaultdict
    buckets = defaultdict(list)
    for r, c, v in cells:
        if abs(v) < 100:  # only meaningful for measurement-scale numbers
            buckets[round(v, 1)].append((r, c, v))
    # Find buckets where multiple DIFFERENT (>1e-4 apart) values map to the same rounded value
    rounding_groups = []
    for k, lst in buckets.items():
        if len(lst) >= 4:
            uniq = set(round(v, 4) for _, _, v in lst)
            if len(uniq) >= 3:
                rounding_groups.append((k, lst))
    rounding_groups.sort(key=lambda kv: -len(kv[1]))
    if rounding_groups:
        top = rounding_groups[:5]
        for k, lst in top:
            uniq = sorted(set(round(v, 4) for _, _, v in lst))
            findings.append(dict(kind="identical_after_rounding",
                                 rounded_to=float(k), n_cells=len(lst), n_unique=len(uniq),
                                 example_values=uniq[:6],
                                 example_cells=[(r + 1, c + 1) for r, c, _ in lst[:6]],
                                 severity="medium",
                                 rule=f"{len(lst)} cells share rounded value {k} but have {len(uniq)} distinct precise values"))
    return findings


def detect_grim_grimmer(sheet, r0, r1, c0, c1, header):
    """GRIM/GRIMMER: flag reported means (and SDs) impossible for integer-valued
    data at the stated n. Strictly gated — needs a header-located mean+n group
    AND a count/score keyword in the MEAN column header signalling integer items —
    to stay false-positive-safe on continuous measurements where GRIM does not apply.
    GRIMMER runs only on a true SD column (SEM/SE columns are deliberately ignored,
    since GRIMMER is undefined for a standard error)."""
    findings = []
    for mean_i, n_i, sd_i in _grim_column_groups(header):
        mean_c, n_c = c0 + mean_i, c0 + n_i
        sd_c = c0 + sd_i if sd_i is not None else None
        grim_fail, grimmer_fail = [], []
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
                grim_fail.append((r, mean, n, d))
                continue                     # GRIM-failing rows are not re-reported
            if sd_c is not None:
                sv = sheet.cell(r, sd_c)
                if is_num(sv):
                    sd = float(sv)
                    ds = _decimals_of(sd)
                    grimmer_checked += 1
                    if not grimmer_consistent(mean, sd, n, d, ds):
                        grimmer_fail.append((r, mean, sd, n, ds))

        mean_name = str(header[mean_i] or f"col{mean_c}")
        n_name = str(header[n_i] or f"col{n_c}")
        sd_name = str(header[sd_i] or f"col{sd_c}") if sd_i is not None else None

        if grim_fail:
            f = dict(kind="grim_inconsistent", severity="high",
                     mean_col=mean_name, n_col=n_name, sd_col=sd_name,
                     col_a_idx=mean_c,
                     n=checked, n_rows_checked=checked, n_failed=len(grim_fail),
                     failed_rows=[dict(row=r + 1, mean=m, n=nn, decimals=dd,
                                       nearest_consistent=round(round(m * nn) / nn, dd))
                                  for (r, m, nn, dd) in grim_fail[:8]],
                     example_cells=[[r + 1, mean_c + 1] for (r, *_rest) in grim_fail[:8]],
                     rule=(f"{len(grim_fail)}/{checked} rows report a mean impossible for "
                           f"integer data at the stated n (GRIM): col '{mean_name}'"))
            if sd_c is not None:
                f["col_b_idx"] = sd_c
            findings.append(f)
        if grimmer_fail:
            findings.append(dict(
                kind="grimmer_inconsistent", severity="high",
                mean_col=mean_name, n_col=n_name, sd_col=sd_name,
                col_a_idx=mean_c, col_b_idx=sd_c,
                n=grimmer_checked, n_rows_checked=grimmer_checked, n_failed=len(grimmer_fail),
                failed_rows=[dict(row=r + 1, mean=m, sd=s, n=nn, sd_decimals=ds)
                             for (r, m, s, nn, ds) in grimmer_fail[:8]],
                example_cells=[[r + 1, sd_c + 1] for (r, *_rest) in grimmer_fail[:8]],
                rule=(f"{len(grimmer_fail)}/{grimmer_checked} rows report an SD impossible for "
                      f"integer data at the stated mean & n (GRIMMER): col '{sd_name}'")))
    return findings


def detect_last_digit(values, label):
    digits = [int(d) for d in (last_significant_digit(v) for v in values) if d is not None and d != "0"]
    if len(digits) < 40:
        return None
    counts = Counter(digits)
    obs = np.array([counts.get(d, 0) for d in range(1, 10)], dtype=float)
    expected = np.full(9, obs.sum() / 9.0)
    chi2 = ((obs - expected) ** 2 / expected).sum()
    p = float(1 - stats.chi2.cdf(chi2, df=8))
    most_common = counts.most_common(3)
    return dict(label=label, n=int(obs.sum()), chi2=float(chi2), p=p,
                counts={str(d): int(counts.get(d, 0)) for d in range(0, 10)},
                top=[[str(d), c] for d, c in most_common])


def detect_repeated_decimals(values, label):
    endings = [trailing_decimal_digits(v, 2) for v in values]
    endings = [e for e in endings if e is not None]
    if len(endings) < 60:
        return None
    counts = Counter(endings)
    n = len(endings)
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


def detect_equal_pairs(sheet, r0, r1, c0, c1, header):
    """Detect column pairs where many rows have identical values
    (e.g. tumor length == tumor width)."""
    findings = []
    for i in range(c1 - c0):
        for j in range(i + 1, c1 - c0):
            pairs = _numeric_pairs(sheet, r0, r1, c0 + i, c0 + j)
            n = len(pairs)
            if n < 6:
                continue
            exact_a = [pair[1] for pair in pairs]
            exact_b = [pair[2] for pair in pairs]
            equal_rows = [row for row, left, right in pairs if left == right]
            eq = len(equal_rows)
            all_equal = eq == n
            if eq >= max(6, n // 2) and eq / n >= 0.5 and not all_equal:
                findings.append(dict(kind="many_equal_pairs", col_a=header[i], col_b=header[j],
                                     col_a_idx=c0 + i, col_b_idx=c0 + j, n=n, equal=eq,
                                     severity="medium" if eq < n else "high",
                                     col_a_sample=_sample_exact(exact_a),
                                     col_b_sample=_sample_exact(exact_b),
                                     rule=f"col[{c0+i}] == col[{c0+j}] in {eq}/{n} rows"))
    return findings


# ---------- driver ----------

def _grid_from_rows(
    sheet,
    min_decimal_places=3,
    max_rows=200,
    max_cells=None,
    *,
    with_coverage=False,
):
    """Build {(r, c): rounded_value} of decimal-bearing numeric cells from a Sheet.
    Only keeps non-integer values with >= min_decimal_places decimals in a sane range —
    these are the values whose bit-identical reuse across tables warrants review."""
    grid = {}
    nm = sheet.numeric
    rmax = max(0, min(sheet.nrows, max_rows))
    cell_limit = None if max_cells is None else max(0, int(max_cells))
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
                        if cell_limit is not None and len(grid) >= cell_limit:
                            cell_limited = True
                            break
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
            "cells_used": len(grid),
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
    modified = sum(1 for k, v in ga.items() if k in gb and gb[k] != v)
    ca, cb = Counter(ga.values()), Counter(gb.values())
    shared = sum((ca & cb).values())
    only_a = sum(ca.values()) - shared
    only_b = sum(cb.values()) - shared
    # The value multiset is layout-robust, so decide on it FIRST: identical content is
    # a perfect_dup even if the two tables lay it out at different offsets (modified_cells
    # is then just a layout-shift artifact, meaningful only when layouts align).
    if only_a == 0 and only_b == 0:
        pattern = "perfect_dup"
    elif only_a == 0 or only_b == 0:
        pattern = "superset"
    elif modified > 0:
        pattern = "value_tweaked"
    else:
        pattern = "value_divergent"
    return dict(pattern=pattern, modified_cells=modified,
                shared_values=shared, only_in_a=only_a, only_in_b=only_b)


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
):
    """Find one aligned block where values differ but decimal tails are reused.

    This is layout-tolerant: if a table is pasted a few rows lower/upper, matching
    cells still share the same (row_delta, col_delta). Grouping by that offset
    distinguishes a copied block from isolated coincidental tail matches.
    """
    inv = {}
    for kb, vb in gb.items():
        sig = _decimal_tail_signature(
            vb,
            min_tail_digits=min_tail_digits,
            skip_decimal_digits=skip_decimal_digits,
        )
        if sig:
            inv.setdefault(sig, []).append((kb, vb))

    by_offset = {}
    for ka, va in ga.items():
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
            off = (kb[0] - ka[0], kb[1] - ka[1])
            by_offset.setdefault(off, []).append((ka, kb, float(va), float(vb), sig))

    if not by_offset:
        return None
    off, pairs = max(by_offset.items(), key=lambda kv: len(kv[1]))
    if len(pairs) < min_matches:
        return None
    pairs = sorted(pairs, key=lambda p: (p[0][0], p[0][1], p[1][0], p[1][1]))
    return {
        "offset": off,
        "pairs": pairs,
        "tail_match_count": len(pairs),
        "min_tail_digits": min_tail_digits,
        "skip_decimal_digits": skip_decimal_digits,
    }


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


def _axis_columns(grids, recur_min=3):
    """Classify, per (file, sheet), which columns are 'axis-like' so a cross-sheet
    overlap that lands only on them can be recognized as a shared-x-axis artifact.

    A column is axis-like if either:
      (A) its values form an arithmetic/geometric progression (a swept axis), or
      (B) its exact value-set recurs as a column across >= ``recur_min`` distinct
          (file, sheet) grids — i.e. the same axis was reused across many panels.
    """
    # (B) fingerprint columns by their value-set; count how many sheets carry each.
    fp_counts = Counter()
    col_fps = {}
    for key, grid in grids.items():
        cols = {c for (_, c) in grid}
        for c in cols:
            vals = frozenset(v for (r, cc), v in grid.items() if cc == c)
            if len(vals) >= 4:
                col_fps[(key, c)] = vals
                fp_counts[vals] += 1
    recurring = {fp for fp, n in fp_counts.items() if n >= recur_min}

    axis = {}
    for key, grid in grids.items():
        cols = {c for (_, c) in grid}
        axis[key] = {c for c in cols
                     if _is_axis_progression(grid, c) or col_fps.get((key, c)) in recurring}
    return axis


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


def detect_collisions(grids, profile="review", sheets=None):
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
    keys = list(grids.keys())
    axis_cols = _axis_columns(grids)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            (fa, sa), (fb, sb) = keys[i], keys[j]
            ga, gb = grids[keys[i]], grids[keys[j]]
            size_a, size_b = len(ga), len(gb)
            smaller = min(size_a, size_b)
            if smaller < 5:
                continue
            same_file = fa == fb
            # label_a / label_b disambiguate sheets when the pair spans two files
            la = sa if same_file else f"{fa}::{sa}"
            lb = sb if same_file else f"{fb}::{sb}"
            scope = "sheets" if same_file else "files"

            fig_a, fig_b = figure_key(sa), figure_key(sb)
            same_figure = bool(same_file and fig_a and fig_b and fig_a == fig_b)
            context = None
            if same_figure:
                context = (f"both sheets belong to the same display item ({fig_a}); "
                           f"a combined panel and its per-replicate breakdown share data "
                           f"by design, so this overlap is expected, not a cross-experiment reuse")

            same_pos = sum(1 for k, v in ga.items() if k in gb and gb[k] == v)
            vals_a, vals_b = set(ga.values()), set(gb.values())
            same_val = len(vals_a & vals_b)

            ctx_fields = dict(figure_a=fig_a, figure_b=fig_b, same_figure=same_figure,
                              delta=_value_delta(ga, gb))
            if context:
                ctx_fields["context"] = context

            if same_pos >= max(6, smaller * 0.15):
                shared = [(k, v) for k, v in ga.items() if k in gb and gb[k] == v]
                examples = shared[:5]
                label_context_a = _label_context_for_matches((sheets or {}).get(keys[i]), shared)
                label_context_b = _label_context_for_matches((sheets or {}).get(keys[j]), shared)
                fraction_of_smaller = same_pos / smaller
                shared_context = _shared_cross_sheet_context(
                    label_context_a,
                    label_context_b,
                    ctx_fields["delta"]["pattern"],
                    fraction_of_smaller,
                )
                # Shared-axis downgrade: if the bit-identical cells concentrate on a
                # column that is a swept/recurring axis AND the rest diverges, this is a
                # shared x-axis, not cross-experiment reuse. A perfect_dup spans every
                # column (incl. measurements), so it is excluded and stays high.
                pair_axis = axis_cols.get(keys[i], set()) | axis_cols.get(keys[j], set())
                on_axis = sum(1 for (_, c), _ in shared if c in pair_axis)
                non_axis_shared = len(shared) - on_axis
                # Downgrade only when the overlap is essentially confined to axis
                # columns: >=80% of shared cells on an axis AND no more than a couple of
                # stray matches off-axis (absolute backstop, so a wide axis can't drag a
                # real measurement overlap under the ratio). A perfect_dup spans every
                # column and is excluded above.
                axis_overlap = (
                    not same_figure
                    and ctx_fields["delta"]["pattern"] != "perfect_dup"
                    and on_axis >= 0.8 * len(shared)
                    and non_axis_shared <= 3
                )
                if axis_overlap:
                    ctx_fields["axis_overlap"] = True
                    axis_note = ("the bit-identical cells fall on a shared x-axis column "
                                 "(serial-dilution dose, time/frequency sweep, or an index "
                                 "reused across panels), while the measured values differ — "
                                 "a shared axis, not cross-experiment data reuse")
                    ctx_fields["context"] = axis_note
                    ctx_fields["likely_benign"] = axis_note
                if same_figure or axis_overlap:
                    sev = "low"
                else:
                    sev = "high"
                findings.append(dict(
                    kind="cross_sheet_position_identical",
                    file=fa if same_file else f"{fa} + {fb}",
                    file_a=fa, file_b=fb, same_file=same_file,
                    sheet_a=la, sheet_b=lb,
                    size_a=size_a, size_b=size_b,
                    same_position_count=same_pos,
                    fraction_of_smaller=fraction_of_smaller,
                    label_context_a=label_context_a,
                    label_context_b=label_context_b,
                    shared_context=shared_context,
                    examples=[dict(row=k[0] + 1, col=k[1] + 1, value=v) for k, v in examples],
                    severity=sev,
                    **ctx_fields,
                    rule=f"{la} and {lb} share {same_pos}/{smaller} ({same_pos/smaller*100:.0f}%) decimal values at SAME (row,col) across 2 {scope}",
                ))
            elif same_val >= max(8, smaller * 0.4):
                examples = sorted(list(vals_a & vals_b))[:5]
                findings.append(dict(
                    kind="cross_sheet_value_overlap",
                    file=fa if same_file else f"{fa} + {fb}",
                    file_a=fa, file_b=fb, same_file=same_file,
                    sheet_a=la, sheet_b=lb,
                    size_a=size_a, size_b=size_b,
                    shared_value_count=same_val,
                    fraction_of_smaller=same_val / smaller,
                    examples=examples,
                    severity="low" if same_figure else "medium",
                    **ctx_fields,
                    rule=f"{la} and {lb} share {same_val} bit-identical decimal values ({same_val/smaller*100:.0f}% of smaller) across 2 {scope}",
                ))

            tail_min_matches = max(8, min(20, math.ceil(smaller * 0.03)))
            tail_reuse = _detect_decimal_tail_reuse_for_pair(
                ga,
                gb,
                min_matches=tail_min_matches,
            )
            if tail_reuse:
                pairs = tail_reuse["pairs"]
                cells_a = [(ka, va) for ka, _kb, va, _vb, _sig in pairs]
                cells_b = [(kb, vb) for _ka, kb, _va, vb, _sig in pairs]
                label_context_a = _label_context_for_matches((sheets or {}).get(keys[i]), cells_a)
                label_context_b = _label_context_for_matches((sheets or {}).get(keys[j]), cells_b)
                fraction_of_smaller = tail_reuse["tail_match_count"] / smaller
                off_r, off_c = tail_reuse["offset"]
                low_reason = None if same_figure else _decimal_tail_low_reason(pairs)
                note_reason = None
                if same_figure:
                    sev = "low"
                elif low_reason:
                    # Strong benign decimal-tail structures: constant transform,
                    # fixed-denominator rates, or per-column shifts/ratios.
                    sev = "low"
                elif tail_reuse["tail_match_count"] >= 12 or fraction_of_smaller >= 0.10:
                    sev = "high"
                    note_reason = _decimal_tail_note_reason(pairs, (label_context_a, label_context_b))
                else:
                    sev = "medium"
                    note_reason = _decimal_tail_note_reason(pairs, (label_context_a, label_context_b))
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
                    tail_fields["tail_benign_reason"] = tail_benign_reason
                findings.append(dict(
                    kind="cross_sheet_decimal_tail_reuse",
                    file=fa if same_file else f"{fa} + {fb}",
                    file_a=fa, file_b=fb, same_file=same_file,
                    sheet_a=la, sheet_b=lb,
                    size_a=size_a, size_b=size_b,
                    tail_match_count=tail_reuse["tail_match_count"],
                    fraction_of_smaller=fraction_of_smaller,
                    offset_rows=off_r,
                    offset_cols=off_c,
                    min_tail_digits=tail_reuse["min_tail_digits"],
                    skip_decimal_digits=tail_reuse["skip_decimal_digits"],
                    label_context_a=label_context_a,
                    label_context_b=label_context_b,
                    examples=examples,
                    severity=sev,
                    **tail_fields,
                    rule=(
                        f"{la} and {lb} share {tail_reuse['tail_match_count']}/{smaller} "
                        f"changed decimal cells with the same long fractional tail at "
                        f"offset ({off_r}, {off_c}) across 2 {scope}"
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
    scale = max(float(np.max(np.abs(a))), 1e-300)
    diffs = np.diff(a)
    if np.allclose(diffs, diffs[0], atol=1e-9 * scale, rtol=1e-9) and abs(diffs[0]) > 1e-9 * scale:
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


def _fingerprint_values(values):
    digest = hashlib.blake2b(digest_size=20)
    for value in values:
        numerator, denominator = _numeric_ratio(value)
        token = f"{numerator}/{denominator};".encode("ascii")
        digest.update(token)
    return digest.hexdigest()


def _fingerprint_example_value(value):
    if isinstance(value, int) and abs(value) > _MAX_EXACT_FLOAT_INT:
        return value
    return float(value)


def _exact_column_axis_like(exact_values):
    if len(set(exact_values)) <= 1:
        return True
    fractions = [
        Fraction(numerator, denominator)
        for numerator, denominator in exact_values
    ]
    differences = [
        fractions[idx + 1] - fractions[idx]
        for idx in range(len(fractions) - 1)
    ]
    if differences and differences[0] != 0 and all(
        value == differences[0] for value in differences
    ):
        return True
    if all(value != 0 for value in fractions):
        ratios = [
            fractions[idx + 1] / fractions[idx]
            for idx in range(len(fractions) - 1)
        ]
        if ratios and ratios[0] != 1 and all(
            value == ratios[0] for value in ratios
        ):
            return True
    return False


def _column_fingerprints(file, sheet, source, blocks, min_column_length):
    best = {}
    for r0, r1, c0, c1 in blocks:
        header = header_for(source, r0, c0, c1)
        for col_idx in range(c0, c1):
            values = [
                source.exact_numeric(row_idx, col_idx)
                for row_idx in range(r0, r1)
            ]
            values = [value for value in values if value is not None]
            if len(values) < min_column_length:
                continue
            exact_values = [_numeric_ratio(value) for value in values]
            requires_exact_qualification = any(
                isinstance(value, int)
                and abs(value) > _MAX_EXACT_FLOAT_INT
                for value in values
            )
            if requires_exact_qualification:
                axis_like = _exact_column_axis_like(exact_values)
                rounded_distinct = len(set(exact_values))
            else:
                qualified = np.asarray(
                    [float(value) for value in values],
                    dtype=float,
                )
                axis_like = _column_axis_like(qualified)
                rounded_distinct = len({
                    round(float(value), 9)
                    for value in values
                })
            if axis_like:
                continue
            if rounded_distinct < max(6, len(values) // 2):
                continue
            fingerprint = ColumnFingerprint(
                file=file,
                sheet=sheet,
                col_idx=col_idx,
                label=header[col_idx - c0],
                length=len(values),
                digest=_fingerprint_values(values),
                all_int=all(denominator == 1 for _numerator, denominator in exact_values),
                distinct=len(set(exact_values)),
                sample=tuple(values[:5]),
            )
            current = best.get(col_idx)
            if current is None or fingerprint.length > current.length:
                best[col_idx] = fingerprint
    return tuple(best[col_idx] for col_idx in sorted(best))


def build_cross_sheet_summary(
    file,
    sheet,
    source,
    *,
    blocks=None,
    collision_max_rows=200,
    collision_max_cells=200000,
    min_column_length=12,
) -> tuple[CrossSheetSummary, list[InputLimitation]]:
    if blocks is None:
        blocks = find_numeric_blocks(source)
    grid, grid_meta = _grid_from_rows(
        source,
        max_rows=collision_max_rows,
        max_cells=collision_max_cells,
        with_coverage=True,
    )
    label_row_limit = min(source.nrows, collision_max_rows + 3)
    labels = SparseLabelContext(
        nrows=source.nrows,
        ncols=source.ncols,
        text={
            (row_idx, col_idx): value
            for (row_idx, col_idx), value in source._text.items()
            if row_idx < label_row_limit and isinstance(value, str)
        },
    )
    summary = CrossSheetSummary(
        file=file,
        sheet=sheet,
        grid=grid,
        labels=labels,
        columns=_column_fingerprints(
            file,
            sheet,
            source,
            blocks,
            min_column_length,
        ),
    )
    limitations = []
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
                "max_cells": max(0, int(collision_max_cells)),
            },
        ))
    return summary, limitations


def detect_cross_sheet_column_duplicates(grid_sheets, profile="review", min_len=12):
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
        emitted = 0
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                left = group[i]
                right = group[j]
                fa, sa_name, la = left.file, left.sheet, left.label
                fb, sb_name, lb = right.file, right.sheet, right.label
                if (fa, sa_name) == (fb, sb_name):
                    continue                          # different columns, same sheet → identical_column
                fig_a, fig_b = figure_key(sa_name), figure_key(sb_name)
                same_figure = fig_a is not None and fig_a == fig_b
                same_file = fa == fb
                scope = "sheets" if same_file else "files"
                sev = "low" if (same_figure or all_int) else "high"
                findings.append(dict(
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
                ))
                emitted += 1
                if emitted >= 10:                     # cap per bucket
                    break
            if emitted >= 10:
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


def detect_within_sheet_fraction_reuse(grid_sheets, profile="review", min_cells=10):
    """B3 — two numeric blocks in the SAME sheet whose positionally-corresponding cells reproduce
    each other's HIGH-PRECISION decimal fractions while their integer parts differ by whole numbers
    (e.g. two dose-response matrices where every cell shares the 5-decimal fraction but the value
    was shifted by an integer). detect_relations only compares columns within one block and
    detect_collisions only compares distinct sheets, so this matrix-to-matrix within-sheet reuse
    has no other detector. The precision + integer-shift + coverage requirements make chance
    coincidence negligible."""
    findings = []
    for (fname, sname), sheet in grid_sheets.items():
        grids = []
        for (r0, r1, c0, c1) in find_numeric_blocks(sheet):
            cells = {}
            for r in range(r0, r1):
                for c in range(c0, c1):
                    v = sheet.cell(r, c)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        cells[(r - r0, c - c0)] = float(v)
            if len(cells) >= min_cells:
                grids.append(((r0, r1, c0, c1), cells))
        best = None                                            # keep only the strongest pair per sheet
        for i in range(len(grids)):
            for j in range(i + 1, len(grids)):
                (ba, ca), (bb, cb) = grids[i], grids[j]
                common = [k for k in ca if k in cb]
                if len(common) < min_cells:
                    continue
                shared = int_diffs = hp = 0
                fracs, diffset = set(), set()
                for k in common:
                    x, y = ca[k], cb[k]
                    same_fraction = bool(integer_shift_close([x], [y])[0])
                    if same_fraction:
                        shared += 1
                        rounded_diff = round(y - x)
                        if abs(rounded_diff) >= 1:
                            int_diffs += 1
                            diffset.add(rounded_diff)
                        if _sig_frac_digits(x) >= 3:
                            hp += 1
                            fracs.add(round(x - round(x), 6))
                if (shared >= max(min_cells, int(round(0.8 * len(common))))
                        and hp >= max(6, int(round(0.5 * len(common))))
                        and int_diffs >= 3 and len(diffset) >= 2 and len(fracs) >= 5
                        and (best is None or shared > best[0])):
                    best = (shared, ba, bb, len(common))
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
    return findings


def _load_provenance(in_dir, paper):
    """Resolve scan provenance: an explicit `paper` override wins; otherwise read a
    paperconan_source.json sidecar left by `fetch`; otherwise None."""
    if paper:
        return paper
    sidecar = os.path.join(in_dir, "paperconan_source.json")
    if os.path.isfile(sidecar):
        try:
            with open(sidecar, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return {
            key: value
            for key, value in data.items()
            if key != "managed_files"
        }
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
# Wide blocks (dense correlation matrices) can make relation, equal-pair, and row-pair
# coupling detector paths expensive in compute time and output size. Skip those three paths
# above this width while the column-wise detectors still run. 0 disables the skip.
_MAX_BLOCK_COLS = int(os.environ.get("PAPERCONAN_MAX_BLOCK_COLS", "120"))
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
# Directory-wide recurrence budgets. The vector budget preserves the historical
# RecurringRowIndex default while giving scan orchestration a stable control point.
_RECURRING_ROW_VECTOR_BUDGET = 3_000_000
_RECURRING_ROW_VECTOR_MAX_FINDINGS = 20

# Severity rank for deterministic, highest-first truncation when a block is over budget.
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class ScanBudgetState:
    coverage: ScanCoverage
    recurring_index: RecurringRowIndex
    profile: str
    evidence: bool
    findings_kept: int = 0
    findings_omitted: int = 0
    report_blocks_kept: int = 0
    include_runtime: bool = True


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


def _analyze_numeric_blocks(
    sheet, *, file_name, sheet_name, blocks, state
):
    report_blocks = []
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
        wide = _MAX_BLOCK_COLS and (c1 - c0) > _MAX_BLOCK_COLS
        if wide:
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
        row_pair_dimension_limited = (
            not wide
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
        rel = [] if wide else detect_relations(
            sheet, r0, r1, c0, c1, header
        )
        ap = detect_arithmetic_progression(
            sheet, r0, r1, c0, c1, header
        )
        eq = [] if wide else detect_equal_pairs(
            sheet, r0, r1, c0, c1, header
        )
        row_pair_meta = {"findings_omitted": 0}
        if wide or row_pair_dimension_limited:
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
            )
            if isinstance(row_pair_result, tuple):
                rp, row_pair_meta = row_pair_result
            else:
                rp = row_pair_result
        if row_pair_meta["findings_omitted"] > 0:
            state.coverage.add_limitation(
                "block",
                "row_pair_finding_limit",
                file=file_name,
                sheet=sheet_name,
                rows=f"{r0 + 1}-{r1}",
                cols=f"{c0 + 1}-{c1}",
                limit=_ROW_PAIR_MAX_FINDINGS_PER_BLOCK,
                omitted_findings=row_pair_meta["findings_omitted"],
            )
        wc = detect_within_column_patterns(
            sheet, r0, r1, c0, c1, header
        )
        wc += detect_dispersed_repeats(
            sheet, r0, r1, c0, c1, header
        )
        iar = detect_identical_after_rounding(
            sheet, r0, r1, c0, c1, header
        )
        gg = detect_grim_grimmer(
            sheet, r0, r1, c0, c1, header
        )
        if not (rel or ap or eq or rp or wc or iar or gg):
            continue

        sheet_context = " ".join([
            file_name,
            sheet_name,
            *[str(value) for value in header],
        ])
        groups = {
            "relations": rel,
            "progressions": ap,
            "equal_pairs": eq,
            "row_pairs": rp,
            "within_col": wc,
            "identical_after_rounding": iar,
            "grim": gg,
        }
        per_block = (
            _MAX_FINDINGS_PER_BLOCK
            if _MAX_FINDINGS_PER_BLOCK > 0
            else None
        )
        block_cap = per_block
        omitted = _cap_block_findings(groups, block_cap)
        state.findings_omitted += omitted
        if omitted:
            state.coverage.add_limitation(
                "block",
                "finding_limit",
                file=file_name,
                sheet=sheet_name,
                rows=f"{r0 + 1}-{r1}",
                cols=f"{c0 + 1}-{c1}",
                omitted_findings=omitted,
                limit=block_cap,
            )
        state.findings_kept += sum(
            len(group) for group in groups.values()
        )
        evidence_truncated = False
        for group in groups.values():
            if state.evidence:
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
        report_blocks.append({
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
            "findings_omitted": omitted,
        })
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

    within_sheet_findings = detect_within_sheet_fraction_reuse(
        {(file_name, sheet_name): sheet},
        profile=state.profile,
    )

    sheet_numbers = sheet.numeric_values()
    label = f"{file_name}::{sheet_name}"
    digit_reports = []
    digit_report = detect_last_digit(sheet_numbers, label=label)
    if digit_report:
        digit_reports.append(digit_report)
    decimal_reports = []
    decimal_report = detect_repeated_decimals(
        sheet_numbers, label=label
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
    if recurring_meta["windows_skipped"] > 0:
        state.coverage.add_limitation(
            "sheet",
            "recurring_row_vector_budget",
            file=file_name,
            sheet=sheet_name,
            windows_skipped=recurring_meta["windows_skipped"],
            limit=state.recurring_index.initial_budget,
        )

    summary, summary_limitations = build_cross_sheet_summary(
        file_name,
        sheet_name,
        sheet,
        blocks=blocks,
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
        "numeric_cells": len(sheet_numbers),
        "n_blocks": len(blocks),
        "elapsed_ms": _elapsed_ms(sheet_start),
    }
    del sheet_numbers
    return _SheetScanResult(
        report_blocks=report_blocks,
        digit_reports=digit_reports,
        decimal_reports=decimal_reports,
        summaries=[summary],
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

    try:
        load_result = load_table_result(path)
    except Exception as exc:
        print(f"  failed to read {file_name}: {exc}", file=sys.stderr)
        errors.append({"file": file_name, "error": str(exc)})
        state.coverage.mark_file_failed(file_name, "parse_error")
        file_stat["error"] = str(exc)
        file_stat["elapsed_ms"] = _elapsed_ms(file_start)
        return _empty_file_scan_result(file_stat, errors)

    state.coverage.mark_file_succeeded()
    sheets = load_result.sheets
    deferred_cell_limits = {}
    for limitation in load_result.limitations:
        details = limitation.to_dict()
        scope = details.pop("scope")
        reason = details.pop("reason")
        details.pop("file", None)
        sheet_name = details.get("sheet")
        if (
            scope == "sheet"
            and reason == "cell_limit"
            and sheet_name in sheets
            and sheets[sheet_name] is None
        ):
            deferred_cell_limits.setdefault(
                sheet_name,
                {
                    key: value
                    for key, value in details.items()
                    if key != "sheet"
                },
            )
            continue
        state.coverage.add_limitation(
            scope,
            reason,
            file=file_name,
            **details,
        )
    file_stat["n_sheets"] = len(sheets)

    for sheet_name, loaded_sheet in sheets.items():
        sheet_start = (
            time.perf_counter() if state.include_runtime else None
        )
        if loaded_sheet is None:
            msg = (f"oversized sheet exceeds {_MAX_CELLS} cells "
                   f"(set PAPERCONAN_MAX_CELLS to raise) — skipped to bound memory")
            errors.append({
                "file": file_name,
                "sheet": sheet_name,
                "error": msg,
            })
            limitation_details = deferred_cell_limits.pop(sheet_name, None)
            state.coverage.mark_sheet_skipped(
                file_name,
                sheet_name,
                "cell_limit",
                **(
                    limitation_details
                    if limitation_details is not None
                    else {"max_cells": _MAX_CELLS}
                ),
            )
            sheet_stats.append({
                "file": file_name,
                "sheet": sheet_name,
                "oversized": True,
                "elapsed_ms": _elapsed_ms(sheet_start),
            })
            continue

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
            budget=_RECURRING_ROW_VECTOR_BUDGET
        ),
        profile=profile,
        evidence=evidence,
        include_runtime=include_runtime,
    )
    report_blocks = []
    digit_reports = []
    decimal_reports = []
    summaries = []
    within_sheet_fraction_findings = []
    scan_errors = []
    scan_stats = {"files": [], "sheets": []}
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
        del result

    _demote_dense_sheets(report_blocks, profile=profile)
    _demote_reused_progressions(report_blocks, profile=profile)

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
    )
    cross_sheet_findings += detect_cross_sheet_column_duplicates(
        summaries,
        profile=profile,
    )
    cross_sheet_findings += within_sheet_fraction_findings
    recurring_findings, recurring_meta = state.recurring_index.findings(
        profile=profile,
        max_findings=_RECURRING_ROW_VECTOR_MAX_FINDINGS,
    )
    recurring_omitted = recurring_meta["findings_omitted"]
    if recurring_omitted > 0:
        coverage.add_limitation(
            "scan",
            "recurring_row_vector_finding_limit",
            limit=_RECURRING_ROW_VECTOR_MAX_FINDINGS,
            omitted_findings=recurring_omitted,
        )
        state.findings_omitted += recurring_omitted
    cross_sheet_findings += recurring_findings
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
        lines.append(f"## ⚠️ Cross-sheet bit-identical collisions ({len(csf)})\n")
        for cf in csf:
            sev = cf.get("severity", "?")
            lines.append(f"- **[{cf['kind']}]** ({sev}) `{cf['file']}` — {cf['rule']}")
            for ex in cf.get("examples", [])[:3]:
                if isinstance(ex, dict):
                    lines.append(f"    example: row {ex['row']}, col {ex['col']}, value {ex['value']}")
                else:
                    lines.append(f"    shared value: {ex}")
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
