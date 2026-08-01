"""Independent synthetic holdout for the offline ratio-context decision.

These generators are disjoint from the frozen calibration curves.  Every absence
assertion is presence-first: a shape must send at least one over-threshold relation
into M2.5 before zero isolated output counts as evidence.
"""
from __future__ import annotations

import math
import random

import pytest

from paperconan._audit import _classify_ratio_structure
from test_ratio_structure_bench import _qualifying_relations


HOLDOUT_SHEETS = 8
HOLDOUT_THRESHOLDS = (5.0, 7.0, 9.0)
SEPARABLE_CASES = (
    ("baseline_sigmoid", 0, 2, 14, 15, 0.63),
    ("exponential_decay", 0, 2, 12, 13, 0.84),
)
AMBIGUOUS_CASES = (
    ("baseline_sigmoid", 0, 1, 14, 15, 0.8371),
    ("saturating_response", 2, 1, 14, 15, 0.8371),
    ("exponential_decay", 4, 1, 14, 15, 0.8371),
    # At two decimals this distant pair still joins the source row's natural
    # neighbours into a supported local family, so it is not structurally isolated.
    ("saturating_response", 2, 2, 7, 19, 1.71),
)


def _holdout_rows(shape, decimals, sheet, rows=30, cols=8):
    """A deterministic smooth row family not used to choose the current thresholds."""
    rng = random.Random(20260811 + 1009 * sheet + 37 * decimals)
    xs = [0.25 + 5.75 * column / (cols - 1) for column in range(cols)]
    grouped_panels = sheet >= HOLDOUT_SHEETS // 2
    table = []
    for row in range(rows):
        fraction = row / (rows - 1)
        profile_fraction = ((row // 3) * 3 / (rows - 1)
                            if grouped_panels else fraction)
        amplitude = 0.7 + 2.6 * fraction
        if shape == "baseline_sigmoid":
            shift = 1.6 + 1.1 * profile_fraction
            profile = [0.65 + 9.0 / (1.0 + math.exp(-1.35 * (x - shift)))
                       for x in xs]
        elif shape == "saturating_response":
            half_max = 0.8 + 1.5 * profile_fraction
            profile = [0.45 + 8.5 * x / (half_max + x) for x in xs]
        elif shape == "exponential_decay":
            rate = 0.22 + 0.34 * profile_fraction
            profile = [0.55 + 8.0 * math.exp(-rate * x) for x in xs]
        else:  # pragma: no cover - the parametrisation below is the public test input
            raise ValueError(shape)
        table.append([
            round(amplitude * value + (
                0.0 if grouped_panels else rng.gauss(0.0, 0.012)), decimals)
            for value in profile
        ])
    return table


@pytest.mark.parametrize(
    "shape", ["baseline_sigmoid", "saturating_response", "exponential_decay"])
def test_holdout_smooth_families_have_no_isolated_ratio_at_stable_thresholds(shape):
    seen = 0
    seen_by_decimal = {decimals: 0 for decimals in (1, 2, 3)}
    summaries = 0
    isolated_by_threshold = {threshold: 0 for threshold in HOLDOUT_THRESHOLDS}
    for decimals in (1, 2, 3):
        for sheet in range(HOLDOUT_SHEETS):
            rows = _holdout_rows(shape, decimals, sheet)
            relations = _qualifying_relations(rows)
            seen += len(relations)
            seen_by_decimal[decimals] += len(relations)
            for threshold in HOLDOUT_THRESHOLDS:
                classified = _classify_ratio_structure(
                    rows, relations, min_peer_excess=threshold)
                isolated_by_threshold[threshold] += len(
                    classified["isolated_relations"])
                summaries += len(classified["families"]) + len(classified["series"])

    assert seen > 0, f"{shape} never reached M2.5"
    assert all(count > 0 for count in seen_by_decimal.values()), (
        shape, seen_by_decimal)
    assert summaries > 0, f"{shape} produced no table-level context summary"
    assert isolated_by_threshold == {5.0: 0, 7.0: 0, 9.0: 0}


@pytest.mark.parametrize("threshold", HOLDOUT_THRESHOLDS)
def test_holdout_separable_planted_relations_remain_isolated(threshold):
    for shape, sheet, decimals, source, target, constant in SEPARABLE_CASES:
        rows = _holdout_rows(shape, decimals, sheet)
        rows[target] = [round(constant * value, decimals) for value in rows[source]]
        relations = _qualifying_relations(rows)
        planted = [relation for relation in relations
                   if {relation["row_a"], relation["row_b"]} == {source, target}]

        assert planted, (shape, "planted relation never reached M2.5")
        classified = _classify_ratio_structure(
            rows, relations, min_peer_excess=threshold)
        decisions = [relation["context_class"] for relation in classified["relations"]
                     if {relation["row_a"], relation["row_b"]} == {source, target}]
        assert decisions
        assert set(decisions) == {"isolated_ratio"}, (shape, threshold, decisions)


@pytest.mark.parametrize("threshold", HOLDOUT_THRESHOLDS)
def test_holdout_nonseparable_planted_relations_fold_as_ambiguous(threshold):
    for shape, sheet, decimals, source, target, constant in AMBIGUOUS_CASES:
        rows = _holdout_rows(shape, decimals, sheet)
        rows[target] = [round(constant * value, decimals) for value in rows[source]]
        relations = _qualifying_relations(rows)
        classified = _classify_ratio_structure(
            rows, relations, min_peer_excess=threshold)
        planted = [relation for relation in classified["relations"]
                   if {relation["row_a"], relation["row_b"]} == {source, target}]

        assert planted, (shape, "non-separable relation disappeared")
        assert {relation["context_class"] for relation in planted} == {
            "ambiguous_within_context"
        }
        assert not [relation for relation in classified["isolated_relations"]
                    if {relation["row_a"], relation["row_b"]} == {source, target}]
        summaries = classified["families"] + classified["series"]
        assert sum(item["ambiguous_relation_count"] for item in summaries) >= len(planted)
