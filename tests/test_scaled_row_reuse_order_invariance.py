"""Insertion order must not change scaled-row-reuse findings.

``grid_sheets`` preserves workbook/tab discovery order, but that order is not
part of the detector's input semantics.  Candidate selection, pair direction,
rectangle folding, and the resulting explanation must therefore describe the
same statistical signal for every insertion order of the same sheets.
"""
from __future__ import annotations

import json
from itertools import permutations

import numpy as np
import pytest

from paperconan._audit import detect_scaled_row_reuse
from paperconan._sheet import Sheet


def _irregular_rows(n: int, *, seed: int) -> list[list[float]]:
    rng = np.random.default_rng(seed)
    return [
        [round(float(rng.uniform(5, 500)), 5) for _ in range(14)]
        for _ in range(n)
    ]


def _sheet(rows: list[list[float]], *, scale: float = 1.0) -> Sheet:
    return Sheet.from_rows(
        [[f"c{j}" for j in range(14)]]
        + [[round(value * scale, 6) for value in row] for row in rows]
    )


def _order_invariance_corpora():
    permuted = _irregular_rows(12, seed=5)
    replicated = _irregular_rows(4, seed=7)
    nonlexical_tabs = _irregular_rows(10, seed=11)
    truncated = _irregular_rows(6, seed=13)

    return [
        pytest.param(
            [
                (("Figure 9a.csv", "Figure 9a"), _sheet(permuted)),
                (("Figure 9b.csv", "Figure 9b"),
                 _sheet(list(reversed(permuted)), scale=2.0)),
            ],
            {"max_candidates": 10**6},
            "scaled_row_reuse",
            id="permuted-rectangle",
        ),
        pytest.param(
            [
                (("Figure 4a.csv", "Figure 4a"), _sheet(replicated)),
                (("Figure 4b.csv", "Figure 4b"),
                 _sheet([replicated[0]] * 9)),
            ],
            {"max_candidates": 10**6},
            "identical_row_reuse",
            id="one-row-replicated-across-panel",
        ),
        pytest.param(
            [
                (("source.xlsx", "Source Data Fig 2"), _sheet(nonlexical_tabs)),
                (("source.xlsx", "Extended Data Fig 2"),
                 _sheet(list(reversed(nonlexical_tabs)))),
            ],
            {"max_candidates": 10**6},
            "identical_row_reuse",
            id="nonlexical-workbook-tabs",
        ),
        pytest.param(
            [
                (("Figure 12c.csv", "Figure 12c"), _sheet(truncated)),
                (("Figure 12a.csv", "Figure 12a"), _sheet(truncated)),
                (("Figure 12b.csv", "Figure 12b"), _sheet(truncated)),
            ],
            # Six candidates fill the first two round-robin depths.  The next
            # two keep a third row from whichever sheets happen to be first,
            # so an insertion-ordered truncation changes which rectangle gets
            # the extra matched row.
            {"max_candidates": 8},
            "identical_row_reuse",
            id="truncated-candidate-pool",
        ),
    ]


def _finding_set(findings: list[dict]) -> frozenset[str]:
    """Freeze complete findings while treating their outer list as a set."""
    return frozenset(
        json.dumps(
            {**finding, "likely_benign": finding.get("likely_benign")},
            sort_keys=True,
            separators=(",", ":"),
        )
        for finding in findings
    )


@pytest.mark.parametrize(
    "entries,detector_kwargs,expected_kind",
    _order_invariance_corpora(),
)
def test_scaled_row_reuse_findings_ignore_grid_sheet_insertion_order(
    entries, detector_kwargs, expected_kind
):
    """Every permutation of one logical grid must emit the same findings."""
    baseline = None
    baseline_order = None

    for ordered_entries in permutations(entries):
        grid_sheets = dict(ordered_entries)
        findings = detect_scaled_row_reuse(
            grid_sheets,
            profile="review",
            max_findings=10**6,
            **detector_kwargs,
        )

        assert any(f["kind"] == expected_kind for f in findings), (
            f"fixture no longer exercises {expected_kind}"
        )
        current = _finding_set(findings)
        order = tuple(key for key, _ in ordered_entries)
        if baseline is None:
            baseline = current
            baseline_order = order
        else:
            assert current == baseline, (
                "the same grid emitted different findings for insertion orders "
                f"{baseline_order!r} and {order!r}"
            )
