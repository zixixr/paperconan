"""End-to-end offline acceptance for the precision-agnostic structure layer.

These tests join M1 reconstruction, M2 scoring and M2.5 context classification,
but deliberately never call or modify the production detector.  All data is
synthetic and deterministic.
"""
from __future__ import annotations

import math
import random

import pytest

from paperconan._audit import (
    _classify_ratio_structure,
    _effective_row_quantums,
    _ratio_prediction_bits,
    _scan_quantized_ratio_runs,
)
from test_curve_bench_baseline import ROWS, SHEETS, _curve_block


def _qualifying_relations(rows, minimum_bits=20.0):
    quantums = [_effective_row_quantums(row) for row in rows]
    width = max((len(row) for row in rows), default=0)
    n_pairs = len(rows) * (len(rows) - 1) // 2
    n_tests = max(1, 2 * n_pairs * max(0, width - 1))
    relations = []
    for row_a in range(len(rows)):
        for row_b in range(len(rows)):
            if row_a == row_b:
                continue
            for run in _scan_quantized_ratio_runs(
                    rows[row_a], rows[row_b], quantums[row_b]):
                if run["contains_one"] or run["contains_power_of_ten"]:
                    continue
                start, end = run["start"], run["end"]
                informative = set(run["informative_columns"])
                score = _ratio_prediction_bits(
                    rows[row_b][start:end + 1],
                    quantums[row_b][start:end + 1],
                    n_tests,
                    informative=[c in informative for c in range(start, end + 1)],
                )
                if score["bits"] < minimum_bits:
                    continue
                relations.append({
                    "row_a": row_a, "row_b": row_b,
                    "start": start, "end": end,
                    "prediction_bits": score["bits"],
                })
    return relations


@pytest.mark.parametrize("decimals", [1, 2, 3])
def test_every_frozen_curve_sheet_has_no_final_isolated_ratio(decimals):
    """Removing block context would leak raw curve edges at every precision."""
    for sheet in range(SHEETS):
        rows = _curve_block(decimals, sheet)
        relations = _qualifying_relations(rows)
        classified = _classify_ratio_structure(rows, relations)

        assert classified["isolated_relations"] == [], (
            f"curve/{decimals}dp sheet {sheet} left isolated relations: "
            f"{classified['isolated_relations'][:3]}"
        )
        assert len(rows) == ROWS


def test_an_isolated_two_decimal_relation_survives_inside_an_unrelated_block():
    """Classifying every strong pair as family would erase the motivating case."""
    source = [42.13, 58.91, 19.37, 71.20, 33.84, 26.55, 49.02, 61.78]
    target = [35.43, 49.54, 16.29, 59.87, 28.46, 22.33, 41.22, 51.95]
    rows = [
        source,
        target,
        [4.12, 91.23, 7.34, 52.45, 18.56, 63.67, 29.78, 80.89],
        [77.14, 3.25, 46.36, 12.47, 88.58, 21.69, 54.71, 9.82],
        [15.19, 34.28, 72.37, 6.46, 49.55, 93.64, 27.73, 58.82],
    ]

    relations = _qualifying_relations(rows)
    classified = _classify_ratio_structure(rows, relations)
    isolated_pairs = {
        tuple(sorted((r["row_a"], r["row_b"])))
        for r in classified["isolated_relations"]
    }

    assert isolated_pairs == {(0, 1)}
    assert classified["families"] == []


def test_a_shifted_curve_sequence_is_classified_after_rank1_stops_fitting():
    """Deleting the smooth-profile branch leaks adjacent partial curve relations."""
    xs = [-3.0 + 6.0 * j / 7 for j in range(8)]
    rows = []
    for amplitude, shift in zip(
            [0.5 + 2.5 * i / 29 for i in range(30)],
            [-0.5 + i / 29 for i in range(30)]):
        response = [10.0 / (1.0 + math.exp(-(x - shift))) for x in xs]
        rows.append([round(amplitude * value, 1) for value in response])

    relations = _qualifying_relations(rows)
    classified = _classify_ratio_structure(rows, relations)

    assert relations, "stress fixture no longer exercises an over-threshold relation"
    assert classified["block_rank1_residual"] > 0.02
    assert classified["series"], "the varying-shape family was not recognized as a series"
    assert classified["isolated_relations"] == []


def test_a_quantized_common_pool_does_not_create_an_isolated_ratio():
    """The independent bench alone does not cover repeated coarse value pools."""
    rng = random.Random(20260801)
    pool = [i / 10 for i in range(1, 11)]
    rows = [[rng.choice(pool) for _ in range(8)] for _ in range(30)]

    relations = _qualifying_relations(rows)
    classified = _classify_ratio_structure(rows, relations)

    assert classified["isolated_relations"] == []
