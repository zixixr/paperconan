"""Table context for ratio relations, after reconstruction and scoring.

The pairwise layers deliberately cannot tell an isolated relationship from a table
whose rows all share one proportional profile.  This file pins that boundary without
guessing a cause: a block-wide family is folded, while a lone pair in an otherwise
unrelated block remains available for human re-check.

All fixtures are synthetic.  Nothing here is wired into the production detector.
"""
from __future__ import annotations

import math

from paperconan._audit import (
    _classify_ratio_structure, _rank1_relative_residual, _row_profile_step_p90,
)


def _relation(a, b, start=0, end=3):
    return {"row_a": a, "row_b": b, "start": start, "end": end}


def test_an_exact_multirow_proportional_block_is_one_family():
    """Removing rank-1/component detection would leak every pair as isolated."""
    base = [2.0, 7.0, 3.0, 11.0]
    rows = [[k * v for v in base] for k in (1.0, 1.5, 2.0, 3.0)]
    relations = [_relation(0, 1), _relation(0, 2), _relation(1, 2),
                 _relation(2, 3)]

    got = _classify_ratio_structure(rows, relations)

    assert got["block_rank1_residual"] < 1e-12
    assert {r["context_class"] for r in got["relations"]} == {
        "proportional_family"
    }
    assert got["isolated_relations"] == []
    assert len(got["families"]) == 1
    assert got["families"][0]["rows"] == [0, 1, 2, 3]
    assert got["families"][0]["edge_count"] == 4


def test_a_proportional_component_is_folded_inside_a_non_rank1_block():
    """Looking only at the whole block would miss a family beside unrelated rows."""
    base = [2.0, 7.0, 3.0, 11.0]
    rows = [base, [1.5 * v for v in base], [2.0 * v for v in base],
            [5.0, 1.0, 9.0, 4.0], [8.0, 3.0, 2.0, 6.0]]
    relations = [_relation(0, 1), _relation(1, 2)]

    got = _classify_ratio_structure(rows, relations)

    assert got["block_rank1_residual"] > 0.02
    assert [f["scope"] for f in got["families"]] == ["component"]
    assert got["families"][0]["rows"] == [0, 1, 2]
    assert got["isolated_relations"] == []


def test_one_planted_pair_in_an_unrelated_block_stays_isolated():
    """A blanket 'any proportional rows form a family' rule would hide this pair."""
    base = [2.0, 7.0, 3.0, 11.0]
    rows = [base, [1.5 * v for v in base],
            [5.0, 1.0, 9.0, 4.0], [8.0, 3.0, 2.0, 6.0],
            [1.0, 8.0, 5.0, 2.0]]

    got = _classify_ratio_structure(rows, [_relation(0, 1)])

    assert got["families"] == []
    assert len(got["isolated_relations"]) == 1
    assert got["isolated_relations"][0]["context_class"] == "isolated_ratio"


def test_family_edge_count_deduplicates_directions_and_multiple_runs():
    """Changing the topology count back to relation count recreates the 10k-edge myth."""
    base = [2.0, 7.0, 3.0, 11.0]
    rows = [base, [2.0 * v for v in base], [3.0 * v for v in base]]
    relations = [
        _relation(0, 1, 0, 3),
        _relation(1, 0, 0, 3),        # reciprocal direction, same topology edge
        _relation(0, 1, 1, 3),        # another maximal run, still the same pair
    ]

    got = _classify_ratio_structure(rows, relations)

    assert got["families"][0]["edge_count"] == 1
    assert got["families"][0]["relation_count"] == 3
    assert len(got["relations"]) == 3, "classification keeps evidence; only summary folds"


def test_nearby_relations_in_a_smooth_profile_sequence_are_a_series():
    """Rank-1 alone misses curves whose shape parameter changes gradually by row."""
    xs = [-3.0 + 6.0 * j / 7 for j in range(8)]
    rows = []
    for amplitude, shift in zip(
            [0.5 + 2.5 * i / 29 for i in range(30)],
            [-0.5 + i / 29 for i in range(30)]):
        response = [10.0 / (1.0 + math.exp(-(x - shift))) for x in xs]
        rows.append([round(amplitude * value, 1) for value in response])
    relations = [_relation(0, 2, 0, 6), _relation(5, 6, 0, 6)]

    got = _classify_ratio_structure(rows, relations)

    assert got["block_rank1_residual"] > 0.02
    assert {r["context_class"] for r in got["relations"]} == {
        "ambiguous_within_context"
    }
    assert len(got["series"]) == 1
    assert got["series"][0]["ambiguous_relation_count"] == 2
    assert got["isolated_relations"] == []


