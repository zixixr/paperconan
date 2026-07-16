from __future__ import annotations

from collections import Counter
import math
import os
import re
import sys

import numpy as np

from ._profiles import apply_profile_to_findings
from ._summaries import _vector_is_patterned


_AXIS_CONTEXT_LABEL_RE = re.compile(
    r"\b(?:time|day|dose|conc(?:entration)?|wavelength|m/z|mz|position|chr|"
    r"coordinate|coord|index|bin)\b|波长|时间|剂量",
    re.I,
)

_SHORT_ROW_MIN_COLS = int(
    os.environ.get("PAPERCONAN_SHORT_ROW_MIN_COLS", "3")
)
_SHORT_ROW_MIN_SIGFIGS = int(
    os.environ.get("PAPERCONAN_SHORT_ROW_MIN_SIGFIGS", "5")
)
_SHORT_ROW_RTOL = float(
    os.environ.get("PAPERCONAN_SHORT_ROW_RTOL", "1e-4")
)
_SHORT_ROW_MAX_ROWS_PER_SHEET = int(
    os.environ.get("PAPERCONAN_SHORT_ROW_MAX_ROWS", "400")
)
_SHORT_ROW_MAX_VALUE_FREQ = int(
    os.environ.get("PAPERCONAN_SHORT_ROW_MAX_VALUE_FREQ", "8")
)

_WITHIN_ROW_FRAC_MIN_DIGITS = int(
    os.environ.get("PAPERCONAN_WITHIN_ROW_FRAC_MIN_DIGITS", "6")
)
_ROW_PAIR_FRAC_MIN_DIGITS = int(
    os.environ.get("PAPERCONAN_ROW_PAIR_FRAC_MIN_DIGITS", "4")
)
_ROW_PAIR_MIN_RUN = int(
    os.environ.get("PAPERCONAN_ROW_PAIR_MIN_RUN", "3")
)
_ROW_PAIR_MAX_ROWS_PER_SHEET = int(
    os.environ.get("PAPERCONAN_ROW_PAIR_MAX_ROWS", "400")
)

_TAIL_CLUSTER_MIN_N = int(
    os.environ.get("PAPERCONAN_TAIL_CLUSTER_MIN_N", "100")
)
_TAIL_CLUSTER_SHARE = float(
    os.environ.get("PAPERCONAN_TAIL_CLUSTER_SHARE", "0.40")
)


def _row_label(sheet, row, first_numeric_col):
    labels = []
    for col in range(max(0, first_numeric_col - 4), first_numeric_col):
        value = sheet.cell(row, col)
        if value is None or isinstance(value, (int, float, bool)):
            continue
        text = str(value).strip()
        if text:
            labels.append(text)
    return " | ".join(labels) if labels else f"row {row + 1}"


def _sample(values, limit=8):
    return [float(value) for value in values[:limit]]


def _longest_constant_ratio_run(a, b, rtol):
    best_len = 0
    best_start = 0
    current_len = 0
    current_ratio = None
    current_start = 0
    for index, (left, right) in enumerate(zip(a, b)):
        if math.isnan(left) or math.isnan(right) or abs(left) <= 1e-12:
            current_len = 0
            current_ratio = None
            continue
        ratio = right / left
        if (
            current_ratio is None
            or abs(ratio - current_ratio)
            > rtol * max(abs(current_ratio), 1e-300)
        ):
            current_ratio = ratio
            current_len = 1
            current_start = index
        else:
            current_len += 1
        if (
            current_len > best_len
            and abs(current_ratio - 1.0) > rtol
        ):
            best_len = current_len
            best_start = current_start
    if best_len == 0:
        return None
    x_run = a[best_start:best_start + best_len].astype(float)
    y_run = b[best_start:best_start + best_len].astype(float)
    ratio = float(np.mean(y_run / x_run))
    if abs(ratio - 1.0) <= rtol or abs(ratio) <= 1e-9:
        return None
    return ratio, best_len, x_run


