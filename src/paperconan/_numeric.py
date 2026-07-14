from __future__ import annotations

import math
from typing import Literal, NamedTuple

import numpy as np


def max_ulp_tolerance(values, *, ulps=16):
    spacing = max(
        (
            abs(math.ulp(float(value)))
            for value in values
        ),
        default=np.finfo(float).smallest_subnormal,
    )
    return ulps * max(
        spacing, np.finfo(float).smallest_subnormal
    )


def scalar_ulp_tolerance(*values, ulps=16):
    return max_ulp_tolerance(values, ulps=ulps)


class RelationInterceptAssessment(NamedTuple):
    state: Literal["proportional", "affine", "ambiguous"]
    zero_tolerance: float
    uncertainty: float
    intercept_lower: float
    intercept_upper: float


def assess_relation_intercept(
    *,
    slope: float,
    intercept: float,
    x_center: float,
    centered_radius: float,
    centered_residual: float,
    transformed_span: float,
    anchor_y: float,
    intercept_product: float,
) -> RelationInterceptAssessment | None:
    scalars = (
        slope,
        intercept,
        x_center,
        centered_radius,
        centered_residual,
        transformed_span,
        anchor_y,
        intercept_product,
    )
    if (
        centered_radius <= 0
        or centered_residual < 0
        or transformed_span < 0
        or not all(math.isfinite(value) for value in scalars)
    ):
        return None

    propagated_roundoff = (
        scalar_ulp_tolerance(anchor_y, intercept_product)
        + abs(x_center) * scalar_ulp_tolerance(slope)
    )
    if not math.isfinite(propagated_roundoff):
        return None
    zero_tolerance = (
        propagated_roundoff
        + 1e-9
        * max(
            transformed_span,
            np.finfo(float).smallest_subnormal,
        )
    )
    if not math.isfinite(zero_tolerance):
        return None
    uncertainty = (
        propagated_roundoff
        + centered_residual
        + abs(x_center)
        * centered_residual
        / centered_radius
    )
    if not math.isfinite(uncertainty):
        return None
    intercept_lower = intercept - uncertainty
    intercept_upper = intercept + uncertainty
    if not all(
        math.isfinite(value)
        for value in (intercept_lower, intercept_upper)
    ):
        return None

    if (
        intercept_lower >= -zero_tolerance
        and intercept_upper <= zero_tolerance
    ):
        state = "proportional"
    elif (
        intercept_lower > zero_tolerance
        or intercept_upper < -zero_tolerance
    ):
        state = "affine"
    else:
        state = "ambiguous"

    return RelationInterceptAssessment(
        state=state,
        zero_tolerance=zero_tolerance,
        uncertainty=uncertainty,
        intercept_lower=intercept_lower,
        intercept_upper=intercept_upper,
    )


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
