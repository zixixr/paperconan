#!/usr/bin/env python3
"""
paper_audit.py — scan a paper's published source data (xlsx) for data-fabrication red flags.

Usage:
    python3 paper_audit.py <dir-with-xlsx-files> [--out OUT_DIR]

Outputs to <OUT_DIR or <dir>/audit>:
  - scan.json   structured findings (every block, every detector)
  - REPORT.md   ranked top-5 + supporting evidence in markdown

What it detects (red flags for fabricated numeric data):
  1. Identical / constant-offset / constant-ratio / exact-linear column relations
  2. Arithmetic-progression columns (constant first difference)
  3. Repeated last-two-decimal endings beyond chance
  4. Last-digit chi-square (true measurements have ~uniform last digits)
  5. Suspicious row pairs that sum to integer / equal-value column pairs
  6. Reverse-engineered generation rules (col_b = col_a + k, col_b = K - col_a, etc.)

Dependencies: openpyxl, numpy, scipy
"""
from __future__ import annotations
import argparse
import csv as _csv
import datetime
import glob
import json
import math
import os
import re
import sys
import time
from collections import Counter
from fractions import Fraction

import openpyxl
import numpy as np
from scipy import stats

from ._profiles import apply_profile_to_findings, normalize_profile
from ._sheet import Sheet
from .schema import PaperconanInputError


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


def grimmer_consistent(mean, sd, n, mean_decimals, sd_decimals):
    """True if a sample of `n` integers can have both the reported `mean` and the
    reported `sd` (to their stated decimals). Implements the GRIMMER test: for the
    integer total T fixed by the mean, search the integer sum-of-squares values
    whose implied sd rounds to the reported sd, and require one with the correct
    parity (since sum(x^2) == sum(x) mod 2 for integers). Accepts either sample
    (n-1) or population (n) SD convention so an unknown convention never
    false-positives."""
    if n <= 1 or sd < 0:
        return True
    T = round(mean * n)
    half = 0.5 / (10 ** sd_decimals)
    lo_sd = max(0.0, sd - half)
    hi_sd = sd + half
    for ddof in (1, 0):
        denom = n - ddof
        if denom <= 0:
            continue
        corr = (T * T) / n
        ss_lo = lo_sd * lo_sd * denom + corr
        ss_hi = hi_sd * hi_sd * denom + corr
        for ss in range(math.ceil(ss_lo - 1e-9), math.floor(ss_hi + 1e-9) + 1):
            if ss < 0:
                continue
            if (ss % 2) != (T % 2):       # integer parity test
                continue
            if ss + 1e-9 >= corr:          # variance >= 0
                return True
    return False


# ---------- sheet I/O ----------

def load_workbook_rows(path):
    """Return dict of sheet_name -> list[list]. A sheet whose cell count exceeds _MAX_CELLS is
    returned as None (oversized: skipped before materializing, to bound memory)."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = {}
    loaded = 0                                       # cumulative cells across this file's sheets
    for s in wb.sheetnames:
        ws = wb[s]
        mr, mc = ws.max_row, ws.max_column
        # Skip a sheet that is too big on its own, OR once this file's cumulative cell budget is
        # spent (a many-sheet workbook materialized at once OOMs even if each sheet is under cap).
        if loaded >= _MAX_CELLS or (mr and mc and mr * mc > _MAX_CELLS):
            out[s] = None
            continue
        rows = []
        cells = 0
        oversized = False
        for r in ws.iter_rows(values_only=True):
            row = list(r)
            rows.append(row)
            cells += len(row)
            if loaded + cells > _MAX_CELLS:          # per-file cumulative budget — bail mid-stream
                oversized = True
                break
        if oversized:
            out[s] = None
            continue
        loaded += cells
        maxc = max((len(r) for r in rows), default=0)
        for r in rows:
            if len(r) < maxc:
                r.extend([None] * (maxc - len(r)))
        out[s] = rows
    wb.close()
    return out


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
    """Load a delimited text file as {sheet_name: rows}, mirroring load_workbook_rows.
    A flat file has no sheets, so it becomes a single sheet named after the file stem."""
    stem = os.path.splitext(os.path.basename(path))[0]
    rows = []
    oversized = False
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            rows = []
            cells = 0
            with open(path, newline="", encoding=enc) as fh:
                for r in _csv.reader(fh, delimiter=delimiter):
                    rows.append([_coerce_cell(c) for c in r])
                    cells += len(r)
                    if cells > _MAX_CELLS:           # oversized: stop before exhausting memory
                        oversized = True
                        break
            break
        except UnicodeDecodeError:
            continue
    if oversized:
        return {stem: None}
    maxc = max((len(r) for r in rows), default=0)
    for r in rows:
        if len(r) < maxc:
            r.extend([None] * (maxc - len(r)))
    return {stem: rows}


def load_table(path):
    """Dispatch by extension to a {sheet_name: rows} loader."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tsv":
        return load_csv_rows(path, delimiter="\t")
    if ext == ".csv":
        return load_csv_rows(path, delimiter=",")
    if ext == ".pdf":
        from ._extract import load_pdf_tables
        return load_pdf_tables(path)
    if ext == ".docx":
        from ._extract import load_docx_tables
        return load_docx_tables(path)
    return load_workbook_rows(path)