def test_one_large_row_cannot_buy_the_block_a_family_label():
    """A Frobenius-relative residual is dominated by whichever row is biggest.

    Supplementary blocks mix magnitudes across rows as a matter of course -- counts
    beside fractions, raw values beside normalised ones. If scale decides the residual
    then a single large row silences every relation under it, and the suppression is
    keyed on formatting rather than on evidence.
    """
    unrelated = [[5.0, 1.0, 9.0, 4.0], [8.0, 3.0, 2.0, 6.0],
                 [1.0, 8.0, 5.0, 2.0], [7.0, 2.0, 3.0, 9.0]]

    for scale in (1.0, 1e2, 1e4, 1e6):
        rows = [[scale * v for v in [3.0, 9.0, 2.0, 7.0]]] + unrelated
        residual = _rank1_relative_residual(rows)

        assert residual > 0.02, (
            f"one row at {scale:g}x pulled the residual to {residual:g}, so five "
            "mutually unrelated rows would be folded into one family"
        )


def test_a_relation_far_tighter_than_its_block_is_not_absorbed_by_it():
    """Proportional-looking context must not outrank the pair's own evidence.

    Rows that merely approximate one profile are ordinary; a pair that matches to the
    last recorded digit while its neighbours are percent-level apart is not. Folding
    on approximate proportionality alone discards exactly that difference.
    """
    base = [21.4, 73.9, 34.2, 112.6, 52.8, 131.5, 41.7, 94.3]
    jitter = [1.012, 0.989, 1.007, 0.994, 1.015, 0.986, 1.003, 0.991]
    rows = [
        [round(v, 2) for v in base],
        # a scalar multiple perturbed CELL BY CELL: the row still looks like the same
        # profile, but no single constant reproduces it
        [round(v * 1.31 * j, 2) for v, j in zip(base, jitter)],
        [round(v * 1.77 / j, 2) for v, j in zip(base, jitter)],
        [round(v * 2.19, 2) for v in base],             # exact to the recorded grid
    ]
    relations = [_relation(0, 1, 0, 7), _relation(0, 2, 0, 7), _relation(0, 3, 0, 7)]

    got = _classify_ratio_structure(rows, relations)
    classes = {(r["row_a"], r["row_b"]): r["context_class"] for r in got["relations"]}

    assert got["block_rank1_residual"] <= 0.02, (
        "the fixture must still LOOK like one proportional block, otherwise the test "
        "passes without the family branch ever firing"
    )
    assert got["families"], "no family was claimed, so nothing was overridden"
    assert classes[(0, 3)] == "isolated_ratio", classes
    assert classes[(0, 1)] == "proportional_family", classes
    assert classes[(0, 2)] == "proportional_family", classes


def test_reciprocal_relations_receive_one_context_decision():
    """Reversing source and target cannot change an undirected context judgement.

    The old implementation measured the candidate against only the target row's
    inferred recording grid.  The same exact pair therefore landed in both the family
    summary and the isolated output when reconstruction returned both directions.
    """
    base = [21.4, 73.9, 34.2, 112.6, 52.8, 131.5, 41.7, 94.3]
    jitter = [1.012, 0.989, 1.007, 0.994, 1.015, 0.986, 1.003, 0.991]
    rows = [
        [round(v, 2) for v in base],
        [round(v * 1.31 * j, 2) for v, j in zip(base, jitter)],
        [round(v * 1.77 / j, 2) for v, j in zip(base, jitter)],
        [round(v * 2.19, 2) for v in base],
    ]
    relations = [
        _relation(0, 1, 0, 7), _relation(1, 0, 0, 7),
        _relation(0, 2, 0, 7), _relation(2, 0, 0, 7),
        _relation(0, 3, 0, 7), _relation(3, 0, 0, 7),
    ]

    got = _classify_ratio_structure(rows, relations)
    classes = {(r["row_a"], r["row_b"]): r["context_class"]
               for r in got["relations"]}

    for row in (1, 2, 3):
        assert classes[(0, row)] == classes[(row, 0)], classes


