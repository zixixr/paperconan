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


def test_conflicting_directions_cannot_support_a_table_transform():
    panel_a = [_profile(seed) for seed in (63, 65, 67)]
    rows = panel_a + [_scaled(row, 0.84) for row in panel_a]
    relations = []
    for source, target in ((0, 3), (1, 4), (2, 5)):
        relations.append({
            "row_a": source, "row_b": target, "start": 0, "end": 7,
            "k_lo": 0.839, "k_hi": 0.841, "prediction_bits": 40.0,
        })
        # Once inverted into table order this claims 0.60–0.61, contradicting
        # the forward interval. Neither direction may be folded on its own.
        relations.append({
            "row_a": target, "row_b": source, "start": 0, "end": 7,
            "k_lo": 1 / 0.61, "k_hi": 1 / 0.60,
            "prediction_bits": 40.0,
        })

    got = _classify_ratio_table_context(
        rows, relations, row_blocks=[0, 0, 0, 1, 1, 1])

    assert got["table_transforms"] == []
    assert [item["context_class"] for item in got["relations"]] == [
        "isolated_cross_block_ratio",
    ] * len(relations)


def test_a_rank1_shape_cannot_merge_relations_across_explicit_blocks():
    """Block separators survive even when every numeric row has the same profile."""
    base = _profile(67)
    panel_a = [_scaled(base, constant) for constant in (1.0, 1.3, 1.7)]
    rows = panel_a + [_scaled(row, 0.84) for row in panel_a]
    relations = [
        {
            "row_a": source,
            "row_b": target,
            "start": 0,
            "end": 7,
            "k_lo": 0.839,
            "k_hi": 0.841,
            "prediction_bits": 40.0,
        }
        for source, target in ((0, 3), (1, 4), (2, 5))
    ]

    got = _classify_ratio_table_context(
        rows, relations, row_blocks=[0, 0, 0, 1, 1, 1])

    assert got["families"] == []
    assert len(got["table_transforms"]) == 1
    assert got["table_transforms"][0]["scope"] == "cross_block"
    assert got["table_transforms"][0]["pair_count"] == 3


