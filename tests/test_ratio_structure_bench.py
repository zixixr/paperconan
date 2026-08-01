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
                    "k_lo": run["k_lo"], "k_hi": run["k_hi"],
                    "prediction_bits": score["bits"],
                })
    return relations


def _shifted_curve_rows(decimals, rows=30, cols=8):
    """A smooth response whose shape parameter moves with the row, so the block is
    proportional-looking without being rank 1."""
    xs = [-3.0 + 6.0 * j / (cols - 1) for j in range(cols)]
    out = []
    for i in range(rows):
        amplitude = 0.5 + 2.5 * i / (rows - 1)
        shift = -0.5 + i / (rows - 1)
        out.append([round(amplitude * 10.0 / (1.0 + math.exp(-(x - shift))), decimals)
                    for x in xs])
    return out


@pytest.mark.parametrize("decimals", [1, 2, 3])
def test_every_frozen_curve_sheet_has_no_final_isolated_ratio(decimals):
    """Removing block context would leak raw curve edges at every precision."""
    seen = 0
    for sheet in range(SHEETS):
        rows = _curve_block(decimals, sheet)
        relations = _qualifying_relations(rows)
        seen += len(relations)
        classified = _classify_ratio_structure(rows, relations)

        assert classified["isolated_relations"] == [], (
            f"curve/{decimals}dp sheet {sheet} left isolated relations: "
            f"{classified['isolated_relations'][:3]}"
        )
        assert len(rows) == ROWS

    # Absence only means something once something was there to absorb. Without this
    # the whole parametrisation stays green when reconstruction returns nothing.
    assert seen > 0, (
        f"curve/{decimals}dp produced no over-threshold relation at all, so a final "
        "isolated count of zero proves silence rather than folding"
    )


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


def test_the_planted_pair_survives_an_otherwise_unrelated_block_at_every_precision():
    """The motivating case, swept, so the layer is not tuned to one fixture."""
    source = [42.13, 58.91, 19.37, 71.20, 33.84, 26.55, 49.02, 61.78]
    filler = [
        [4.12, 91.23, 7.34, 52.45, 18.56, 63.67, 29.78, 80.89],
        [77.14, 3.25, 46.36, 12.47, 88.58, 21.69, 54.71, 9.82],
        [15.19, 34.28, 72.37, 6.46, 49.55, 93.64, 27.73, 58.82],
    ]
    for decimals in (2, 3):
        rows = [[round(v, decimals) for v in source],
                [round(0.8409 * v, decimals) for v in source]] + filler

        relations = _qualifying_relations(rows)
        classified = _classify_ratio_structure(rows, relations)
        isolated = {tuple(sorted((r["row_a"], r["row_b"])))
                    for r in classified["isolated_relations"]}

        assert relations, f"{decimals}dp: nothing scored"
        assert isolated == {(0, 1)}, f"{decimals}dp: got {isolated}"


def test_a_quantized_common_pool_is_stopped_before_the_structure_layer():
    """Honest scope: the score already answers this one, so M2.5 never sees it.

    Rows drawn repeatedly from a small coarse pool collide constantly, and an earlier
    version of this file asserted `isolated_relations == []` here as if that
    demonstrated folding. It does not: nothing reaches the classifier at all. The
    assertion that carries meaning is the empty relation list.
    """
    rng = random.Random(20260801)
    pool = [i / 10 for i in range(1, 11)]
    rows = [[rng.choice(pool) for _ in range(8)] for _ in range(30)]

    assert _qualifying_relations(rows) == []
    assert _classify_ratio_structure(rows, [])["isolated_relations"] == []


def test_an_exact_copy_between_adjacent_rows_survives_a_smooth_block():
    """Row distance must not decide the verdict.

    A row written as an exact multiple of the row above it is the likeliest layout for
    the pattern this detector exists to surface, and it lands where a smooth block's
    own relations are densest. Classifying by proximity therefore deletes the signal
    precisely where it is most expected.
    """
    for gap in (1, 2, 3):
        rows = _shifted_curve_rows(2)
        source = 14
        rows[source + gap] = [round(0.8371 * v, 2) for v in rows[source]]

        relations = _qualifying_relations(rows)
        classified = _classify_ratio_structure(rows, relations)
        isolated = {tuple(sorted((r["row_a"], r["row_b"])))
                    for r in classified["isolated_relations"]}

        assert relations, f"gap {gap}: the planted copy was never scored"
        assert isolated == {(source, source + gap)}, (
            f"gap {gap}: expected the planted copy to survive, got {isolated}"
        )
        planted = [r for r in classified["relations"]
                   if {r["row_a"], r["row_b"]} == {source, source + gap}]
        assert planted
        assert {r["context_class"] for r in planted} == {"isolated_ratio"}


def test_an_exact_copy_survives_a_three_row_near_proportional_panel():
    """A three-row panel is the ordinary supplementary shape, and replicate rows are
    approximately proportional by their nature. Judging that block by how closely it
    approximates one profile discards the only thing that separates the planted pair
    from its neighbours: it is exact, and they are not.
    """
    base = [42.13, 58.91, 19.37, 71.20, 33.84, 26.55, 49.02, 61.78]
    rng = random.Random(11)
    rows = [
        base,
        [round(v * (1 + rng.uniform(-0.02, 0.02)), 2) for v in base],
        [round(0.8409 * v, 2) for v in base],
    ]

    relations = _qualifying_relations(rows)
    classified = _classify_ratio_structure(rows, relations)
    isolated = {tuple(sorted((r["row_a"], r["row_b"])))
                for r in classified["isolated_relations"]}

    assert relations
    assert isolated == {(0, 2)}


def test_one_decimal_inside_a_smooth_block_is_folded_as_ambiguous_context():
    """A non-separable pair stays in the table summary, never as an isolated output."""
    rows = _shifted_curve_rows(1)
    rows[15] = [round(0.8371 * v, 1) for v in rows[14]]

    relations = _qualifying_relations(rows)
    classified = _classify_ratio_structure(rows, relations)
    planted = [r for r in classified["relations"]
               if {r["row_a"], r["row_b"]} == {14, 15}]

    assert relations, "the planted copy was never scored"
    assert planted, "the non-separable relation disappeared instead of being folded"
    assert {r["context_class"] for r in planted} == {"ambiguous_within_context"}
    assert classified["isolated_relations"] == []
    summaries = classified["families"] + classified["series"]
    assert summaries
    assert sum(s["ambiguous_relation_count"] for s in summaries) >= len(planted)
