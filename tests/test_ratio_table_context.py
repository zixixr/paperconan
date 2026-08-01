"""Stress the table-wide context a person would inspect around a ratio pair.

The arithmetic and score have already admitted each relation.  These fixtures ask a
different question: is the pair alone, or is the same transform repeated in the same
column window and table layout?  Everything remains synthetic and offline.
"""
from __future__ import annotations

from paperconan._audit import _classify_ratio_table_context
from test_ratio_structure_bench import _qualifying_relations


def _profile(seed, columns=8):
    return [
        11.0 + ((seed * 37 + column * 53) % 89) + (column + 1) * 0.6180339887
        for column in range(columns)
    ]


def _scaled(values, constant, decimals=2):
    return [round(constant * value, decimals) for value in values]


def _pairs(relations):
    return {
        tuple(sorted((relation["row_a"], relation["row_b"])))
        for relation in relations
    }


def test_a_local_column_family_folds_despite_unrelated_outer_columns():
    middle = [21.43, 73.91, 34.27, 112.61, 52.89]
    rows = [
        [4.17, 91.23, *middle, 8.14],
        [77.41, 3.25, *_scaled(middle, 1.31), 46.36],
        [15.19, 34.28, *_scaled(middle, 1.77), 72.37],
    ]
    relations = _qualifying_relations(rows)

    assert relations, "the local window never reached the context layer"
    got = _classify_ratio_table_context(rows, relations)

    assert got["families"], "the local three-row family was not summarized"
    assert got["isolated_relations"] == []


def test_repeated_count_to_percentage_rows_become_one_table_transform():
    counts = [_profile(seed) for seed in (3, 7, 11)]
    rows = []
    for count in counts:
        rows.extend([count, _scaled(count, 0.4)])
    relations = _qualifying_relations(rows)

    assert {(0, 1), (2, 3), (4, 5)} <= _pairs(relations)
    got = _classify_ratio_table_context(rows, relations)

    summaries = got["table_transforms"]
    assert len(summaries) == 1
    assert summaries[0]["context_class"] == "proportional_table_transform"
    assert summaries[0]["pair_count"] == 3
    assert not ({(0, 1), (2, 3), (4, 5)} & _pairs(got["isolated_relations"]))
    assert got["isolated_relations"] == []


def test_one_count_to_percentage_pair_without_semantic_metadata_stays_isolated():
    count = _profile(19)
    rows = [count, _scaled(count, 0.4)]
    relations = _qualifying_relations(rows)

    assert _pairs(relations) == {(0, 1)}
    got = _classify_ratio_table_context(rows, relations)

    assert got["table_transforms"] == []
    assert _pairs(got["isolated_relations"]) == {(0, 1)}


def test_two_aligned_cross_panel_pairs_are_ambiguous_not_isolated():
    panel_a = [_profile(seed) for seed in (23, 29)]
    panel_b = [_scaled(row, 0.84) for row in panel_a]
    rows = panel_a + panel_b
    relations = _qualifying_relations(rows)

    assert {(0, 2), (1, 3)} <= _pairs(relations)
    got = _classify_ratio_table_context(rows, relations, row_blocks=[0, 0, 1, 1])

    assert len(got["table_transforms"]) == 1
    assert got["table_transforms"][0]["context_class"] == (
        "ambiguous_table_transform")
    assert got["table_transforms"][0]["pair_count"] == 2
    assert got["isolated_relations"] == []


def test_three_cross_panel_pairs_fold_but_a_different_scale_stays_isolated():
    panel_a = [_profile(seed) for seed in (31, 37, 41, 43)]
    panel_b = [_scaled(row, 0.84) for row in panel_a[:3]]
    panel_b.append(_scaled(panel_a[3], 0.61))
    rows = panel_a + panel_b
    relations = _qualifying_relations(rows)

    expected_family = {(0, 4), (1, 5), (2, 6)}
    assert expected_family | {(3, 7)} <= _pairs(relations)
    got = _classify_ratio_table_context(
        rows, relations, row_blocks=[0, 0, 0, 0, 1, 1, 1, 1])

    assert len(got["table_transforms"]) == 1
    summary = got["table_transforms"][0]
    assert summary["context_class"] == "proportional_table_transform"
    assert summary["pair_count"] == 3
    assert summary["relation_count"] >= 3, "reciprocal directions are evidence, not pairs"
    assert _pairs(got["isolated_relations"]) == {(3, 7)}
    assert {relation["context_class"] for relation in got["isolated_relations"]} == {
        "isolated_cross_block_ratio"
    }


def test_an_exact_shared_control_does_not_reenter_the_ratio_arm():
    control = _profile(47)

    assert _qualifying_relations([control, list(control)]) == []


def test_reciprocal_only_evidence_and_input_order_give_one_table_decision():
    rows = [_profile(seed) for seed in (53, 59, 61)]
    rows += [_scaled(row, 0.84) for row in rows]
    reciprocal = [
        {
            "row_a": target,
            "row_b": source,
            "start": 0,
            "end": 7,
            "k_lo": 1.0 / 0.841,
            "k_hi": 1.0 / 0.839,
            "prediction_bits": 40.0,
        }
        for source, target in ((0, 3), (1, 4), (2, 5))
    ]
    blocks = [0, 0, 0, 1, 1, 1]

    forward = _classify_ratio_table_context(rows, reciprocal, row_blocks=blocks)
    reversed_input = _classify_ratio_table_context(
        rows, list(reversed(reciprocal)), row_blocks=blocks)

    assert forward["table_transforms"] == reversed_input["table_transforms"]
    assert forward["table_transforms"][0]["pair_count"] == 3
    assert forward["table_transforms"][0]["relation_count"] == 3
    assert {relation["context_class"] for relation in forward["relations"]} == {
        "proportional_table_transform"
    }