def detect_row_relations(
    sheet,
    r0,
    r1,
    c0,
    c1,
    *,
    max_rows,
    min_cols,
    rtol,
    work_budget,
):
    findings = []
    row_count = r1 - r0
    col_count = c1 - c0
    if row_count < 2 or col_count < min_cols or row_count > max_rows:
        return findings

    remaining = max(0, int(work_budget))
    labels = {
        row: _row_label(sheet, row, c0)
        for row in range(r0, r1)
    }
    for row_a in range(r0, r1):
        label_a = labels[row_a]
        if _AXIS_CONTEXT_LABEL_RE.search(label_a):
            continue
        a = sheet.numeric[row_a, c0:c1]
        for row_b in range(row_a + 1, r1):
            remaining -= col_count
            if remaining <= 0:
                print(
                    "[paperconan] detect_row_relations: "
                    "column-operation budget exhausted",
                    file=sys.stderr,
                )
                return findings
            label_b = labels[row_b]
            if _AXIS_CONTEXT_LABEL_RE.search(label_b):
                continue
            b = sheet.numeric[row_b, c0:c1]
            mask = ~np.isnan(a) & ~np.isnan(b)
            paired = int(mask.sum())
            if paired < min_cols:
                continue
            x = a[mask].astype(float)
            y = b[mask].astype(float)
            if np.ptp(x) <= 0 or len(np.unique(x)) < 6:
                continue
            sample_a = _sample(x)
            sample_b = _sample(y)
            if np.all(np.isclose(x, y, rtol=1e-10, atol=0.0)):
                findings.append({
                    "kind": "identical_row",
                    "row_a": label_a,
                    "row_b": label_b,
                    "row_a_idx": row_a,
                    "row_b_idx": row_b,
                    "n": paired,
                    "severity": "high",
                    "row_a_sample": sample_a,
                    "row_b_sample": sample_b,
                    "rule": (
                        f"row[{row_b + 1}] == row[{row_a + 1}] "
                        f"over {paired} columns"
                    ),
                })
                continue
            run = _longest_constant_ratio_run(a, b, rtol)
            if run is None:
                continue
            ratio, run_length, x_run = run
            if run_length < min_cols or len(np.unique(x_run)) < 6:
                continue
            findings.append({
                "kind": "constant_ratio_row",
                "row_a": label_a,
                "row_b": label_b,
                "row_a_idx": row_a,
                "row_b_idx": row_b,
                "n": int(run_length),
                "ratio": ratio,
                "run_length": int(run_length),
                "severity": "high",
                "row_a_sample": sample_a,
                "row_b_sample": sample_b,
                "rule": (
                    f"row[{row_b + 1}] = row[{row_a + 1}] * "
                    f"{ratio:.6g} over a run of "
                    f"{int(run_length)}/{paired} columns"
                ),
            })
    return findings


