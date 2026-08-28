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

And a bench of things that must not fire is passed perfectly by a detector that
never fires, so there is a TRUE stratum too: the same curve data with one row
replaced by an exact scaled copy of another row, in a different panel -- the
relation this arm exists to catch, planted where the benign rows already sit. It
is frozen as recall of the planted pair specifically, by row label, so the benign
findings in the same stratum cannot be counted as having caught it, and by run
length as well as by sheet, because the whole row is an exact copy and a short run
is width the detector did not recover. Where that recall is currently zero, that
is a RECORDED GAP and not a target: a change that raises it has earned the false
positives it also brings, and a change that lowers it has to say so.

All data is generated from SEED below by `random.Random`, chosen over numpy's
Generator because only the stdlib Mersenne Twister stream is stable across versions,
and a gate that drifts with a dependency upgrade is not a gate. No real data.

Baselines measured on: eee615b. BASELINE was first frozen at 5e64dc7 and re-measured
unchanged here; PLANTED_BASELINE was added at eee615b. Record a commit rather than "main",
which moves -- the detector gained a rare-gate change between those two.
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

# Recall of the planted copy-then-scale pair, per (layout, decimals), as {run length: sheets}
# out of SHEETS. An empty dict means the plant is not found at all on that stratum.
# RECORDED, NOT ENDORSED -- the zeros are the gap this arm has, written down so that a
# change which closes any of them can be weighed against what it costs above, in the same
# file, instead of being argued from a hope.
#
# WHAT HAS BEEN TRIED AGAINST THE ZEROS, so the next attempt starts here rather than at the
# beginning. THREE gates hold these rows down, in different layouts, and only two are
# tolerances. An earlier draft of this note named two and sent the reader to sweep them for
# a zero neither of them causes, so the split is spelled out:
#
#   * `_is_short_hp` admits a cell by its ABSOLUTE decimal count, so at one and two decimals
#     the cells never enter a run and nothing downstream can matter. Sweep
#     PAPERCONAN_SHORT_ROW_MIN_FRAC_DIGITS and watch the coarse PANELLED rows move.
#   * with that floor lowered, the flat `_SHORT_ROW_RTOL` blocks the same rows a second
#     time, because two cells recorded to few decimals cannot pin a ratio that tightly.
#     PAPERCONAN_SHORT_ROW_RTOL sweeps it.
#   * neither touches the CONTIGUOUS rows, and that is the one worth knowing. `_same_band`
#     suppresses the ratio arm whenever every row between the pair is also a candidate,
#     which with no separator is every pair -- so contiguous recall stays at zero with both
#     tolerances opened as far as they go. It is a THIRD gate, it is not a precision effect,
#     and the design note this file cites plans to remove it.
#
# One correction worth carrying, because it was believed here on the strength of another
# commit's prose rather than a measurement. Lowering the admission floor was said to make
# previously reported findings VANISH, `_is_short_hp` feeding the frequency pool behind the
# rarity gate. On this bench it does not: across the floor's settings the reported set only
# grows, and no run gets shorter. That coupling was repaired by "trim a matched run to its
# rare part instead of rejecting it whole", which is already in main -- so "admission and
# the rarity baseline have to be separated first" is advice toward work already done.
#
# The rejected experiment, for the record. A version letting the tolerance follow each
# cell's recorded precision was written and measured against this file: it raised none of
# the coarse rows on its own, it LOWERED the one non-zero row here, and it invented a run
# longer than anything in BASELINE. It also put findings on the contiguous layout -- which
# is zero at the SHIPPED tolerances, not by construction, since a wide enough sweep makes
# the contiguous rows fire too, so that on its own disqualifies nothing.
#
# Re-measure rather than trusting any of this -- `_measure` and `_measure_planted` are the
# whole harness, and all three knobs above are read at import.
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

    Source and destination sit in different panels, because a copy landing next to its
    source is the case the band guard already covers and is not what this measures. The
    copy is rounded to the stratum's precision after scaling, which is what an export
    does and is the whole difficulty: the relation is exact and the stored numbers
    cannot say so.
    """
    assert ROWS >= 3 * PANEL_SIZE, "need three panels to put the copy outside the source's"
    pristine = _curve_block(decimals, sheet)
    block = [list(r) for r in pristine]
    # Stride wider than SHEETS, as `_curve_block` uses, so neighbouring strata cannot share a
    # plant. At a stride of 7 they did: two thirds of the (decimals, sheet) cells collided with
    # another stratum's, leaving barely half as many distinct (src, dst, k) triples as cells.
    # Harmless while the coarse rows report nothing, and precisely not harmless the moment the
    # gap this table records starts closing, since location and scale are all the plant varies.
    rng = random.Random(SEED * 31 + decimals * 1009 + sheet)
    src = rng.randrange(0, PANEL_SIZE)
    dst = rng.randrange(2 * PANEL_SIZE, 3 * PANEL_SIZE)
    k = rng.uniform(1.15, 2.4)
    block[dst] = [round(v * k, decimals) for v in block[src]]
    # The pristine block is returned so the non-vacuity control asserts on the SAME data this
    # planted into. Re-deriving it there instead left the control certifying an unrelated
    # block: planting into a different sheet's rows kept every test green.
    # Row labels as _as_sheet writes them, so a finding can be matched to THIS pair.
    return block, pristine, f"row {src + 1}", f"row {dst + 1}"


def _measure_planted(layout: str, decimals: int):
    """{run_length: sheets} for the planted pair, matched by row label.

    Stratified by run length for the reason the false-positive table already is, which cuts
    both ways: a longer run reads as stronger evidence to whoever opens the report. The whole
    row is an exact scaled copy here, so a run shorter than the block is width the detector
    did not recover -- and a change that kept every pair reported while halving its run would
    be invisible in a plain count of sheets.
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

    Rows of one dose-response family are near-multiples of each other by construction, so
    a stratum that counted findings on the planted pair could be counting the curve. It is
    not: with the same seeds, the same two rows, and the plant removed, that pair is
    reported nowhere. Asserted only where recall is non-zero -- elsewhere it would hold
    for the uninteresting reason that nothing is reported at all -- and
    test_the_bench_still_has_teeth is what keeps that set from quietly emptying.
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


if __name__ == "__main__":       # pragma: no cover - regeneration helper, not a test
    # Paste-ready tables. Iterates the LAYOUTS and the precision ladder, never the committed
    # tables' own keys: doing the latter silently drops any stratum added since, which is the
    # obvious mistake and a quiet one.
    for _name, _fn in (("BASELINE", _measure), ("PLANTED_BASELINE", _measure_planted)):
        print(f"{_name} = {{")
        for _layout in ("contiguous", "panelled"):
            for _d in (1, 2, 3):
                print(f'    ("{_layout}", {_d}): {dict(sorted(_fn(_layout, _d).items()))!r},')
        print("}\n")