def test_repeated_floor_cells_fold_as_a_quantized_common_pool():
    source = [70.0, 71.0, 72.0, 0.1, 0.1, 0.1, 0.1, 0.1]
    target = [35.0, 35.5, 36.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    rows = [source, _profile(71), _profile(73),
            target, _profile(79), _profile(83)]
    # Make the floor bins common inside each structural block, independently of the
    # candidate pair itself.
    for row in (rows[1], rows[2]):
        row[3:] = [0.1] * 5
    for row in (rows[4], rows[5]):
        row[3:] = [0.0] * 5
    forward = {
        "row_a": 0, "row_b": 3, "start": 0, "end": 7,
        "informative_columns": list(range(8)),
        "k_lo": 0.49, "k_hi": 0.51, "n_tests": 48,
        "prediction_bits": 35.0,
    }
    reverse = {
        "row_a": 3, "row_b": 0, "start": 0, "end": 7,
        "informative_columns": list(range(8)),
        "k_lo": 1 / 0.51, "k_hi": 1 / 0.49, "n_tests": 48,
        "prediction_bits": 35.0,
    }

    got = _classify_ratio_table_context(
        rows, [forward, reverse], row_blocks=[0, 0, 0, 1, 1, 1])

    assert got["isolated_relations"] == []
    assert [item["context_class"] for item in got["relations"]] == [
        "quantized_common_pool", "quantized_common_pool",
    ]
    assert len(got["quantized_common_pools"]) == 1
    assert got["quantized_common_pools"][0]["pair_count"] == 1


def test_common_pool_keeps_both_directions_if_either_has_distinctive_evidence():
    source = [10.0, 50.0, 90.0, 130.0, 0.2, 0.2]
    target = [3.710, 18.550, 33.390, 48.230, 0.074, 0.074]
    rows = [source, target]
    rows.extend([
        [200.0 + index, 300.0 + index, 400.0 + index, 500.0 + index,
         0.2, 0.2]
        for index in range(4)
    ])
    rows.extend([
        [1.111 + index, 2.222 + index, 3.333 + index, 4.444 + index,
         0.074, 0.074]
        for index in range(4)
    ])
    forward = {
        "row_a": 0, "row_b": 1, "start": 0, "end": 5,
        "informative_columns": list(range(6)),
        "k_lo": 0.3709, "k_hi": 0.3711, "n_tests": 100,
        "prediction_bits": 40.0,
    }
    reverse = {
        "row_a": 1, "row_b": 0, "start": 0, "end": 5,
        "informative_columns": list(range(6)),
        "k_lo": 1 / 0.3711, "k_hi": 1 / 0.3709, "n_tests": 100,
        "prediction_bits": 40.0,
    }

    got = _classify_ratio_table_context(
        rows, [forward, reverse], row_blocks=list(range(len(rows))))

    assert [item["context_class"] for item in got["relations"]] == [
        "isolated_cross_block_ratio", "isolated_cross_block_ratio",
    ]
    assert got["quantized_common_pools"] == []


def test_one_common_grid_cell_cannot_explain_an_otherwise_distinctive_pair():
    source = [19.3, 42.7, 67.1, 83.9, 51.4, 28.6, 74.2, 0.1]
    target = _scaled(source, 0.63)
    rows = [source, _profile(89), _profile(97),
            target, _profile(101), _profile(103)]
    for row in (rows[1], rows[2], rows[4], rows[5]):
        row[-1] = 0.1
    relations = [{
        "row_a": 0, "row_b": 3, "start": 0, "end": 7,
        "informative_columns": list(range(8)),
        "k_lo": 0.629, "k_hi": 0.631, "n_tests": 48,
        "prediction_bits": 40.0,
    }]

    got = _classify_ratio_table_context(
        rows, relations, row_blocks=[0, 0, 0, 1, 1, 1])

    assert got["quantized_common_pools"] == []
    assert len(got["isolated_relations"]) == 1
    assert got["isolated_relations"][0]["context_class"] == (
        "isolated_cross_block_ratio")


def test_common_values_on_only_one_side_do_not_erase_target_evidence():
    source = [70.0, 72.0, 0.2, 0.2, 0.2, 0.2]
    target = [35.0, 36.0, 0.1, 0.1, 0.1, 0.1]
    rows = [source, target]
    rows.extend([
        [80.0 + index, 90.0 + index, 0.2, 0.2, 0.2, 0.2]
        for index in range(3)
    ])
    relations = [{
        "row_a": 0, "row_b": 1, "start": 0, "end": 5,
        "informative_columns": list(range(6)),
        "k_lo": 0.49, "k_hi": 0.51, "n_tests": 40,
        "prediction_bits": 30.0,
    }]

    got = _classify_ratio_table_context(
        rows, relations, row_blocks=[0, 1, 2, 3, 4])

    assert got["quantized_common_pools"] == []
    assert len(got["isolated_relations"]) == 1


def test_common_floor_bins_accumulate_across_small_panels():
    rows = [
        [70.0 + index, 71.0 + index, 72.0 + index, 0.1, 0.1, 0.1]
        for index in range(5)
    ]
    relations = [{
        "row_a": 0, "row_b": 4, "start": 0, "end": 5,
        "informative_columns": list(range(6)),
        "k_lo": 1.04, "k_hi": 1.07, "n_tests": 100,
        "prediction_bits": 30.0,
    }]

    got = _classify_ratio_table_context(
        rows, relations, row_blocks=[0, 1, 2, 3, 4])

    assert got["relations"][0]["context_class"] == "quantized_common_pool"
    assert got["isolated_relations"] == []


def test_common_bins_do_not_hide_enough_distinctive_confirmation_columns():
    source = [19.3, 42.7, 67.1, 83.9, 51.4, 28.6, 0.1, 0.1]
    target = _scaled(source, 0.63)
    rows = [source, _profile(107), _profile(109),
            target, _profile(113), _profile(127)]
    for row in rows:
        row[-2:] = [0.1, 0.1]
    relations = [{
        "row_a": 0, "row_b": 3, "start": 0, "end": 7,
        "informative_columns": list(range(8)),
        "k_lo": 0.629, "k_hi": 0.631, "n_tests": 48,
        "prediction_bits": 45.0,
    }]

    got = _classify_ratio_table_context(
        rows, relations, row_blocks=[0, 0, 0, 1, 1, 1])

    assert got["quantized_common_pools"] == []
    assert len(got["isolated_relations"]) == 1


def test_a_pattern_split_by_a_separator_is_no_longer_reported_as_isolated():
    """A separator row is a layout artifact, not evidence that a pair stands alone.

    Rows sampled along one response are near-multiples of each other by construction.
    Splitting them into panels does not stop that being one repeated pattern, but the
    classifier used to hand every pair spanning the split straight to `isolated_ratio`
    without examining it, which is the largest measured false-positive source on
    panelled supplementary layouts. The blocks are not merged: the pairs are aggregated
    into one cross-panel signal, which `cross_panel_contexts` records.
    """
    base = _profile(5)
    rows = [_scaled(base, constant) for constant in (1.0, 1.31, 1.77, 2.13, 2.56, 3.04)]
    relations = _qualifying_relations(rows)
    blocks = [0, 0, 0, 1, 1, 1]
    spanning = {
        pair for pair in _pairs(relations)
        if blocks[pair[0]] != blocks[pair[1]]
    }

    assert spanning, "no relation spans the separator, so the fixture proves nothing"
    got = _classify_ratio_table_context(rows, relations, row_blocks=blocks)

    assert _pairs(got["isolated_relations"]) & spanning == set()


def test_a_rescaled_segment_across_a_separator_is_not_explained_away():
    """The separator-spanning recheck must not fold what the neighbours cannot explain.

    Only one pair shares a rescaled window here, and the rows around it have unrelated
    profiles. Asking the whole row set is supposed to answer "these neighbours do not
    account for this pair", so the pair has to stay isolated.
    """
    window = [21.43, 73.91, 34.27, 112.61, 52.89]
    rows = [
        [*_profile(11, columns=2), *window, *_profile(13, columns=2)],
        _profile(17, columns=9),
        _profile(19, columns=9),
        [*_profile(23, columns=2), *_scaled(window, 1.37), *_profile(29, columns=2)],
        _profile(31, columns=9),
        _profile(37, columns=9),
    ]
    relations = _qualifying_relations(rows)
    blocks = [0, 0, 0, 1, 1, 1]

    assert (0, 3) in _pairs(relations), "the rescaled window never reached the context layer"
    got = _classify_ratio_table_context(rows, relations, row_blocks=blocks)

    assert (0, 3) in _pairs(got["isolated_relations"])


def test_a_cross_panel_component_folds_into_one_recorded_signal():
    """Folding must leave a trace: the spec keeps block, row and edge counts.

    A component of cross-block edges is aggregated into one cross-panel signal rather
    than reported edge by edge. Dropping it from `isolated_relations` without recording
    anything would remove the pairs from every output the reader has.
    """
    base = _profile(5)
    rows = [_scaled(base, constant) for constant in (1.0, 1.31, 1.77, 2.13, 2.56, 3.04)]
    relations = _qualifying_relations(rows)
    blocks = [0, 0, 0, 1, 1, 1]

    got = _classify_ratio_table_context(rows, relations, row_blocks=blocks)

    assert len(got["cross_panel_contexts"]) == 1
    summary = got["cross_panel_contexts"][0]
    assert summary["context_class"] == "cross_panel_ratio_context"
    # Pinned exactly. A subset check over every row of the fixture holds for the empty
    # list too, so it cannot tell a recorded component from a dropped one; `>= 2` leaves
    # the count free to drift by sevenfold without a test noticing.
    assert summary["block_count"] == 2
    assert summary["edge_count"] == 9
    assert summary["relation_count"] == 19
    assert summary["rows"] == [0, 1, 2, 3, 4, 5]
    assert {relation["context_class"] for relation in got["relations"]
            if _spans(relation, blocks)} == {"cross_panel_ratio_context"}


def test_the_recorded_block_count_follows_how_many_panels_the_component_spans():
    """A second block layout, so `block_count` is measured rather than assumed.

    With only one fixture, and that one holding two panels, hardcoding the field to
    the literal 2 passes every test -- the count would be free to stop tracking the
    component at all.
    """
    base = _profile(5)
    rows = [_scaled(base, constant) for constant in (1.0, 1.31, 1.77, 2.13, 2.56, 3.04)]
    relations = _qualifying_relations(rows)

    got = _classify_ratio_table_context(rows, relations, row_blocks=[0, 0, 1, 1, 2, 2])

    assert len(got["cross_panel_contexts"]) == 1
    summary = got["cross_panel_contexts"][0]
    assert summary["block_count"] == 3
    assert summary["blocks"] == [0, 1, 2]
    assert summary["edge_count"] == 12


def _spans(relation, blocks):
    return blocks[int(relation["row_a"])] != blocks[int(relation["row_b"])]


def test_a_lone_cross_block_edge_is_reported_not_aggregated():
    """One edge is not a repeated pattern, so it stays the plain cross-block signal."""
    window = [21.43, 73.91, 34.27, 112.61, 52.89]
    rows = [
        [*_profile(11, columns=2), *window, *_profile(13, columns=2)],
        _profile(17, columns=9),
        _profile(19, columns=9),
        [*_profile(23, columns=2), *_scaled(window, 1.37), *_profile(29, columns=2)],
        _profile(31, columns=9),
        _profile(37, columns=9),
    ]
    relations = _qualifying_relations(rows)
    blocks = [0, 0, 0, 1, 1, 1]

    got = _classify_ratio_table_context(rows, relations, row_blocks=blocks)

    assert got["cross_panel_contexts"] == []
    assert (0, 3) in _pairs(got["isolated_relations"])
    assert {relation["context_class"] for relation in got["isolated_relations"]} == {
        "isolated_cross_block_ratio"
    }


def test_an_already_folded_edge_cannot_pull_a_lone_pair_into_a_component():
    """Edges the passes above explained are not available to build components with.

    Three aligned pairs fold into a table transform. One further cross-block pair is
    not part of that repeat. If the folded edges stayed in the graph they would share a
    row with it, make a two-pair component, and aggregate away a pair that nothing has
    accounted for.
    """
    panel_a = [_profile(seed) for seed in (11, 13, 17, 19)]
    rows = list(panel_a)
    rows += [_scaled(row, 0.84) for row in panel_a[:3]]
    rows.append(_scaled(panel_a[0], 2.31))
    blocks = [0, 0, 0, 0, 1, 1, 1, 1]
    relations = _qualifying_relations(rows)

    assert (0, 7) in _pairs(relations), "the lone cross-block pair never reached context"
    got = _classify_ratio_table_context(rows, relations, row_blocks=blocks)

    assert got["table_transforms"], "the aligned repeat should still fold as a transform"
    assert (0, 7) in _pairs(got["isolated_relations"])
