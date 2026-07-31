"""Table context for ratio relations, after reconstruction and scoring.

The pairwise layers deliberately cannot tell an isolated relationship from a table
whose rows all share one proportional profile.  This file pins that boundary without
guessing a cause: a block-wide family is folded, while a lone pair in an otherwise
unrelated block remains available for human re-check.

All fixtures are synthetic.  Nothing here is wired into the production detector.
"""
from __future__ import annotations

from paperconan._audit import _classify_ratio_structure, _rank1_relative_residual


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