def detect_decimal_tail_clustering(values, label, top_k=6):
    tails = []
    full_fractions = []
    high_precision_values = []
    for value in values:
        if isinstance(value, int) and abs(value) >= 10_000_000:
            continue
        try:
            magnitude = abs(float(value))
        except (OverflowError, TypeError, ValueError):
            continue
        if not math.isfinite(magnitude) or magnitude >= 1e7:
            continue
        text = f"{magnitude:.10f}".rstrip("0")
        if "." not in text:
            continue
        fraction = text.split(".", 1)[1]
        if len(fraction) < 3:
            continue
        tails.append(fraction[-3:])
        full_fractions.append(fraction)
        high_precision_values.append(magnitude)
    count = len(tails)
    if count < _TAIL_CLUSTER_MIN_N:
        return None
    distinct_fractions = len(set(full_fractions))
    if distinct_fractions < max(50, count // 2):
        return None
    top = Counter(tails).most_common(top_k)
    top_count = sum(item_count for _tail, item_count in top)
    share = top_count / count
    if share < _TAIL_CLUSTER_SHARE:
        return None
    top_tails = [tail for tail, _item_count in top]
    top_tail_set = set(top_tails)
    carriers = [
        value
        for value, tail in zip(high_precision_values, tails)
        if tail in top_tail_set
    ]
    for divisor in range(2, 13):
        terminating = sum(
            1
            for value in carriers
            if abs(value * divisor - round(value * divisor, 4)) < 1e-6
        )
        if carriers and terminating >= 0.9 * len(carriers):
            return None
    complementary_pairs = sum(
        1
        for tail in top_tails
        if int(tail) < 500
        and f"{1000 - int(tail):03d}" in top_tail_set
    )
    return {
        "label": label,
        "n": count,
        "n_unique": len(set(tails)),
        "n_distinct_fraction": distinct_fractions,
        "top": [[tail, item_count] for tail, item_count in top],
        "top_share": round(share, 4),
        "complementary_pairs": complementary_pairs,
        "severity": "high",
        "rule": (
            f"the {top_k} most common 3-digit fractional tails cover "
            f"{top_count}/{count} ({share:.0%}) of the high-precision "
            f"values, which have {distinct_fractions} distinct "
            "fractional parts"
        ),
    }


def _longest_identical_run(a, b):
    best_len = 0
    best_start = 0
    current_len = 0
    current_start = 0
    for index, (left, right) in enumerate(zip(a, b)):
        if (
            math.isnan(left)
            or math.isnan(right)
            or abs(left - right)
            > 1e-9 * max(abs(left), abs(right), 1e-300)
        ):
            current_len = 0
            continue
        if current_len == 0:
            current_start = index
        current_len += 1
        if current_len > best_len:
            best_len = current_len
            best_start = current_start
    if best_len == 0:
        return None
    return best_len, a[best_start:best_start + best_len].astype(float)


def _row_bands(sheet, min_cols):
    data_rows = [
        int(np.count_nonzero(~np.isnan(sheet.numeric[row, :]))) >= min_cols
        for row in range(sheet.nrows)
    ]
    bands = []
    start = None
    for row, is_data in enumerate(data_rows):
        if is_data and start is None:
            start = row
        elif not is_data and start is not None:
            bands.append((start, row))
            start = None
    if start is not None:
        bands.append((start, sheet.nrows))
    return bands


def _scaled_row_candidates(grid_sheets, min_cols, max_rows):
    candidates = []
    for (file_name, sheet_name), sheet in grid_sheets.items():
        for band_index, (r0, r1) in enumerate(
            _row_bands(sheet, min_cols)
        ):
            if r1 - r0 < 2 or r1 - r0 > max_rows:
                continue
            for row in range(r0, r1):
                values = sheet.numeric[row, :]
                finite = values[~np.isnan(values)]
                if (
                    len(finite) < min_cols
                    or np.ptp(finite) <= 0
                    or len(np.unique(finite)) < 6
                    or _vector_is_patterned(list(finite))
                ):
                    continue
                label = _row_label(sheet, row, 1)
                if _AXIS_CONTEXT_LABEL_RE.search(label):
                    continue
                candidates.append({
                    "file": file_name,
                    "sheet": sheet_name,
                    "band": (file_name, sheet_name, band_index),
                    "rows": (r0, r1),
                    "row": row,
                    "label": label,
                    "values": values,
                })
    return candidates


def detect_scaled_row_reuse(
    grid_sheets,
    *,
    profile,
    figure_key,
    row_relation_min_cols,
    row_relation_max_rows,
    row_relation_rtol,
    max_candidates=1500,
    max_findings=40,
):
    candidates = _scaled_row_candidates(
        grid_sheets,
        row_relation_min_cols,
        row_relation_max_rows,
    )
    truncated = len(candidates) > max_candidates
    candidates = candidates[:max_candidates]
    findings = []
    remaining = 4_000_000
    for left_index, left in enumerate(candidates):
        for right in candidates[left_index + 1:]:
            if left["band"] == right["band"]:
                continue
            left_values = left["values"]
            right_values = right["values"]
            width = min(len(left_values), len(right_values))
            remaining -= width
            if remaining <= 0:
                break
            identical = _longest_identical_run(
                left_values[:width], right_values[:width]
            )
            ratio = _longest_constant_ratio_run(
                left_values[:width],
                right_values[:width],
                row_relation_rtol,
            )
            identical_ok = (
                identical is not None
                and identical[0] >= row_relation_min_cols
                and len(np.unique(identical[1])) >= 6
            )
            ratio_ok = (
                ratio is not None
                and ratio[1] >= row_relation_min_cols
                and len(np.unique(ratio[2])) >= 6
            )
            if identical_ok and (
                not ratio_ok or identical[0] >= ratio[1]
            ):
                kind = "identical_row_reuse"
                coefficient = 1.0
                run_length = identical[0]
                run_values = identical[1]
            elif ratio_ok:
                kind = "scaled_row_reuse"
                coefficient = ratio[0]
                run_length = ratio[1]
                run_values = ratio[2]
            else:
                continue
            file_a = left["file"]
            file_b = right["file"]
            sheet_a = left["sheet"]
            sheet_b = right["sheet"]
            same_file = file_a == file_b
            same_sheet = same_file and sheet_a == sheet_b
            figure_a = figure_key(sheet_a)
            figure_b = figure_key(sheet_b)
            findings.append({
                "kind": kind,
                "file": file_a if same_file else f"{file_a} + {file_b}",
                "file_a": file_a,
                "file_b": file_b,
                "same_file": same_file,
                "same_sheet": same_sheet,
                "sheet_a": sheet_a,
                "sheet_b": sheet_b,
                "row_a": left["label"],
                "row_b": right["label"],
                "size_a": run_length,
                "size_b": run_length,
                "same_position_count": run_length,
                "fraction_of_smaller": 1.0,
                "ratio": coefficient,
                "run_length": run_length,
                "figure_a": figure_a,
                "figure_b": figure_b,
                "same_figure": (
                    figure_a is not None and figure_a == figure_b
                ),
                "delta": {
                    "pattern": (
                        "identical_row"
                        if kind == "identical_row_reuse"
                        else "scaled_row"
                    )
                },
                "block_a": (
                    f"rows {left['rows'][0] + 1}-{left['rows'][1]}"
                ),
                "block_b": (
                    f"rows {right['rows'][0] + 1}-{right['rows'][1]}"
                ),
                "examples": [
                    {
                        "row": left["label"],
                        "col": None,
                        "value": float(value),
                    }
                    for value in run_values[:5]
                ],
                "severity": "high",
                "rule": (
                    f"rows '{left['label']}' and '{right['label']}' "
                    f"have an exact {kind} relationship over "
                    f"{run_length} aligned columns"
                ),
            })
            if len(findings) >= max_findings:
                break
        if remaining <= 0 or len(findings) >= max_findings:
            break
    if truncated or remaining <= 0:
        print(
            "[paperconan] detect_scaled_row_reuse: coverage bounded",
            file=sys.stderr,
        )
    apply_profile_to_findings(findings, profile)
    return findings


def _sigfigs_and_frac(value):
    magnitude = abs(float(value))
    if not math.isfinite(magnitude) or magnitude == 0.0:
        return 0, 0
    text = f"{magnitude:.10f}".rstrip("0")
    integer, _dot, fraction = text.partition(".")
    significant = (
        len(fraction.lstrip("0"))
        if integer == "0"
        else len(integer) + len(fraction)
    )
    return significant, len(fraction)


def _is_short_hp(value):
    if math.isnan(value):
        return False
    significant, fractional = _sigfigs_and_frac(value)
    return (
        fractional >= 3
        and significant >= _SHORT_ROW_MIN_SIGFIGS
    )


def _near_power_of_ten(coefficient):
    magnitude = abs(float(coefficient))
    if magnitude <= 1e-300 or not math.isfinite(magnitude):
        return False
    exponent = round(math.log10(magnitude))
    expected = 10.0 ** exponent
    return abs(magnitude - expected) <= _SHORT_ROW_RTOL * expected


def _longest_hp_identical_run(a, b):
    best_len = 0
    best_start = 0
    current_len = 0
    current_start = 0
    for index, (left, right) in enumerate(zip(a, b)):
        if (
            not _is_short_hp(left)
            or not _is_short_hp(right)
            or abs(left - right)
            > 1e-9 * max(abs(left), abs(right), 1e-300)
        ):
            current_len = 0
            continue
        if current_len == 0:
            current_start = index
        current_len += 1
        if current_len > best_len:
            best_len = current_len
            best_start = current_start
    if best_len == 0:
        return None
    return best_len, a[best_start:best_start + best_len].astype(float)


def _longest_hp_ratio_run(a, b):
    best_len = 0
    best_start = 0
    best_ratio = None
    current_len = 0
    current_start = 0
    current_ratio = None
    for index, (left, right) in enumerate(zip(a, b)):
        if (
            not _is_short_hp(left)
            or not _is_short_hp(right)
            or abs(left) <= 1e-12
        ):
            current_len = 0
            current_ratio = None
            continue
        ratio = right / left
        if (
            current_ratio is None
            or abs(ratio - current_ratio)
            > _SHORT_ROW_RTOL * max(abs(current_ratio), 1e-300)
        ):
            current_ratio = ratio
            current_len = 1
            current_start = index
        else:
            current_len += 1
        if (
            current_len > best_len
            and abs(current_ratio - 1.0) > _SHORT_ROW_RTOL
        ):
            best_len = current_len
            best_start = current_start
            best_ratio = current_ratio
    if best_len == 0 or best_ratio is None:
        return None
    x_run = a[best_start:best_start + best_len].astype(float)
    ratio = float(
        np.mean(
            b[best_start:best_start + best_len].astype(float) / x_run
        )
    )
    if abs(ratio - 1.0) <= _SHORT_ROW_RTOL or abs(ratio) <= 1e-9:
        return None
    return ratio, best_len, x_run


def _longest_hp_offset_run(a, b):
    best_len = 0
    best_start = 0
    best_offset = None
    current_len = 0
    current_start = 0
    current_offset = None
    current_scale = 1.0
    for index, (left, right) in enumerate(zip(a, b)):
        if not _is_short_hp(left) or not _is_short_hp(right):
            current_len = 0
            current_offset = None
            continue
        offset = right - left
        tolerance = _SHORT_ROW_RTOL * max(
            abs(left), abs(right), 1e-300
        )
        if (
            current_offset is None
            or abs(offset - current_offset) > tolerance
        ):
            current_offset = offset
            current_len = 1
            current_start = index
            current_scale = max(abs(left), abs(right), 1e-300)
        else:
            current_len += 1
        if (
            current_len > best_len
            and abs(current_offset) > _SHORT_ROW_RTOL * current_scale
        ):
            best_len = current_len
            best_start = current_start
            best_offset = current_offset
    if best_len == 0 or best_offset is None:
        return None
    x_run = a[best_start:best_start + best_len].astype(float)
    offset = float(
        np.mean(
            b[best_start:best_start + best_len].astype(float) - x_run
        )
    )
    if abs(offset) <= 1e-9:
        return None
    return offset, best_len, x_run


def _short_row_candidates(grid_sheets):
    candidates = []
    for (file_name, sheet_name), sheet in grid_sheets.items():
        rows = []
        for row in range(sheet.nrows):
            values = sheet.numeric[row, :]
            high_precision = [
                value for value in values if _is_short_hp(value)
            ]
            if (
                len(high_precision) < _SHORT_ROW_MIN_COLS
                or len(set(high_precision)) < 3
                or _vector_is_patterned(high_precision)
            ):
                continue
            label = _row_label(sheet, row, 1)
            if _AXIS_CONTEXT_LABEL_RE.search(label):
                continue
            rows.append({
                "file": file_name,
                "sheet": sheet_name,
                "row": row,
                "label": label,
                "values": values,
            })
            if len(rows) >= _SHORT_ROW_MAX_ROWS_PER_SHEET:
                break
        candidates.extend(rows)
    return candidates


def detect_short_row_reuse(
    grid_sheets,
    *,
    profile,
    figure_key,
    row_relation_min_cols,
    max_findings=60,
):
    by_sheet = {}
    for candidate in _short_row_candidates(grid_sheets):
        key = (candidate["file"], candidate["sheet"])
        by_sheet.setdefault(key, []).append(candidate)
    findings = []
    remaining = 4_000_000
    for (file_name, sheet_name), rows in by_sheet.items():
        figure = figure_key(sheet_name)

        def frequency_key(value):
            return float(f"{float(value):.5g}")

        frequencies = Counter(
            frequency_key(value)
            for row in rows
            for value in row["values"]
            if _is_short_hp(value)
        )

        def rare(run):
            return all(
                frequencies.get(frequency_key(value), 0)
                <= _SHORT_ROW_MAX_VALUE_FREQ
                for value in run
            )

        candidate_rows = {row["row"] for row in rows}

        def same_band(left_row, right_row):
            lo, hi = sorted((left_row, right_row))
            return all(
                row in candidate_rows for row in range(lo + 1, hi)
            )

        for left_index, left in enumerate(rows):
            for right in rows[left_index + 1:]:
                a = left["values"]
                b = right["values"]
                width = min(len(a), len(b))
                remaining -= width
                if remaining <= 0:
                    break
                identical = _longest_hp_identical_run(
                    a[:width], b[:width]
                )
                ratio = _longest_hp_ratio_run(a[:width], b[:width])
                offset = _longest_hp_offset_run(a[:width], b[:width])
                identical_ok = (
                    identical is not None
                    and _SHORT_ROW_MIN_COLS
                    <= identical[0]
                    < row_relation_min_cols
                    and len(np.unique(identical[1])) >= 3
                    and rare(identical[1])
                )
                ratio_ok = (
                    ratio is not None
                    and _SHORT_ROW_MIN_COLS
                    <= ratio[1]
                    < row_relation_min_cols
                    and len(np.unique(ratio[2])) >= 3
                    and rare(ratio[2])
                    and not same_band(left["row"], right["row"])
                    and not _near_power_of_ten(ratio[0])
                )
                offset_ok = (
                    offset is not None
                    and _SHORT_ROW_MIN_COLS
                    <= offset[1]
                    < row_relation_min_cols
                    and len(np.unique(offset[2])) >= 3
                    and rare(offset[2])
                    and not same_band(left["row"], right["row"])
                )
                if identical_ok and identical[0] >= max(
                    ratio[1] if ratio_ok else 0,
                    offset[1] if offset_ok else 0,
                ):
                    kind = "identical_row_reuse"
                    coefficient = 1.0
                    run_length = identical[0]
                    run_values = identical[1]
                elif offset_ok and (
                    not ratio_ok or offset[1] >= ratio[1]
                ):
                    kind = "offset_row_reuse"
                    coefficient = offset[0]
                    run_length = offset[1]
                    run_values = offset[2]
                elif ratio_ok:
                    kind = "scaled_row_reuse"
                    coefficient = ratio[0]
                    run_length = ratio[1]
                    run_values = ratio[2]
                else:
                    continue
                findings.append({
                    "kind": kind,
                    "short_run": True,
                    "file": file_name,
                    "file_a": file_name,
                    "file_b": file_name,
                    "same_file": True,
                    "same_sheet": True,
                    "sheet_a": sheet_name,
                    "sheet_b": sheet_name,
                    "row_a": left["label"],
                    "row_b": right["label"],
                    "size_a": run_length,
                    "size_b": run_length,
                    "same_position_count": run_length,
                    "fraction_of_smaller": 1.0,
                    "run_length": run_length,
                    "ratio": (
                        coefficient
                        if kind == "scaled_row_reuse"
                        else None
                    ),
                    "offset": (
                        coefficient
                        if kind == "offset_row_reuse"
                        else None
                    ),
                    "figure_a": figure,
                    "figure_b": figure,
                    "same_figure": figure is not None,
                    "delta": {
                        "pattern": {
                            "identical_row_reuse": "identical_row",
                            "offset_row_reuse": "offset_row",
                            "scaled_row_reuse": "scaled_row",
                        }[kind]
                    },
                    "block_a": f"row {left['row'] + 1}",
                    "block_b": f"row {right['row'] + 1}",
                    "examples": [
                        {
                            "row": left["label"],
                            "col": None,
                            "value": float(value),
                        }
                        for value in run_values[:5]
                    ],
                    "severity": "high",
                    "rule": (
                        f"rows '{left['label']}' and '{right['label']}' "
                        f"have an exact {kind} relationship over a short "
                        f"run of {run_length} high-precision columns"
                    ),
                })
                if len(findings) >= max_findings:
                    break
            if remaining <= 0 or len(findings) >= max_findings:
                break
        if remaining <= 0 or len(findings) >= max_findings:
            break
    if remaining <= 0:
        print(
            "[paperconan] detect_short_row_reuse: coverage bounded",
            file=sys.stderr,
        )
    apply_profile_to_findings(findings, profile)
    return findings


def _reliable_frac_tail(value):
    magnitude = abs(float(value))
    if not math.isfinite(magnitude):
        return ""
    precision = min(
        10,
        max(0, 15 - len(str(int(magnitude)))),
    )
    text = f"{magnitude:.{precision}f}".rstrip("0")
    return text.split(".", 1)[1] if "." in text else ""


def _shared_frac_is_small_denominator(fraction, max_denominator=128):
    if isinstance(fraction, str):
        value = float("0." + fraction) if fraction else 0.0
    else:
        value = abs(float(fraction)) % 1.0
    if value == 0.0:
        return False
    return any(
        abs(value * denominator - round(value * denominator)) < 2e-6
        for denominator in range(2, max_denominator + 1)
    )


def detect_within_row_shared_fraction(
    grid_sheets,
    *,
    profile,
    figure_key,
    max_findings=60,
):
    findings = []
    remaining = 8_000_000
    for (file_name, sheet_name), sheet in grid_sheets.items():
        figure = figure_key(sheet_name)
        for row in range(sheet.nrows):
            remaining -= sheet.ncols
            if remaining <= 0:
                break
            by_fraction = {}
            for col, value in enumerate(sheet.numeric[row, :]):
                if math.isnan(value):
                    continue
                magnitude = abs(float(value))
                if magnitude >= 1e7:
                    continue
                fraction = _reliable_frac_tail(magnitude)
                if len(fraction) < _WITHIN_ROW_FRAC_MIN_DIGITS:
                    continue
                by_fraction.setdefault(fraction, []).append(
                    (col, float(value), int(magnitude))
                )
            groups = [
                (fraction, cells)
                for fraction, cells in by_fraction.items()
                if len(cells) >= 2
                and len({integer for _col, _value, integer in cells}) >= 2
                and not _shared_frac_is_small_denominator(fraction)
            ]
            if not groups:
                continue
            groups.sort(key=lambda item: item[1][0][0])
            label = _row_label(sheet, row, 1)
            examples = [
                {
                    "row": label,
                    "col": None,
                    "tail": fraction,
                    "values": [
                        float(value)
                        for _col, value, _integer in cells[:3]
                    ],
                }
                for fraction, cells in groups[:5]
            ]
            sample = " / ".join(
                f"{value:.10g}"
                for _col, value, _integer in groups[0][1][:2]
            )
            findings.append({
                "kind": "within_row_shared_fraction",
                "file": file_name,
                "file_a": file_name,
                "file_b": file_name,
                "same_file": True,
                "same_sheet": True,
                "sheet_a": sheet_name,
                "sheet_b": sheet_name,
                "row": label,
                "row_a": label,
                "row_b": label,
                "n_groups": len(groups),
                "size_a": len(groups),
                "same_position_count": len(groups),
                "fraction_of_smaller": 1.0,
                "figure_a": figure,
                "figure_b": figure,
                "same_figure": figure is not None,
                "delta": {"pattern": "shared_fraction"},
                "block_a": f"row {row + 1}",
                "block_b": f"row {row + 1}",
                "examples": examples,
                "severity": "high",
                "rule": (
                    f"row '{label}' contains {len(groups)} value pairs "
                    f"that share a >={_WITHIN_ROW_FRAC_MIN_DIGITS}-digit "
                    "fractional tail while their integer parts differ "
                    f"(for example {sample})"
                ),
            })
            if len(findings) >= max_findings:
                break
        if remaining <= 0 or len(findings) >= max_findings:
            break
    if remaining <= 0:
        print(
            "[paperconan] detect_within_row_shared_fraction: "
            "coverage bounded",
            file=sys.stderr,
        )
    apply_profile_to_findings(findings, profile)
    return findings


def _row_has_fraction_candidates(values):
    count = 0
    for value in values:
        if math.isnan(value) or abs(float(value)) >= 1e7:
            continue
        if len(_reliable_frac_tail(value)) >= _ROW_PAIR_FRAC_MIN_DIGITS:
            count += 1
            if count >= _ROW_PAIR_MIN_RUN:
                return True
    return False


def detect_row_pair_shared_fraction(
    grid_sheets,
    *,
    profile,
    figure_key,
    max_findings=60,
):
    findings = []
    remaining = 40_000_000
    for (file_name, sheet_name), sheet in grid_sheets.items():
        candidates = []
        for row in range(sheet.nrows):
            if _row_has_fraction_candidates(sheet.numeric[row, :]):
                candidates.append(
                    (row, _row_label(sheet, row, 1))
                )
                if len(candidates) >= _ROW_PAIR_MAX_ROWS_PER_SHEET:
                    print(
                        "[paperconan] detect_row_pair_shared_fraction: "
                        "candidate rows capped",
                        file=sys.stderr,
                    )
                    break
        figure = figure_key(sheet_name)
        for left_index, (row_a, label_a) in enumerate(candidates):
            a = sheet.numeric[row_a, :]
            for row_b, label_b in candidates[left_index + 1:]:
                b = sheet.numeric[row_b, :]
                width = min(len(a), len(b))
                remaining -= width
                if remaining <= 0:
                    break
                runs = []
                current = []
                for col in range(width):
                    left = a[col]
                    right = b[col]
                    if (
                        math.isnan(left)
                        or math.isnan(right)
                        or abs(float(left)) >= 1e7
                        or abs(float(right)) >= 1e7
                    ):
                        left_tail = right_tail = ""
                    else:
                        left_tail = _reliable_frac_tail(left)
                        right_tail = _reliable_frac_tail(right)
                    if (
                        len(left_tail) >= _ROW_PAIR_FRAC_MIN_DIGITS
                        and left_tail == right_tail
                        and int(abs(float(left))) != int(abs(float(right)))
                    ):
                        current.append(
                            (
                                col,
                                left_tail,
                                float(left),
                                float(right),
                            )
                        )
                    else:
                        if len(current) >= _ROW_PAIR_MIN_RUN:
                            runs.append(current)
                        current = []
                if len(current) >= _ROW_PAIR_MIN_RUN:
                    runs.append(current)
                best = None
                for run in sorted(runs, key=len, reverse=True):
                    distinct = {
                        tail
                        for _col, tail, _left, _right in run
                        if not _shared_frac_is_small_denominator(tail)
                    }
                    differences = {
                        int(round(right - left))
                        for _col, _tail, left, right in run
                    }
                    if len(distinct) >= 3 and len(differences) >= 2:
                        best = run
                        break
                if best is None:
                    continue
                sample = ", ".join(
                    f"{left:.10g}/{right:.10g}"
                    for _col, _tail, left, right in best[:3]
                )
                findings.append({
                    "kind": "shared_fraction_row_pair",
                    "file": file_name,
                    "file_a": file_name,
                    "file_b": file_name,
                    "same_file": True,
                    "same_sheet": True,
                    "sheet_a": sheet_name,
                    "sheet_b": sheet_name,
                    "row_a": label_a,
                    "row_b": label_b,
                    "run_length": len(best),
                    "size_a": len(best),
                    "same_position_count": len(best),
                    "fraction_of_smaller": 1.0,
                    "figure_a": figure,
                    "figure_b": figure,
                    "same_figure": figure is not None,
                    "delta": {"pattern": "shared_fraction"},
                    "block_a": f"row {row_a + 1}",
                    "block_b": f"row {row_b + 1}",
                    "examples": [
                        {
                            "row": f"{label_a} / {label_b}",
                            "col": None,
                            "tail": tail,
                            "values": [float(left), float(right)],
                        }
                        for _col, tail, left, right in best[:5]
                    ],
                    "severity": "high",
                    "rule": (
                        f"rows '{label_a}' and '{label_b}' share the same "
                        f"decimal fraction at {len(best)} aligned columns "
                        "while their integer parts differ "
                        f"(for example {sample})"
                    ),
                })
                if len(findings) >= max_findings:
                    break
            if remaining <= 0 or len(findings) >= max_findings:
                break
        if remaining <= 0 or len(findings) >= max_findings:
            break
    if remaining <= 0:
        print(
            "[paperconan] detect_row_pair_shared_fraction: coverage bounded",
            file=sys.stderr,
        )
    apply_profile_to_findings(findings, profile)
    return findings
