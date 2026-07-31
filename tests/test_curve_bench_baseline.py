"""A frozen false-positive baseline for short-row ratio findings on curve data.

Rows sampled along one smooth response are near-multiples of one another BY
CONSTRUCTION -- that is what a dose-response family is, not an anomaly. It is also
the largest false-positive driver measured for this detector, so any redesign of the
ratio arm has to be held against a number rather than against a hope.

This file IS that number. It generates the bench itself, runs the shipped detector,
and asserts the exact per-stratum counts. It is not a snapshot that regenerates:
change the detector and this test fails until someone decides the new counts are
acceptable and says so here.

Two layouts, because they behave completely differently:

  contiguous   every row adjacent, so the band guard sees one band and suppresses
               the ratio arm outright -- 0 at every precision
  panelled     a separator every PANEL_SIZE rows, an ordinary supplementary layout,
               which the band guard cannot see across

The panelled variant is why this file exists. A contiguous-only bench reports 0
whatever happens and would have certified any change as harmless.

All data is generated from SEED below by `random.Random`, chosen over numpy's
Generator because only the stdlib Mersenne Twister stream is stable across versions,
and a gate that drifts with a dependency upgrade is not a gate. No real data.

Baseline measured on: 5e64dc7 (paperconan main + the short-row ratio work to date)
Design: docs/superpowers/specs/2026-07-30-short-row-significance-gate.md section 7.5

Signal, not verdict: everything this bench produces is a false positive by
construction, which is the point -- it bounds what the detector says about data that
has nothing to explain.
"""
from __future__ import annotations

import collections
import math
import random

import pytest

from paperconan._audit import detect_short_row_reuse
from paperconan._sheet import Sheet

SEED = 20260731
SHEETS, ROWS, COLS = 20, 30, 8
PANEL_SIZE = 5

# What the detector emits today, per (layout, decimals) -> {run_length: count}.
# An empty dict means it reports nothing at all on that stratum.
BASELINE = {
    ("contiguous", 1): {},
    ("contiguous", 2): {},
    ("contiguous", 3): {},
    ("panelled", 1): {},
    ("panelled", 2): {},
    ("panelled", 3): {3: 17, 4: 1},
}


def _curve_block(decimals: int, sheet: int):
    """One smooth response sampled at COLS points, ROWS amplitudes, plus noise."""
    # Integer-derived seed, never hash(): string/tuple hashing is randomised
    # per process, which would make a committed baseline unreproducible.
    rng = random.Random(SEED * 1_000_003 + decimals * 1009 + sheet)
    xs = [-3.0 + 6.0 * j / (COLS - 1) for j in range(COLS)]
    response = [10.0 / (1.0 + math.exp(-x)) for x in xs]
    out = []
    for i in range(ROWS):
        amp = 0.5 + 2.5 * i / (ROWS - 1)
        out.append([round(abs(amp * r + rng.gauss(0.0, 0.02)) + 1e-9, decimals)
                    for r in response])
    return out


def _as_sheet(block, panelled: bool):
    rows = [["", *[f"c{j}" for j in range(COLS)]]]
    for i, r in enumerate(block):
        if panelled and i and i % PANEL_SIZE == 0:
            rows.append([f"Panel {i // PANEL_SIZE + 1}", *[None] * COLS])
        rows.append([f"row {i + 1}", *r])
    return Sheet.from_rows(rows)


def _measure(layout: str, decimals: int):
    counts = collections.Counter()
    for s in range(SHEETS):
        sheet = _as_sheet(_curve_block(decimals, s), layout == "panelled")
        grid = {("bench.xlsx", f"{layout}-{decimals}-{s}"): sheet}
        for f in detect_short_row_reuse(grid):
            if f["kind"] == "scaled_row_reuse":
                counts[int(f["run_length"])] += 1
    return dict(counts)


@pytest.mark.parametrize("layout,decimals", sorted(BASELINE))
def test_curve_data_stays_within_its_frozen_false_positive_baseline(layout, decimals):
    """Per stratum, so a rise in one cannot be paid for by a fall in another.

    Stratifying by run length as well as precision is deliberate: a redesign that
    traded eight short false positives for two long ones would look like an
    improvement in a single total and is not one -- a longer run reads as stronger
    evidence to whoever opens the report.
    """
    observed = _measure(layout, decimals)
    expected = BASELINE[(layout, decimals)]

    assert observed == expected, (
        f"curve false positives moved on {layout}/{decimals}dp: "
        f"expected {expected or 'none'}, got {observed or 'none'}. "
        "This bench contains nothing to find, so any change here is a change in "
        "what the detector invents. Update BASELINE only as a deliberate decision, "
        "recording the new commit."
    )


def test_the_contiguous_layout_cannot_certify_anything_on_its_own():
    """Why the panelled variant exists, asserted rather than left in a comment.

    With every row adjacent the band guard treats the block as one band and the
    ratio arm reports nothing at any precision. A bench built only that way returns
    0 no matter what the detector does, so it would certify any regression as
    harmless. The panelled layout is what gives this file its teeth.
    """
    contiguous = {d: _measure("contiguous", d) for d in (1, 2, 3)}
    panelled = {d: _measure("panelled", d) for d in (1, 2, 3)}

    assert all(v == {} for v in contiguous.values()), contiguous
    assert any(v for v in panelled.values()), (
        "the panelled layout stopped discriminating; this file no longer has teeth"
    )
