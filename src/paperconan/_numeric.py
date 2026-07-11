from __future__ import annotations

import numpy as np


def ulp_tolerance(actual, expected, *, ulps=16):
    actual = np.asarray(actual, dtype=float)
    expected = np.asarray(expected, dtype=float)
    spacing = np.maximum(
        np.abs(np.spacing(actual)),
        np.abs(np.spacing(expected)),
    )
    floor = np.full_like(spacing, np.finfo(float).smallest_subnormal)
    return ulps * np.maximum(spacing, floor)


def _local_variation(values):
    values = np.asarray(values, dtype=float)
    if values.size <= 1:
        return np.zeros_like(values)
    center = float(np.median(values))
    centered = np.abs(values - center)
    ordered = np.sort(values)
    steps = np.abs(np.diff(ordered))
    positive = steps[steps > 0]
    step = float(np.median(positive)) if positive.size else 0.0
    return np.maximum(centered, step)


def relation_close(actual, expected, *, rtol=1e-10, ulps=16):
    actual = np.asarray(actual, dtype=float)
    expected = np.asarray(expected, dtype=float)
    scale = np.maximum(_local_variation(actual), _local_variation(expected))
    tolerance = ulp_tolerance(actual, expected, ulps=ulps) + rtol * scale
    return np.abs(actual - expected) <= tolerance


def integer_shift_close(left, right, *, ulps=16):
    left = np.asarray(left, dtype=float)
    right = np.asarray(right, dtype=float)
    diff = right - left
    nearest = np.rint(diff)
    arithmetic_noise = ulp_tolerance(diff, nearest, ulps=ulps)
    return (
        (arithmetic_noise < 0.5)
        & (np.abs(diff - nearest) <= arithmetic_noise)
    )
