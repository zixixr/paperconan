"""A frozen baseline for short-row ratio findings on curve data, both directions.

Rows sampled along one smooth response are near-multiples of one another BY CONSTRUCTION --
that is what a dose-response family is, not an anomaly -- and it is the largest false-positive
driver measured for this detector. So a redesign of the ratio arm is held against a number
here rather than against a hope.

  BASELINE          what the detector invents on curve data. Everything it produces is a
                    false positive by construction.
  PLANTED_BASELINE  recall of one row replaced by an exact scaled copy of another, in a
                    different panel, rounded to the stratum's precision the way an export
                    does. Matched by row label, so the benign findings in the same stratum
                    cannot be counted as having caught it, and by RUN LENGTH as well as by
                    sheet, since the whole row is a copy and a short run is width the detector
                    did not recover.

A bench of only the first is passed perfectly by a detector that never fires, and until the
second existed a change to this arm could only be argued on its cost -- half a decision. Its
zeros are a RECORDED GAP, not a target.

Two layouts, because they behave completely differently:

  contiguous   every row adjacent, so the band guard sees one band and suppresses the ratio
               arm at every precision -- empty at the committed seed
  panelled     a separator every PANEL_SIZE rows, an ordinary supplementary layout, which the
               band guard cannot see across

HOW TO USE IT
  * `python tests/test_curve_bench_baseline.py` reprints both tables paste-ready.
  * `pytest -k "not bench_baseline"` deselects this file and its column-pair sibling.
  * Reseed before reading a movement as a size. This file freezes COUNTS, and they are
    seed-sensitive: the busiest stratum ranges over most of what it can produce and the
    committed draw sits at the top of that range. The sibling bench freezes bands for that
    reason; here the counts are kept because run length is the quantity that matters.

WHAT IT DOES NOT BOUND
  * The contiguous stratum is empty at the COMMITTED SEED, not as a property. `_same_band` is
    what holds it down and does not gate the pass-2 adjacent-pair path, so a minority of
    neighbouring seeds put a short finding there. Two earlier drafts called it zero
    "whatever happens" and then zero "at the shipped tolerances"; both were universals, and
    reseeding broke both.
  * Recall counts the SCALED arm only. Plant an identical copy instead and it reads zero
    while the pair is reported, through the identical arm.

THREE GATES hold the coarse rows down, and only two are tolerances. `_is_short_hp` admits a
cell by its ABSOLUTE decimal count (`PAPERCONAN_SHORT_ROW_MIN_FRAC_DIGITS`); the flat
`_SHORT_ROW_RTOL` holds most of the rest (`PAPERCONAN_SHORT_ROW_RTOL`); and neither moves
contiguous RECALL, because `_same_band` suppresses the ratio arm whenever every row between a
pair is also a candidate -- which with no separator is every pair. That guard is FROZEN by a
go/no-go in the design note this file cites, which forbids removing or rewriting it until the
real corpus is widened or known candidates are supplied.

All data is generated from SEED by `random.Random` -- only the stdlib stream is stable across
versions, and a gate that drifts with a dependency upgrade is not a gate. No real data.

Baselines measured on: eee615b. BASELINE was first frozen at 5e64dc7 and re-measured unchanged
here; PLANTED_BASELINE was added at eee615b.
Design: docs/superpowers/specs/2026-07-30-short-row-significance-gate.md

Signal, not verdict: everything the benign side produces is a false positive by construction,
which is the point -- it bounds what the detector says about data that has nothing to explain.

What was tried against the zeros and rejected is in this file's commit messages, not here.
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
# The strata, named once. Hardcoding the layouts and the ladder at each use site is how a
# rung added later gets silently dropped by the regenerator and by the layout test below.
_LAYOUTS = ("contiguous", "panelled")
_DECIMALS = (1, 2, 3)
_STRATA = tuple((lay, d) for lay in _LAYOUTS for d in _DECIMALS)

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

# Recall of the planted pair, per (layout, decimals), as {run length: sheets} out of SHEETS.
# An empty dict means the plant is not found at all on that stratum. RECORDED, NOT ENDORSED:
# the zeros are the gap this arm has. A change that raises this has earned the false positives
# it also brings; one that lowers it has to say so even if every stratum above improves.
#
# Stratifying by run length costs seed stability -- the histogram reproduces markedly less
# often than a plain sheet count -- and is paid deliberately, because the whole row is a copy
# and a change halving every run while keeping the pair reported would be invisible in an
# integer. See the module docstring on reseeding before reading a movement as a size.
PLANTED_BASELINE = {
    ("contiguous", 1): {},
    ("contiguous", 2): {},
    ("contiguous", 3): {},
    ("panelled", 1): {},
    ("panelled", 2): {},
    ("panelled", 3): {3: 7, 4: 3, 5: 2, 6: 2},
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


def _planted_block(decimals: int, sheet: int):
    """The same curve block with one row replaced by an exact scaled copy of another.

    Source and destination sit in different panels: a copy landing next to its source is what
    the band guard already covers. The copy is rounded to the stratum's precision AFTER
    scaling, which is what an export does and is the whole difficulty -- the relation is exact
    and the stored numbers cannot say so. Returns the pristine block too, so the control below
    asserts on the same data this planted into.
    """
    assert ROWS >= 3 * PANEL_SIZE, "need three panels to put the copy outside the source's"
    pristine = _curve_block(decimals, sheet)
    block = [list(r) for r in pristine]
    # Stride wider than SHEETS, as `_curve_block` uses, so neighbouring strata cannot share a
    # plant. Narrower than SHEETS, most cells collided with another stratum's and the table
    # held far fewer distinct (src, dst, k) triples than it had cells -- figures deliberately
    # not carried, since the expression they were measured on is no longer in the file.
    # Harmless while the coarse rows report nothing, and precisely not harmless the moment the
    # gap this table records starts closing, since location and scale are all the plant varies.
    rng = random.Random(SEED * 31 + decimals * 1009 + sheet)
    src = rng.randrange(0, PANEL_SIZE)
    dst = rng.randrange(2 * PANEL_SIZE, 3 * PANEL_SIZE)
    k = rng.uniform(1.15, 2.4)
    block[dst] = [round(v * k, decimals) for v in block[src]]
    # The control downstream is only meaningful if `pristine` really is this block minus the
    # plant. Restoring the decoupling the first version had -- planting into one sheet and
    # returning another's rows -- left every test green, so the coupling is asserted rather
    # than left to convention.
    assert sum(1 for a, b in zip(block, pristine) if a != b) == 1, \
        "the plant must differ from the pristine block in exactly the destination row"
    # The pristine block is returned so the non-vacuity control asserts on the SAME data this
    # planted into. Re-deriving it there instead left the control certifying an unrelated
    # block: planting into a different sheet's rows kept every test green.
    # Row labels as _as_sheet writes them, so a finding can be matched to THIS pair.
    return block, pristine, f"row {src + 1}", f"row {dst + 1}"


def _measure_planted(layout: str, decimals: int):
    """{run_length: sheets} for the planted pair, matched by row label.

    Counts the SCALED arm only -- see the module docstring for what that misses.
    """
    counts: collections.Counter = collections.Counter()
    for s in range(SHEETS):
        block, _pristine, label_src, label_dst = _planted_block(decimals, s)
        sheet = _as_sheet(block, layout == "panelled")
        grid = {("bench.xlsx", f"planted-{layout}-{decimals}-{s}"): sheet}
        for f in detect_short_row_reuse(grid):
            if f["kind"] != "scaled_row_reuse":
                continue
            if {f.get("row_a"), f.get("row_b")} == {label_src, label_dst}:
                counts[int(f["run_length"])] += 1
                break
    return dict(counts)


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


@pytest.mark.parametrize("layout,decimals", sorted(PLANTED_BASELINE))
def test_planted_scaled_copies_are_found_as_often_as_recorded(layout, decimals):
    """The half a false-positive bench cannot supply on its own.

    A RISE here is the goal of working on this arm; when it rises, move BASELINE above in
    the same commit so the trade is argued once rather than assumed twice. A FALL is a
    regression even if every stratum above improves.
    """
    observed = _measure_planted(layout, decimals)
    expected = PLANTED_BASELINE[(layout, decimals)]

    assert observed == expected, (
        f"recall of the planted scaled copy moved on {layout}/{decimals}dp: "
        f"expected {expected or 'none'} of {SHEETS} sheets, got {observed or 'none'}. "
        "Read it as {run length: sheets} -- more sheets is the goal of this arm, and so is "
        "a longer run on the same sheet, since the whole row is an exact copy."
    )


def test_the_planted_pair_is_not_reported_without_planting():
    """The planted stratum measures the plant, not the curve underneath it.

    Rows of one dose-response family are near-multiples of each other by construction, so a
    stratum counting findings on the planted pair could be counting the curve. With the plant
    removed, that pair is reported nowhere. Asserted only where recall is non-zero.
    """
    live = [k for k, v in PLANTED_BASELINE.items() if v]
    assert live, "no stratum with recall to check; see test_the_bench_still_has_teeth"

    for layout, decimals in live:
        for s in range(SHEETS):
            _planted, pristine, label_src, label_dst = _planted_block(decimals, s)
            sheet = _as_sheet(pristine, layout == "panelled")
            grid = {("bench.xlsx", f"unplanted-{layout}-{decimals}-{s}"): sheet}
            for f in detect_short_row_reuse(grid):
                assert not (f["kind"] == "scaled_row_reuse"
                            and {f.get("row_a"), f.get("row_b")} == {label_src, label_dst}), (
                    f"{layout}/{decimals}dp sheet {s}: the curve alone relates "
                    f"{label_src} and {label_dst}, so planting into them measures nothing")


def test_the_bench_still_has_teeth():
    """A bench of things that must not fire is passed perfectly by a detector that never
    fires. Anchored on the planted side, so "this arm got quieter about curve data" --
    the outcome worth working towards -- is not read as the bench breaking.
    """
    assert any(v for v in PLANTED_BASELINE.values()), (
        "PLANTED_BASELINE records no recall anywhere. A table regenerated to all-zero "
        "would certify a detector that never fires, so whatever made an exact scaled "
        "copy undetectable at every precision has to be understood before it is "
        "committed in that state."
    )


def test_the_contiguous_layout_cannot_certify_anything_on_its_own():
    """Why the panelled variant exists, asserted rather than left in a comment.

    The contiguous stratum is empty at THIS SEED -- a frozen fact, not a property; the module
    docstring says what actually holds it down and how it breaks. The teeth clause is anchored
    on the PLANTED side, because anchoring it on the benign counts reads the outcome this arm
    is working towards -- the detector getting quieter about curve data -- as the bench
    breaking.
    """
    contiguous = {d: _measure("contiguous", d) for d in _DECIMALS}
    panelled_planted = {d: _measure_planted("panelled", d) for d in _DECIMALS}

    assert all(v == {} for v in contiguous.values()), contiguous
    # Anchored on the PLANTED side, not on the benign counts. Anchoring it on those reads the
    # outcome this arm is working towards -- the detector getting quieter about curve data --
    # as the bench breaking: silencing the detector on the benign sheets while leaving the
    # plant found used to redden this test with a message saying the opposite of what had
    # happened.
    assert any(v for v in panelled_planted.values()), (
        "the panelled layout no longer finds even the planted copy; this file has no teeth"
    )


if __name__ == "__main__":       # pragma: no cover - regeneration helper, not a test
    # Paste-ready tables, over _STRATA. A rung added to _DECIMALS lands in both tables here
    # and the parametrized tests pick it up from the tables' own keys; a new LAYOUT would
    # reach both but still bypass the layout test, which names its two on purpose.
    for _name, _fn in (("BASELINE", _measure), ("PLANTED_BASELINE", _measure_planted)):
        print(f"{_name} = {{")
        for _layout, _d in _STRATA:
            print(f'    ("{_layout}", {_d}): {dict(sorted(_fn(_layout, _d).items()))!r},')
        print("}\n")