def test_context_class_is_invariant_to_unit_scale():
    """A unit conversion must not turn one proportional family into isolated pairs."""
    base = [2.0, 7.0, 3.0, 11.0]
    relations = [_relation(0, 1), _relation(0, 2),
                 _relation(1, 2), _relation(2, 3)]
    observed = []

    for scale in (1e-14, 1.0, 1e14):
        rows = [[scale * k * value for value in base]
                for k in (1.0, 1.5, 2.0, 3.0)]
        got = _classify_ratio_structure(rows, relations)
        observed.append([relation["context_class"] for relation in got["relations"]])

    assert observed == [["proportional_family"] * 4] * 3


def test_the_profile_step_separates_an_ordered_block_from_a_shuffled_one():
    """What the series threshold is measuring, pinned on both sides of its value.

    The statistic must be small only because the rows are ORDERED along one gradually
    changing profile. Shuffling the very same rows destroys nothing except the
    ordering, so a threshold that both survives keeps a label it has not earned.
    """
    xs = [-3.0 + 6.0 * j / 7 for j in range(8)]
    rows = []
    for i in range(30):
        shift = -0.5 + i / 29
        rows.append([round((0.5 + 2.5 * i / 29)
                           * 10.0 / (1.0 + math.exp(-(x - shift))), 2) for x in xs])
    shuffled = [rows[i] for i in
                [7, 22, 1, 15, 29, 4, 18, 11, 25, 0, 13, 27, 6, 20, 9,
                 23, 2, 16, 28, 5, 19, 12, 26, 3, 17, 10, 24, 8, 21, 14]]

    assert _row_profile_step_p90(rows) <= 0.05
    assert _row_profile_step_p90(shuffled) > 0.05

    # And the classifier must spend that evidence, not merely compute it. The peer
    # comparison is permutation-invariant -- identical rows, identical pairwise fits --
    # so the ordering is the ONLY thing separating these two calls.
    relations = [_relation(0, 2, 0, 6), _relation(5, 6, 0, 6)]
    assert _classify_ratio_structure(rows, relations)["series"]
    assert _classify_ratio_structure(shuffled, relations)["series"] == []


def test_a_two_row_block_offers_no_context_and_cannot_absorb_anything():
    """Folding needs something to fold into.

    With only the two rows the relation itself names, there is no third row to say
    whether that agreement is ordinary here. Absorbing on such a block would be
    asserting a family from a single observation of it.
    """
    base = [2.0, 7.0, 3.0, 11.0]
    got = _classify_ratio_structure([base, [2.0 * v for v in base]],
                                    [_relation(0, 1)])

    assert got["families"] == []
    assert len(got["isolated_relations"]) == 1


def test_ragged_rows_are_rejected_rather_than_raising():
    """Real numeric blocks are ragged. The layer must have a defined answer."""
    got = _classify_ratio_structure(
        [[1.0, 2.0, 3.0], [2.0, 4.0]], [_relation(0, 1, 0, 1)])

    assert got["block_rank1_residual"] == float("inf")
    assert got["families"] == []
    assert len(got["isolated_relations"]) == 1

    assert _rank1_relative_residual([[1.0, 2.0, 3.0], [2.0, 4.0]]) == float("inf")


def test_unusable_matrices_cannot_grant_a_family_explanation():
    """Changing an invalid/zero residual from infinity to zero would hide relations."""
    assert _rank1_relative_residual([[0.0, 0.0], [0.0, 0.0]]) == float("inf")
    assert _rank1_relative_residual([[1.0], [2.0], [3.0]]) == float("inf")
    assert _rank1_relative_residual([[1.0, float("nan")],
                                     [2.0, float("nan")]]) == float("inf")

    got = _classify_ratio_structure(
        [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        [_relation(0, 1, end=1), _relation(1, 2, end=1)],
    )
    assert got["families"] == []
    assert len(got["isolated_relations"]) == 2
