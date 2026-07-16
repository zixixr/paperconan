"""Unit tests for dense-relation-flood demotion (_demote_dense_relations).

A wide, correlated matrix (correlation tables, normalized replicate panels) yields
O(cols^2) identical/linear column relations by construction — one real paper produced
~20,000 'high' relations. Those are a density artifact, not a duplication red flag, and
must not drown the genuine signal in high-severity output.
"""
from __future__ import annotations

from paperconan._audit import (_demote_dense_relations, _demote_dense_sheets,
                               RELATION_FLOOD_CAP)


def _synthetic_relations(n):
    return [dict(kind="identical_column", col_a=f"c{i}", col_b=f"c{i+1}",
                 n=10, severity="high", rule=f"col[{i}] == col[{i+1}]") for i in range(n)]


def test_flood_of_relations_demoted_to_low():
    rels = _synthetic_relations(RELATION_FLOOD_CAP + 10)
    out = _demote_dense_relations(rels)
    assert all(r["severity"] == "low" for r in out)
    assert all(r.get("dense_block") for r in out)
    # findings are kept (visible), not dropped
    assert len(out) == RELATION_FLOOD_CAP + 10


def test_handful_of_relations_keep_high():
    rels = _synthetic_relations(3)
    out = _demote_dense_relations(rels)
    assert all(r["severity"] == "high" for r in out)
    assert not any(r.get("dense_block") for r in out)


def test_empty_relations_safe():
    assert _demote_dense_relations([]) == []


def test_flood_demoted_across_blocks_of_one_sheet():
    """A dense sheet is split into many numeric blocks, each under the cap on its own,
    but together over it — the per-SHEET total must trigger demotion."""
    half = RELATION_FLOOD_CAP // 2 + 5
    blocks = [
        {"file": "f.xlsx", "sheet": "S1", "relations": _synthetic_relations(half), "equal_pairs": []},
        {"file": "f.xlsx", "sheet": "S1", "relations": _synthetic_relations(half), "equal_pairs": []},
    ]
    _demote_dense_sheets(blocks)
    flat = [r for b in blocks for r in b["relations"]]
    assert all(r["severity"] == "low" and r.get("dense_block") for r in flat)


def test_separate_sheets_each_below_cap_keep_high():
    """Two different sheets, each with a handful of relations, must NOT be demoted just
    because their combined total would exceed the cap."""
    blocks = [
        {"file": "f.xlsx", "sheet": "S1", "relations": _synthetic_relations(3), "equal_pairs": []},
        {"file": "f.xlsx", "sheet": "S2", "relations": _synthetic_relations(3), "equal_pairs": []},
    ]
    _demote_dense_sheets(blocks, cap=4)
    flat = [r for b in blocks for r in b["relations"]]
    assert all(r["severity"] == "high" for r in flat)
