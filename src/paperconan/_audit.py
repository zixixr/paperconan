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
import sys
from collections import Counter
from fractions import Fraction

import openpyxl
import numpy as np
from scipy import stats


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


# ---------- sheet I/O ----------

def load_workbook_rows(path):
    """Return dict of sheet_name -> list[list]."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = {}
    for s in wb.sheetnames:
        ws = wb[s]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
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
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as fh:
                rows = [[_coerce_cell(c) for c in r]
                        for r in _csv.reader(fh, delimiter=delimiter)]
            break
        except UnicodeDecodeError:
            continue
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
    return load_workbook_rows(path)


def find_numeric_blocks(rows, min_rows=3, min_cols=1):
    if not rows:
        return []
    R = len(rows)
    C = max(len(r) for r in rows)
    num = np.zeros((R, C), dtype=bool)
    for i in range(R):
        for j in range(min(C, len(rows[i]))):
            if is_num(rows[i][j]):
                num[i, j] = True
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


def header_for(rows, r0, c0, c1):
    for r in range(r0 - 1, max(-1, r0 - 5), -1):
        if r < 0:
            continue
        line = rows[r][c0:c1]
        texty = [x for x in line if x is not None and not is_num(x)]
        if texty:
            return [str(rows[r][c]).strip() if rows[r][c] is not None else "" for c in range(c0, c1)]
    return [""] * (c1 - c0)


def col_array(rows, r0, r1, c):
    out = []
    for r in range(r0, r1):
        v = rows[r][c] if c < len(rows[r]) else None
        out.append(to_float(v) if is_num(v) else np.nan)
    return np.array(out, dtype=float)


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


def _block_evidence(rows, r0, r1, c0, c1, header, highlight_cols, highlight_rows=None):
    """Slice a numeric block (with 1 row of context above/below if available) into a
    JSON-friendly evidence dict that the HTML renderer can show as a table."""
    r_start = max(0, r0 - 1)
    r_end = min(len(rows), r1 + 1)
    data_rows = []
    for r in range(r_start, r_end):
        vals = [_cell_value(rows[r][c]) if c < len(rows[r]) else None for c in range(c0, c1)]
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


def _attach_evidence(findings, rows, r0, r1, c0, c1, header):
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
        f["evidence"] = _block_evidence(rows, r0, r1, c0, c1, header,
                                        highlight_cols=hi_cols,
                                        highlight_rows=hi_rows)
    return findings


# ---------- detectors ----------

def detect_relations(rows, r0, r1, c0, c1, header):
    findings = []
    cols = [(c, col_array(rows, r0, r1, c)) for c in range(c0, c1)]
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
            if n >= 5 and np.std(x) > 0:
                slope, intercept, r, _p, _se = stats.linregress(x, y)
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


def detect_arithmetic_progression(rows, r0, r1, c0, c1, header):
    findings = []
    for c in range(c0, c1):
        a = col_array(rows, r0, r1, c)
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


def detect_within_column_patterns(rows, r0, r1, c0, c1, header, min_n=6):
    """Detect within-column anomalies:
       - many identical values in one column (Su Jiacao: '13 中 8 个相同')
       - many values sharing same last-2 decimals (Su Jiacao: '13 中 11 个末两位相同')
       - too many .0 / .5 endings (Su Jiacao: '71 个中 51 个末位 0 或 5')
       - missing last digits (Su Jiacao: '70 个数据中末位完全没有 3 或 7')
    """
    findings = []
    for c in range(c0, c1):
        a = col_array(rows, r0, r1, c)
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


def detect_identical_after_rounding(rows, r0, r1, c0, c1, header):
    """Detect pairs/groups of cells that differ at higher precision but match at lower (e.g.
       4.2735 vs 4.2812 — both round to 4.3). Kang Tiebang ED6h/6j signal."""
    findings = []
    cells = []
    for r in range(r0, r1):
        for c in range(c0, c1):
            v = rows[r][c] if c < len(rows[r]) else None
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


def detect_equal_pairs(rows, r0, r1, c0, c1, header):
    """Detect column pairs where many rows have identical values
    (e.g. tumor length == tumor width)."""
    findings = []
    A = np.array([[to_float(rows[r][c]) if c < len(rows[r]) and is_num(rows[r][c]) else np.nan
                   for c in range(c0, c1)] for r in range(r0, r1)], dtype=float)
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

def _grid_from_rows(rows, min_decimal_places=3, max_rows=200):
    """Build {(r, c): rounded_value} of decimal-bearing numeric cells from a rows matrix.
    Only keeps non-integer values with >= min_decimal_places decimals in a sane range —
    these are the values whose bit-identical reuse across tables is suspicious."""
    grid = {}
    for ri, r in enumerate(rows[:max_rows]):
        for ci, v in enumerate(r):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                fv = float(v)
                if fv != int(fv) and 0.001 <= abs(fv) < 100000:
                    s = repr(fv)
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


def detect_collisions(grids):
    """Find pairs of tables (sheets and/or flat files) with many bit-identical decimal
    values at the SAME (row, col). Catches "copy a table, then tweak a few values" fraud,
    whether the copy lives in another sheet of the same workbook or in a separate file.

    `grids` maps (file, sheet) -> grid (from _grid_from_rows). Returns one dict per
    suspicious pair, with file_a/file_b set so same-file and cross-file pairs are
    distinguishable.

    Severity is context-aware: when both sheets resolve to the SAME figure id
    (e.g. exFig.6i ↔ exFig.6k-n), the overlap is the expected combined-vs-individual
    re-plot and is downgraded to "low" with an explanatory `context`. Cross-figure /
    cross-file overlaps (e.g. main Fig 5o ↔ Extended Fig 6b-e) keep their base severity.
    """
    findings = []
    keys = list(grids.keys())
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
                examples = [(k, v) for k, v in ga.items() if k in gb and gb[k] == v][:5]
                findings.append(dict(
                    kind="cross_sheet_position_identical",
                    file=fa if same_file else f"{fa} + {fb}",
                    file_a=fa, file_b=fb, same_file=same_file,
                    sheet_a=la, sheet_b=lb,
                    size_a=size_a, size_b=size_b,
                    same_position_count=same_pos,
                    fraction_of_smaller=same_pos / smaller,
                    examples=[dict(row=k[0] + 1, col=k[1] + 1, value=v) for k, v in examples],
                    severity="low" if same_figure else "high",
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
    return findings


def scan_dir(in_dir, out_dir, *, write_md=False, write_html=True):
    files = sorted({p for pat in ("*.xlsx", "*.csv", "*.tsv")
                    for p in glob.glob(os.path.join(in_dir, pat))})
    if not files:
        sys.exit(f"no .xlsx / .csv / .tsv files in {in_dir}\n"
                 f"(paperconan reads .xlsx, .csv and .tsv; .xls/.xlsm are not supported)")

    report_blocks = []
    per_sheet_numbers = {}
    grids = {}  # (file, sheet) -> decimal grid, for the unified collision pass

    for f in files:
        try:
            sheets = load_table(f)
        except Exception as e:
            print(f"  failed to read {os.path.basename(f)}: {e}", file=sys.stderr)
            continue
        for sn, rows in sheets.items():
            grids[(os.path.basename(f), sn)] = _grid_from_rows(rows)
            sheet_nums = [float(v) for r in rows for v in r if is_num(v)]
            per_sheet_numbers[(os.path.basename(f), sn)] = sheet_nums
            blocks = find_numeric_blocks(rows)
            for (r0, r1, c0, c1) in blocks:
                header = header_for(rows, r0, c0, c1)
                rel = detect_relations(rows, r0, r1, c0, c1, header)
                ap = detect_arithmetic_progression(rows, r0, r1, c0, c1, header)
                eq = detect_equal_pairs(rows, r0, r1, c0, c1, header)
                wc = detect_within_column_patterns(rows, r0, r1, c0, c1, header)
                iar = detect_identical_after_rounding(rows, r0, r1, c0, c1, header)
                if rel or ap or eq or wc or iar:
                    for group in (rel, ap, eq, wc, iar):
                        _attach_evidence(group, rows, r0, r1, c0, c1, header)
                    report_blocks.append(dict(file=os.path.basename(f), sheet=sn,
                                              block=dict(rows=f"{r0+1}-{r1}", cols=f"{c0+1}-{c1}", header=header),
                                              relations=rel, progressions=ap, equal_pairs=eq,
                                              within_col=wc, identical_after_rounding=iar))

    # Unified collision pass: every (file, sheet) grid against every other —
    # covers both intra-workbook sheet pairs and cross-file duplicates.
    cross_sheet_findings = detect_collisions(grids)

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
               input_dir=in_dir,
               n_files=len(files),
               n_blocks_with_findings=len(report_blocks),
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
    ap = argparse.ArgumentParser(description="Scan a paper's source-data xlsx files for fabrication red flags")
    ap.add_argument("in_dir", help="Directory containing the paper's *.xlsx source data files")
    ap.add_argument("--out", default=None, help="Output directory (default: <in_dir>/audit)")
    ap.add_argument("--md", action="store_true",
                    help="Also write REPORT.md (default: only scan.json + report.html)")
    ap.add_argument("--no-html", action="store_true",
                    help="Skip the HTML report (only scan.json, plus REPORT.md if --md)")
    ap.add_argument("--version", action="version", version=f"paperconan {_version()}")
    args = ap.parse_args()
    out_dir = args.out or os.path.join(args.in_dir, "audit")
    write_html = not args.no_html
    res = scan_dir(args.in_dir, out_dir, write_md=args.md, write_html=write_html)
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