def find_numeric_blocks(sheet, min_rows=3, min_cols=1):
    R, C = sheet.nrows, sheet.ncols
    if R == 0 or C == 0:
        return []
    num = ~np.isnan(sheet.numeric)
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


def _block_evidence(sheet, r0, r1, c0, c1, header, highlight_cols, highlight_rows=None):
    """Slice a numeric block (with 1 row of context above/below if available) into a
    JSON-friendly evidence dict that the HTML renderer can show as a table."""
    r_start = max(0, r0 - 1)
    r_end = min(sheet.nrows, r1 + 1)
    data_rows = []
    for r in range(r_start, r_end):
        vals = [_cell_value(sheet.cell(r, c)) for c in range(c0, c1)]
        data_rows.append({
            "row_idx": r + 1,
            "is_context": r < r0 or r >= r1,
            "values": vals,
        })
    return {
        "headers": list(header),
        "col_offset": c0,
        "highlight_cols": list(highlight_cols),
        "highlight_rows": list(highlight_rows) if highlight_rows else [],
        "rows": data_rows,
    }


def benign_reason(f):
    """Return a common innocent explanation for a finding kind, or None.

    Attached to findings as `likely_benign` so the agent always has the
    false-positive context in hand and the HTML report can show it inline.
    """
    kind = f.get("kind")
    if kind == "arithmetic_progression":
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
    for f in findings:
        hi_cols = []
        for k in ("col_a_idx", "col_b_idx", "col_idx"):
            if k in f and isinstance(f[k], int):
                hi_cols.append(f[k])
        hi_rows = []
        # identical_after_rounding lists specific (row, col) example cells (1-based).
        for ex in f.get("example_cells", []) or []:
            try:
                hi_rows.append(int(ex[0]))
            except (TypeError, ValueError, IndexError):
                pass
        f["evidence"] = _block_evidence(sheet, r0, r1, c0, c1, header,
                                        highlight_cols=hi_cols,
                                        highlight_rows=hi_rows)
    return findings


# ---------- detectors ----------

_GRIM_MEAN_RE = re.compile(r"\b(mean|average|avg)\b|均值|平均", re.I)
_GRIM_SD_RE = re.compile(r"\b(s\.?d\.?|std)\b|标准差", re.I)
_GRIM_N_RE = re.compile(r"\bn\b|sample.?size|样本量|例数", re.I)
_GRIM_INT_RE = re.compile(
    r"count|number|cells|foci|colon|nuclei|score|rating|likert"
    r"|个数|数目|计数|数量|评分|#", re.I)
_GRIM_RATIO_RE = re.compile(
    r"%|percent|percentage|\bratio\b|\brate\b|\bindex\b|proportion|fraction"
    r"|百分|比例|比率|占比|指数", re.I)


def detect_relations(sheet, r0, r1, c0, c1, header):
    findings = []
    cols = [(c, col_array(sheet, r0, r1, c)) for c in range(c0, c1)]
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            ci, ai = cols[i]
            cj, aj = cols[j]
            mask = ~np.isnan(ai) & ~np.isnan(aj)
            n = int(mask.sum())
            if n < 4:
                continue
            x, y = ai[mask], aj[mask]
            # identical
            if np.allclose(x, y, atol=1e-9):
                findings.append(dict(kind="identical_column", col_a=header[ci - c0], col_b=header[cj - c0],
                                     col_a_idx=ci, col_b_idx=cj, n=n, severity="high",
                                     rule=f"col[{cj}] == col[{ci}]"))
                continue
            # constant offset
            diff = y - x
            if np.std(diff) < 1e-9 and abs(np.mean(diff)) > 1e-9:
                findings.append(dict(kind="constant_offset", col_a=header[ci - c0], col_b=header[cj - c0],
                                     col_a_idx=ci, col_b_idx=cj, n=n, offset=float(np.mean(diff)),
                                     severity="high",
                                     rule=f"col[{cj}] = col[{ci}] + {np.mean(diff):.6g}"))
                continue
            # constant ratio
            if np.all(np.abs(x) > 1e-12):
                ratio = y / x
                if np.std(ratio) < 1e-9 and abs(np.mean(ratio) - 1) > 1e-9 and abs(np.mean(ratio)) > 1e-9:
                    findings.append(dict(kind="constant_ratio", col_a=header[ci - c0], col_b=header[cj - c0],
                                         col_a_idx=ci, col_b_idx=cj, n=n, ratio=float(np.mean(ratio)),
                                         severity="high",
                                         rule=f"col[{cj}] = col[{ci}] * {np.mean(ratio):.6g}"))
            # mirror: x + y == constant
            csum = x + y
            if n >= 5 and np.std(csum) < 1e-9:
                K = float(np.mean(csum))
                if abs(K) > 1e-9:
                    findings.append(dict(kind="sum_constant", col_a=header[ci - c0], col_b=header[cj - c0],
                                         col_a_idx=ci, col_b_idx=cj, n=n, sum=K,
                                         severity="high",
                                         rule=f"col[{ci}] + col[{cj}] = {K:.6g}"))
            # exact linear (non-identical)
            if n >= 5 and np.ptp(x) > 1e-12:
                try:
                    slope, intercept, r, _p, _se = stats.linregress(x, y)
                except ValueError:
                    continue
                resid = y - (slope * x + intercept)
                if np.std(y) > 0 and np.std(resid) < 1e-9 and abs(r) > 0.99:
                    if not (abs(slope - 1) < 1e-9 and abs(intercept) < 1e-9):
                        findings.append(dict(kind="exact_linear", col_a=header[ci - c0], col_b=header[cj - c0],
                                             col_a_idx=ci, col_b_idx=cj, n=n,
                                             slope=float(slope), intercept=float(intercept),
                                             severity="high",
                                             rule=f"col[{cj}] = {slope:.4g} * col[{ci}] + {intercept:.4g}"))
            # small discrete diff set
            if n >= 8:
                diff_rounded = np.round(diff, 4)
                uniq = np.unique(diff_rounded)
                if 2 <= len(uniq) <= min(6, n // 3):
                    findings.append(dict(kind="small_diff_set", col_a=header[ci - c0], col_b=header[cj - c0],
                                         col_a_idx=ci, col_b_idx=cj, n=n,
                                         unique_diffs=[float(x) for x in uniq],
                                         severity="medium",
                                         rule=f"col[{cj}] - col[{ci}] only takes {len(uniq)} discrete values"))
    return findings


# Above this many pairwise column relations in ONE block, the sheet is a dense /
# correlated matrix (correlation tables, normalized replicate panels) where identical or
# linear columns are expected by construction — not a duplication red flag. One real
# proteomics sheet produced ~20,000 such 'high' relations, drowning the genuine signal.
RELATION_FLOOD_CAP = 40


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


def _demote_dense_sheets(report_blocks, cap=RELATION_FLOOD_CAP):
    """Apply the dense-flood demotion per (file, sheet), not per block: a dense matrix
    is split into many numeric blocks, each holding only part of the column relations,
    so the flood must be judged by the SHEET total. Mutates findings in place."""
    by_sheet = {}
    for b in report_blocks:
        key = (b["file"], b["sheet"])
        agg = by_sheet.setdefault(key, {"relations": [], "equal_pairs": []})
        agg["relations"].extend(b.get("relations", []))
        agg["equal_pairs"].extend(b.get("equal_pairs", []))
    for agg in by_sheet.values():
        _demote_dense_relations(agg["relations"], cap)   # same dict objects as in blocks
        _demote_dense_relations(agg["equal_pairs"], cap)
    return report_blocks


def detect_arithmetic_progression(sheet, r0, r1, c0, c1, header):
    findings = []
    for c in range(c0, c1):
        a = col_array(sheet, r0, r1, c)
        a = a[~np.isnan(a)]
        if len(a) < 5:
            continue
        diffs = np.diff(a)
        if np.allclose(diffs, diffs[0], atol=1e-9) and abs(diffs[0]) > 1e-9:
            sev = "medium" if abs(diffs[0] - round(diffs[0])) < 1e-9 else "high"
            findings.append(dict(kind="arithmetic_progression", col=header[c - c0], col_idx=c,
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

        # 1) duplicate values within the column
        vals_rounded = np.round(a_clean, 4)
        counts = Counter(vals_rounded.tolist())
        top_val, top_count = counts.most_common(1)[0]
        if top_count >= max(4, n // 2) and n - top_count >= 1:
            findings.append(dict(kind="within_col_value_duplication",
                                 col=col_name, col_idx=c, n=n,
                                 dup_value=float(top_val), dup_count=int(top_count),
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
    suspicious = []
    for k, lst in buckets.items():
        if len(lst) >= 4:
            uniq = set(round(v, 4) for _, _, v in lst)
            if len(uniq) >= 3:
                suspicious.append((k, lst))
    suspicious.sort(key=lambda kv: -len(kv[1]))
    if suspicious:
        top = suspicious[:5]
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
    data at the stated n. Strictly gated — needs a header-located mean+n triple
    AND a count/score keyword in the MEAN column header signalling integer items —
    to stay false-positive-safe on continuous measurements where GRIM does not apply.
    GRIMMER runs only on a true SD column (SEM/SE columns are deliberately ignored,
    since GRIMMER is undefined for a standard error)."""
    findings = []

    def _find(rx, taken):
        for idx, h in enumerate(header):
            if idx not in taken and rx.search(str(h or "")):
                return idx
        return None

    taken = set()
    mean_i = _find(_GRIM_MEAN_RE, taken)
    if mean_i is not None:
        taken.add(mean_i)
    n_i = _find(_GRIM_N_RE, taken)
    if n_i is not None:
        taken.add(n_i)
    sd_i = _find(_GRIM_SD_RE, taken)
    if mean_i is None or n_i is None:
        return findings
    # Integer-data gate: the count/score keyword must be in the MEAN column header
    # itself, not anywhere in the row — otherwise a bookkeeping column such as
    # "number of replicates" would license GRIM on a continuous measurement.
    if not _GRIM_INT_RE.search(str(header[mean_i] or "")):
        return findings
    # Negative gate: a continuous ratio / percentage / index mean is not integer
    # data even when its header also contains a count word (e.g. "% positive cells").
    # NB: deliberately excludes "score"/"count" — GRIM's original domain is integer
    # composite/Likert scores, which must still be checked.
    if _GRIM_RATIO_RE.search(str(header[mean_i] or "")):
        return findings

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
    A = sheet.block(r0, r1, c0, c1)
    for i in range(c1 - c0):
        for j in range(i + 1, c1 - c0):
            a, b = A[:, i], A[:, j]
            mask = ~np.isnan(a) & ~np.isnan(b)
            n = int(mask.sum())
            if n < 6:
                continue
            eq = int((np.isclose(a[mask], b[mask], atol=1e-6)).sum())
            if eq >= max(6, n // 2) and eq / n >= 0.5 and not np.allclose(a[mask], b[mask], atol=1e-9):
                findings.append(dict(kind="many_equal_pairs", col_a=header[i], col_b=header[j],
                                     col_a_idx=c0 + i, col_b_idx=c0 + j, n=n, equal=eq,
                                     severity="medium" if eq < n else "high",
                                     rule=f"col[{c0+i}] == col[{c0+j}] in {eq}/{n} rows"))
    return findings


# ---------- driver ----------

def _grid_from_rows(sheet, min_decimal_places=3, max_rows=200):
    """Build {(r, c): rounded_value} of decimal-bearing numeric cells from a Sheet.
    Only keeps non-integer values with >= min_decimal_places decimals in a sane range —
    these are the values whose bit-identical reuse across tables is suspicious."""
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


import re as _re

# Matches a figure id inside a sheet name: an optional "extended/ED/ex" marker
# followed by a figure number, e.g. "Figure 5o", "exFig.6b-e", "ED_Fig8b", " exFig.6i".
_FIG_RE = _re.compile(r"(ext(?:ended)?|ed|ex)?\s*\.?\s*fig(?:ure)?\s*\.?\s*0*(\d+)", _re.I)


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
    told apart from a copy-then-tweak.

    - modified_cells: same (row,col) position, different value — only meaningful when
      the two tables share a layout; the copy-then-tweak fingerprint.
    - only_in_a / only_in_b: value-multiset members unique to each side (layout-robust).
    - pattern:
        value_tweaked : >=1 cell changed in place (most interesting — possible edit)
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


def detect_collisions(grids, profile="review"):
    """Find pairs of tables (sheets and/or flat files) with many bit-identical decimal
    values at the SAME (row, col). Catches "copy a table, then tweak a few values" fraud,
    whether the copy lives in another sheet of the same workbook or in a separate file.

    `grids` maps (file, sheet) -> grid (from _grid_from_rows). Returns one dict per
    suspicious pair, with file_a/file_b set so same-file and cross-file pairs are
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
            same_figure = bool(fig_a and fig_b and fig_a == fig_b)
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
                    fraction_of_smaller=same_pos / smaller,
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
                return json.load(fh)
        except (OSError, ValueError):
            return None
    return None


# Per-file memory guard: workbooks above this size expand to many GB of Python objects
# when fully materialized, so they are skipped (recorded as oversized) before loading.
# Coarse byte backstop (generous — the precise guard is the cell-count cap below).
_MAX_FILE_MB = float(os.environ.get("PAPERCONAN_MAX_FILE_MB", "200"))
_MAX_FILE_BYTES = int(_MAX_FILE_MB * 1024 * 1024)
# Precise memory guard: each cell expands to ~100-200 bytes as a Python object, so a dense
# matrix OOMs regardless of file *size*. Skip a sheet whose cell count exceeds this, checked
# from the sheet dimensions BEFORE materializing. Default 2M cells ≈ a 2000×1000 table.
_MAX_CELLS = int(os.environ.get("PAPERCONAN_MAX_CELLS", "2000000"))
# Wide blocks (dense correlation matrices) blow up the O(col²) relation/equal-pair detectors in
# both compute time and output size (scan.json / report.html). Skip just those two detectors when
# a block is wider than this; the cheap column-wise detectors still run. 0 disables the skip.
_MAX_BLOCK_COLS = int(os.environ.get("PAPERCONAN_MAX_BLOCK_COLS", "120"))
# Output cap: each finding embeds a table-snippet as evidence, so a paper with thousands of
# findings balloons scan.json to many GB. Stop collecting blocks once this many have findings.
_MAX_REPORT_BLOCKS = int(os.environ.get("PAPERCONAN_MAX_REPORT_BLOCKS", "2000"))


def scan_dir(in_dir, out_dir, *, write_md=False, write_html=True, paper=None,
             profile="review"):
    profile = normalize_profile(profile)
    files = sorted({p for pat in ("*.xlsx", "*.csv", "*.tsv", "*.pdf", "*.docx")
                    for p in glob.glob(os.path.join(in_dir, pat))})
    if not files:
        raise PaperconanInputError(
            f"no .xlsx / .csv / .tsv / .pdf / .docx files in {in_dir}\n"
            f"(paperconan reads .xlsx, .csv, .tsv, and tables inside .pdf / .docx; "
            f".xls/.xlsm are not supported)"
        )

    report_blocks = []
    per_sheet_numbers = {}
    grids = {}  # (file, sheet) -> decimal grid, for the unified collision pass
    scan_errors = []
    scan_stats = {"files": [], "sheets": []}
    scan_start = time.perf_counter()

    for f in files:
        file_start = time.perf_counter()
        file_stat = {"file": os.path.basename(f), "path": f}
        # Memory guard: a large workbook expands to many GB of Python objects when fully
        # loaded, so cap file size BEFORE loading. Oversized files are recorded (never
        # silently treated as clean) and skipped. Raise PAPERCONAN_MAX_FILE_MB on big-RAM hosts.
        try:
            fsize = os.path.getsize(f)
        except OSError:
            fsize = 0
        if fsize > _MAX_FILE_BYTES:
            msg = (f"oversized: {fsize / 1048576:.1f}MB exceeds {_MAX_FILE_MB:.0f}MB cap "
                   f"(set PAPERCONAN_MAX_FILE_MB to raise) — skipped to bound memory")
            print(f"  skipping {os.path.basename(f)}: {msg}", file=sys.stderr)
            scan_errors.append({"file": os.path.basename(f), "error": msg})
            file_stat["error"] = msg
            file_stat["oversized"] = True
            file_stat["elapsed_ms"] = round((time.perf_counter() - file_start) * 1000, 3)
            scan_stats["files"].append(file_stat)
            continue
        try:
            sheets = load_table(f)
        except Exception as e:
            print(f"  failed to read {os.path.basename(f)}: {e}", file=sys.stderr)
            scan_errors.append({"file": os.path.basename(f), "error": str(e)})
            file_stat["error"] = str(e)
            file_stat["elapsed_ms"] = round((time.perf_counter() - file_start) * 1000, 3)
            scan_stats["files"].append(file_stat)
            continue
        file_stat["n_sheets"] = len(sheets)
        file_stat["elapsed_ms"] = round((time.perf_counter() - file_start) * 1000, 3)
        scan_stats["files"].append(file_stat)
        for sn, rows in sheets.items():
            sheet_start = time.perf_counter()
            if rows is None:        # oversized sheet (>_MAX_CELLS): recorded, never audited
                msg = (f"oversized sheet exceeds {_MAX_CELLS} cells "
                       f"(set PAPERCONAN_MAX_CELLS to raise) — skipped to bound memory")
                scan_errors.append({"file": os.path.basename(f), "sheet": sn, "error": msg})
                scan_stats["sheets"].append({
                    "file": os.path.basename(f), "sheet": sn, "oversized": True,
                    "elapsed_ms": round((time.perf_counter() - sheet_start) * 1000, 3)})
                continue
            sheet = rows if isinstance(rows, Sheet) else Sheet.from_rows(rows)
            grids[(os.path.basename(f), sn)] = _grid_from_rows(sheet)
            sheet_nums = sheet.numeric_values()
            per_sheet_numbers[(os.path.basename(f), sn)] = sheet_nums
            blocks = find_numeric_blocks(sheet)
            max_cols = sheet.ncols
            scan_stats["sheets"].append({
                "file": os.path.basename(f),
                "sheet": sn,
                "n_rows": sheet.nrows,
                "n_cols": max_cols,
                "numeric_cells": len(sheet_nums),
                "n_blocks": len(blocks),
                "elapsed_ms": round((time.perf_counter() - sheet_start) * 1000, 3),
            })
            for (r0, r1, c0, c1) in blocks:
                if len(report_blocks) >= _MAX_REPORT_BLOCKS:   # output budget reached; stop collecting
                    break
                header = header_for(sheet, r0, c0, c1)
                # On very wide blocks (dense correlation matrices) the O(col²) relation and
                # equal-pair detectors explode in compute + output, so skip just those two; the
                # column-wise detectors below still run. (_MAX_BLOCK_COLS=0 disables the skip.)
                wide = _MAX_BLOCK_COLS and (c1 - c0) > _MAX_BLOCK_COLS
                rel = [] if wide else detect_relations(sheet, r0, r1, c0, c1, header)
                ap = detect_arithmetic_progression(sheet, r0, r1, c0, c1, header)
                eq = [] if wide else detect_equal_pairs(sheet, r0, r1, c0, c1, header)
                wc = detect_within_column_patterns(sheet, r0, r1, c0, c1, header)
                iar = detect_identical_after_rounding(sheet, r0, r1, c0, c1, header)
                gg = detect_grim_grimmer(sheet, r0, r1, c0, c1, header)
                if rel or ap or eq or wc or iar or gg:
                    sheet_context = " ".join([os.path.basename(f), sn, *[str(h) for h in header]])
                    for group in (rel, ap, eq, wc, iar, gg):
                        _attach_evidence(group, sheet, r0, r1, c0, c1, header)
                        _attach_benign(group)
                        apply_profile_to_findings(group, profile,
                                                  sheet_context=sheet_context)
                    report_blocks.append(dict(file=os.path.basename(f), sheet=sn,
                                              block=dict(rows=f"{r0+1}-{r1}", cols=f"{c0+1}-{c1}", header=header),
                                              relations=rel, progressions=ap, equal_pairs=eq,
                                              within_col=wc, identical_after_rounding=iar,
                                              grim=gg))

    # Down-weight dense/correlated sheets: judged by per-sheet relation totals, so a
    # wide matrix's expected identical/linear columns don't flood high-severity output.
    _demote_dense_sheets(report_blocks)

    # Unified collision pass: every (file, sheet) grid against every other —
    # covers both intra-workbook sheet pairs and cross-file duplicates.
    cross_sheet_findings = detect_collisions(grids, profile=profile)
    _attach_benign(cross_sheet_findings)

    digit_reports, decimal_reports = [], []
    for key, nums in per_sheet_numbers.items():
        d = detect_last_digit(nums, label=f"{key[0]}::{key[1]}")
        if d:
            digit_reports.append(d)
        dec = detect_repeated_decimals(nums, label=f"{key[0]}::{key[1]}")
        if dec:
            decimal_reports.append(dec)

    # Multiple-testing control: dozens of per-sheet χ² tests run at once, so a raw
    # p-threshold over-reports. Attach a BH-adjusted q-value + significance flag.
    if digit_reports:
        adj, sig = benjamini_hochberg([d["p"] for d in digit_reports], alpha=0.05)
        for d, a, s in zip(digit_reports, adj, sig):
            d["p_adj"] = a
            d["fdr_significant"] = bool(s)

    out = dict(tool="paperconan",
               tool_version=_version(),
               scanned_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               profile=profile,
               input_dir=in_dir,
               paper=_load_provenance(in_dir, paper),
               n_files=len(files),
               n_blocks_with_findings=len(report_blocks),
               scan_errors=scan_errors,
               scan_stats={**scan_stats,
                           "elapsed_ms": round((time.perf_counter() - scan_start) * 1000, 3)},
               relations_blocks=report_blocks,
               digit_distribution=digit_reports,
               decimal_endings=decimal_reports,
               cross_sheet_findings=cross_sheet_findings)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "scan.json"), "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    if write_md:
        write_markdown_report(out, os.path.join(out_dir, "REPORT.md"))
    if write_html:
        from ._html import write_html_report
        write_html_report(out, os.path.join(out_dir, "report.html"))
    return out


def write_markdown_report(out, path):
    lines = ["# Paper data audit report\n",
             f"- Input: `{out['input_dir']}`",
             f"- Files scanned: {out['n_files']}",
             f"- Blocks with findings: {out['n_blocks_with_findings']}\n"]

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
        for r in b["progressions"]:
            push(b, r)
        for r in b.get("within_col", []):
            push(b, r)
        for r in b.get("identical_after_rounding", []):
            push(b, r)
        for r in b.get("grim", []):
            push(b, r)

    csf = out.get("cross_sheet_findings", [])
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
    ap = argparse.ArgumentParser(description="Scan a paper's source data (xlsx/csv/tsv, or tables inside pdf/docx) for fabrication red flags")
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
    ap.add_argument("--version", action="version", version=f"paperconan {_version()}")
    args = ap.parse_args()
    out_dir = args.out or os.path.join(args.in_dir, "audit")
    write_html = not args.no_html
    paper = None
    if args.doi or args.title:
        paper = {"doi": args.doi, "title": args.title}
    try:
        res = scan_dir(args.in_dir, out_dir, write_md=args.md, write_html=write_html,
                       paper=paper, profile=args.profile)
    except PaperconanInputError as e:
        sys.exit(str(e))
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
