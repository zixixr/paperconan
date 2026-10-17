"""A frozen behaviour bench for local pair runs (docs/superpowers/specs/2026-10-15-local-pair-runs-design.md).

The two existing benches call their own detectors and never this one, so they cannot hold it.
This one runs THIS detector on THEIR benign data -- the curve families of
test_curve_bench_baseline.py and the column families of test_column_pair_bench_baseline.py,
imported rather than copied so the three benches stay about the same data -- plus:

  KNOWN_COST   part of a column computed from another (one stretch normalised). It has the
               shape this detector looks for and numbers cannot tell it apart; header labels
               are what route it, so it is FROZEN AS A COST, not as a target to silence.
  MONOTONE     two lines drawn independently, each sorted (side-by-side ranked lists) or
               each a smooth curve with noise. Each moves little from one cell to the next,
               so a constant fits a few neighbouring cells by coincidence; scored against the
               whole line's range such a stretch once read as significant.
  DOSE         two dilution series with different top concentrations, one ending in its
               vehicle at 0 where the other has one dilution more: a whole-line ratio whose
               last cell departs. Listed once per dose, once per replicate, or twice over.
               Each line is itself a grid, which is what keeps it out.
  PARALLEL     two straight trends, noisy within about a rounding step, running parallel and
               then diverging. At the cell level this is a stretch of one line shifted onto
               the other; frozen as measured, a recorded cost rather than a target.
  MIXED        a line recorded in whole numbers that turns to three decimals partway, against
               another doing the same, the decimal part a benign shift plus noise. A coarse group
               interval met by fine cells credits them at their own step; frozen as measured.
  DECAY        two proportional decays with a near-zero tail: a whole-line relation that
               locality should refuse, but the tail makes the line's median |b| -- the scale
               the ratio arm reads departures against -- small. Frozen as measured.
  PLANTED      one stretch (4 or 6 cells) copied from a line, then scaled or shifted, in
               columns and in rows. A bench of things that must not fire is passed perfectly
               by a detector that never fires; this is the side that gives it teeth.
  PLANTED_MONOTONE  a 6-cell stretch copied into one of two MONOTONE lines: the cost side of
               scoring by segment, since within a sorted or smooth line a segment's spread is
               local and a copied stretch there carries less than among unrelated values.
  PLANTED_STRAIGHT  8 cells of a straight line with noise under a rounding step, shifted onto
               another: the cost side of reading a line straight within rounding as a grid,
               which leaves it out of the comparison (only some draws come out that straight).

Verdicts, not counts (CLAUDE.md "Freeze a verdict, not a count"): each stratum is the band
of _verdict -- silent / partial / pervasive -- over its draws, using the column-pair bench's
own banding so the three benches read alike.

Regenerate with `python -m tests.test_local_run_bench_baseline` from the repository root.
No real data: every value comes from `random.Random` seeded below.
"""
from __future__ import annotations

import functools
import math
import random

import pytest

from paperconan._audit import detect_local_pair_runs, find_numeric_blocks
from paperconan._sheet import Sheet
from tests import test_column_pair_bench_baseline as CP
from tests import test_curve_bench_baseline as CV

SEED = 20261015
REPEATS = CP.REPEATS
SEGMENTS = (4, 6)


def _local(sheet):
    """Every local-run finding the detector makes on a sheet, block by block."""
    out = []
    for r0, r1, c0, c1 in find_numeric_blocks(sheet):
        header = [sheet.cell(r0 - 1, c) if r0 > 0 else None for c in range(c0, c1)]
        out += detect_local_pair_runs(sheet, r0, r1, c0, c1, header)
    return out


def _pair_sheet(col_a, col_b):
    return Sheet.from_rows([["Group A", "Group B"]] + [[a, b] for a, b in zip(col_a, col_b)])


# --- benign: the curve families, every row pair of every sheet ------------------------------

@functools.lru_cache(maxsize=None)
def _measure_curves(layout, decimals):
    return CP._verdict(_local(CV._as_sheet(CV._curve_block(decimals, s), layout == "panelled"))
                       for s in range(CV.SHEETS))


# --- benign: the column families --------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _measure_column_family(family, digits):
    rng = random.Random(CP._seed(family, digits) + SEED)
    return CP._verdict(_local(_pair_sheet(*CP.BENIGN[family](rng, 1.0, digits)))
                       for _ in range(REPEATS))


# --- benign: two independent monotone lines --------------------------------------------------

MONOTONE = (("sorted", 100, 2), ("sorted", 300, 2), ("sorted", 1000, 3), ("smooth", 100, 2), ("smooth", 100, 3))


def _monotone_pair(rng, shape, n, decimals):
    if shape == "sorted":
        return [sorted(round(rng.uniform(5.0, 20.0), decimals) for _ in range(n)) for _ in range(2)]

    def curve(top, base):
        mid = rng.uniform(0.3, 0.7)
        return [round(top / (1.0 + math.exp(-8.0 * (i / (n - 1) - mid))) + base + rng.gauss(0.0, 0.02), decimals)
                for i in range(n)]

    return [curve(10.0, 1.0), curve(12.0, 2.0)]


@functools.lru_cache(maxsize=None)
def _measure_monotone(shape, n, decimals):
    rng = random.Random(SEED * 19 + n * 109 + decimals * 7 + (shape == "smooth"))
    return CP._verdict(_local(_pair_sheet(*_monotone_pair(rng, shape, n, decimals)))
                       for _ in range(REPEATS))


# --- benign: two dilution series ---------------------------------------------------------------

# (fold, significant figures with 0 = as computed, layout)
DOSES = tuple((fold, figures, layout) for fold in (2, 3) for figures in (0, 3)
              for layout in ("once", "per_replicate", "twice"))


def _dose_pair(rng, fold, figures, layout):
    def series(top, n):
        values = [top / fold ** i for i in range(n)]
        return [float(f"{v:.{figures}g}") for v in values] if figures else values
    top = round(rng.uniform(5.0, 100.0), 1)
    a, b = series(top, 9), series(top * rng.choice((0.2, 0.25, 0.5, 2.0, 3.0, 4.0)), 8) + [0.0]
    if layout == "per_replicate":
        return [x for x in a for _ in range(3)], [x for x in b for _ in range(3)]
    return (a * 2, b * 2) if layout == "twice" else (a, b)


@functools.lru_cache(maxsize=None)
def _measure_doses(fold, figures, layout):
    rng = random.Random(SEED * 31 + fold * 131 + figures + ("once", "per_replicate", "twice").index(layout) * 17)
    return CP._verdict(_local(_pair_sheet(*_dose_pair(rng, fold, figures, layout))) for _ in range(REPEATS))


# --- benign: two straight trends, parallel and then diverging ----------------------------------

PARALLEL = ((1, 0.05), (0, 0.5), (0, 1.0))          # (decimals, noise sd)


def _straight(rng, slope, base, noise, decimals, length=12):
    return [round(base + slope * t + rng.gauss(0.0, noise), decimals) for t in range(length)]


@functools.lru_cache(maxsize=None)
def _measure_parallel(decimals, noise):
    rng = random.Random(SEED * 37 + decimals * 137 + int(noise * 100))
    draws = []
    for _ in range(REPEATS):
        slope = rng.uniform(0.3, 1.0) if decimals else rng.uniform(3.0, 15.0)
        base, shift = rng.uniform(10.0, 40.0), rng.uniform(2.0, 8.0) * (1 if decimals else 6)
        bend = rng.choice((-1, 1)) * slope * rng.uniform(0.5, 1.5)
        a = _straight(rng, slope, base, noise, decimals)
        b = [round(base + shift + slope * t + (bend * (t - 7) if t >= 8 else 0.0) + rng.gauss(0.0, noise), decimals)
             for t in range(12)]
        draws.append(_local(_pair_sheet(a, b)))
    return CP._verdict(draws)


# --- benign: a line whose precision changes partway -------------------------------------------

MIXED = ((20, 8), (40, 8), (60, 12))          # (whole-number cells, three-decimal cells)


@functools.lru_cache(maxsize=None)
def _measure_mixed(whole, fine):
    rng = random.Random(SEED * 43 + whole * 149 + fine)
    draws = []
    for _ in range(REPEATS):
        a = [float(rng.randint(0, 20)) for _ in range(whole)]
        b = [float(rng.randint(0, 20)) for _ in range(whole)]
        tail = [round(rng.uniform(10.0, 30.0), 3) for _ in range(fine)]
        draws.append(_local(_pair_sheet(a + tail, b + [round(x + 1.5 + rng.gauss(0.0, 0.25), 3) for x in tail])))
    return CP._verdict(draws)


# --- benign: two proportional decays with a near-zero tail ------------------------------------

DECAYS = ((24, 2.0, 0.02, 2), (30, 3.0, 0.002, 3))


def _decay_pair(rng, n, rate, noise, decimals):
    k = rng.uniform(1.2, 2.0)
    base = [10.0 * math.exp(-t / rate) for t in range(n)]
    return ([round(v + rng.gauss(0.0, noise), decimals) for v in base],
            [round(k * v + rng.gauss(0.0, noise), decimals) for v in base])


@functools.lru_cache(maxsize=None)
def _measure_decays(n, rate, noise, decimals):
    rng = random.Random(SEED * 23 + n * 113 + decimals)
    return CP._verdict(_local(_pair_sheet(*_decay_pair(rng, n, rate, noise, decimals)))
                       for _ in range(REPEATS))


# --- known cost: one stretch of a column computed from another --------------------------------

def _partial_computed(rng, digits, segment):
    a, b = CP._base(rng, 1.0, digits), CP._base(rng, 1.0, digits)
    start = rng.randrange(0, CP.ROWS - segment + 1)
    control = CP._sig(4.73, digits)
    for i in range(start, start + segment):
        b[i] = CP._sig(a[i] / control, digits)
    return a, b


@functools.lru_cache(maxsize=None)
def _measure_known_cost(digits, segment):
    rng = random.Random(SEED * 11 + digits * 101 + segment)
    return CP._verdict(_local(_pair_sheet(*_partial_computed(rng, digits, segment)))
                       for _ in range(REPEATS))


# --- planted: one stretch copied then scaled or shifted ---------------------------------------

def _planted_columns(rng, digits, segment, relation):
    a, b = CP._base(rng, 1.0, digits), CP._base(rng, 1.0, digits)
    start = rng.randrange(0, CP.ROWS - segment + 1)
    k = rng.uniform(1.05, 3.0) if relation == "ratio" else CP._sig(rng.uniform(0.5, 3.0), digits)
    for i in range(start, start + segment):
        b[i] = CP._sig(a[i] * k if relation == "ratio" else a[i] + k, digits)
    return a, b, start


def _found(findings, kind, row_1based):
    return [f for f in findings
            if f["kind"] == kind and any(s <= row_1based <= e for s, e in f["segments"])]


@functools.lru_cache(maxsize=None)
def _measure_planted_columns(relation, digits, segment):
    rng = random.Random(SEED * 13 + digits * 103 + segment * 7 + (relation == "offset"))
    kind = "local_ratio_run" if relation == "ratio" else "local_offset_run"
    draws = []
    for _ in range(REPEATS):
        a, b, start = _planted_columns(rng, digits, segment, relation)
        draws.append(_found(_local(_pair_sheet(a, b)), kind, start + 2))   # header row, 1-based
    return CP._verdict(draws)


PLANTED_MONOTONE = tuple((shape, decimals, relation)
                         for shape, decimals in (("sorted", 2), ("smooth", 2), ("smooth", 3))
                         for relation in ("ratio", "offset"))


@functools.lru_cache(maxsize=None)
def _measure_planted_monotone(shape, decimals, relation):
    rng = random.Random(SEED * 29 + decimals * 127 + (shape == "smooth") * 3 + (relation == "offset"))
    kind = "local_ratio_run" if relation == "ratio" else "local_offset_run"
    draws = []
    for _ in range(REPEATS):
        a, b = _monotone_pair(rng, shape, 100, decimals)
        start = rng.randrange(10, 100 - 16)
        k = rng.uniform(1.2, 2.4) if relation == "ratio" else round(rng.uniform(0.5, 3.0), decimals)
        for i in range(start, start + 6):
            b[i] = round(a[i] * k if relation == "ratio" else a[i] + k, decimals)
        draws.append(_found(_local(_pair_sheet(a, b)), kind, start + 2))    # header row, 1-based
    return CP._verdict(draws)


PLANTED_STRAIGHT = ((1, 0.05), (0, 0.5))          # (decimals, noise sd)


@functools.lru_cache(maxsize=None)
def _measure_planted_straight(decimals, noise):
    rng = random.Random(SEED * 41 + decimals * 139 + int(noise * 100))
    draws = []
    for _ in range(REPEATS):
        slope = rng.uniform(0.3, 1.0) if decimals else rng.uniform(3.0, 15.0)
        a = _straight(rng, slope, rng.uniform(10.0, 40.0), noise, decimals)
        shift = round(rng.uniform(2.0, 8.0) * (1 if decimals else 6), decimals)
        b = [round(x + shift, decimals) for x in a[:8]] + [round(rng.uniform(10.0, 60.0), decimals) for _ in range(4)]
        draws.append(_found(_local(_pair_sheet(a, b)), "local_offset_run", 2))     # the stretch starts at row 1
    return CP._verdict(draws)


def _planted_rows(decimals, sheet, segment, relation):
    block = CV._curve_block(decimals, sheet)
    rng = random.Random(SEED * 17 + decimals * 107 + sheet * 3 + segment + (relation == "offset"))
    src, dst = rng.sample(range(CV.ROWS), 2)
    start = rng.randrange(0, CV.COLS - segment + 1)
    k = rng.uniform(1.15, 2.4) if relation == "ratio" else round(rng.uniform(0.5, 3.0), decimals)
    for j in range(start, start + segment):
        v = block[src][j]
        block[dst][j] = round(v * k if relation == "ratio" else v + k, decimals)
    return block, src, dst, start


@functools.lru_cache(maxsize=None)
def _measure_planted_rows(relation, decimals, segment):
    kind = "local_ratio_run" if relation == "ratio" else "local_offset_run"
    draws = []
    for s in range(CV.SHEETS):
        block, src, dst, start = _planted_rows(decimals, s, segment, relation)
        sheet = CV._as_sheet(block, False)
        rows = {src + 1, dst + 1}                                # sheet rows of the pair, 0-based
        hits = [f for f in _found(_local(sheet), kind, start + 2)  # label column, 1-based
                if f["orientation"] == "rows" and {f["row_a_idx"], f["row_b_idx"]} == rows]
        draws.append(hits)
    return CP._verdict(draws)


# --- frozen tables ----------------------------------------------------------------------------
# RECORDED, NOT ENDORSED. Regenerate with `python -m tests.test_local_run_bench_baseline` and
# paste; never hand-edit a band. KNOWN_COST is pervasive by construction (see the docstring);
# PLANTED_ROWS silent strata are a recorded gap -- four coarse cells do not carry the information
# a stretch needs -- not a target.
CURVE_BASELINE = {
    ('contiguous', 1): ('silent', {}),
    ('contiguous', 2): ('silent', {}),
    ('contiguous', 3): ('silent', {}),
    ('panelled', 1): ('silent', {}),
    ('panelled', 2): ('silent', {}),
    ('panelled', 3): ('silent', {}),
}
COLUMN_BASELINE = {
    ('baseline_subtracted', 4): ('silent', {}),
    ('baseline_subtracted', 6): ('silent', {}),
    ('baseline_subtracted', 7): ('silent', {}),
    ('baseline_subtracted', 8): ('silent', {}),
    ('baseline_subtracted', 9): ('silent', {}),
    ('baseline_subtracted', 10): ('silent', {}),
    ('complementary_percentages', 4): ('silent', {}),
    ('complementary_percentages', 6): ('silent', {}),
    ('complementary_percentages', 7): ('silent', {}),
    ('complementary_percentages', 8): ('silent', {}),
    ('complementary_percentages', 9): ('silent', {}),
    ('complementary_percentages', 10): ('silent', {}),
    ('control_normalised', 4): ('silent', {}),
    ('control_normalised', 6): ('silent', {}),
    ('control_normalised', 7): ('silent', {}),
    ('control_normalised', 8): ('silent', {}),
    ('control_normalised', 9): ('silent', {}),
    ('control_normalised', 10): ('silent', {}),
    ('duplicated_measurement', 4): ('silent', {}),
    ('duplicated_measurement', 6): ('silent', {}),
    ('duplicated_measurement', 7): ('silent', {}),
    ('duplicated_measurement', 8): ('silent', {}),
    ('duplicated_measurement', 9): ('silent', {}),
    ('duplicated_measurement', 10): ('silent', {}),
    ('null_independent', 4): ('silent', {}),
    ('null_independent', 6): ('silent', {}),
    ('null_independent', 7): ('silent', {}),
    ('null_independent', 8): ('silent', {}),
    ('null_independent', 9): ('silent', {}),
    ('null_independent', 10): ('silent', {}),
    ('percent_of_total', 4): ('silent', {}),
    ('percent_of_total', 6): ('silent', {}),
    ('percent_of_total', 7): ('silent', {}),
    ('percent_of_total', 8): ('silent', {}),
    ('percent_of_total', 9): ('silent', {}),
    ('percent_of_total', 10): ('silent', {}),
    ('rate_per_thousand', 4): ('silent', {}),
    ('rate_per_thousand', 6): ('silent', {}),
    ('rate_per_thousand', 7): ('silent', {}),
    ('rate_per_thousand', 8): ('silent', {}),
    ('rate_per_thousand', 9): ('silent', {}),
    ('rate_per_thousand', 10): ('silent', {}),
    ('sd_sem', 4): ('silent', {}),
    ('sd_sem', 6): ('silent', {}),
    ('sd_sem', 7): ('silent', {}),
    ('sd_sem', 8): ('silent', {}),
    ('sd_sem', 9): ('silent', {}),
    ('sd_sem', 10): ('silent', {}),
    ('sparse_shared_support', 4): ('silent', {}),
    ('sparse_shared_support', 6): ('silent', {}),
    ('sparse_shared_support', 7): ('silent', {}),
    ('sparse_shared_support', 8): ('silent', {}),
    ('sparse_shared_support', 9): ('silent', {}),
    ('sparse_shared_support', 10): ('silent', {}),
    ('two_level_coded', 4): ('silent', {}),
    ('two_level_coded', 6): ('silent', {}),
    ('two_level_coded', 7): ('silent', {}),
    ('two_level_coded', 8): ('silent', {}),
    ('two_level_coded', 9): ('silent', {}),
    ('two_level_coded', 10): ('silent', {}),
    ('unit_decadic', 4): ('silent', {}),
    ('unit_decadic', 6): ('silent', {}),
    ('unit_decadic', 7): ('silent', {}),
    ('unit_decadic', 8): ('silent', {}),
    ('unit_decadic', 9): ('silent', {}),
    ('unit_decadic', 10): ('silent', {}),
    ('unit_non_decadic', 4): ('silent', {}),
    ('unit_non_decadic', 6): ('silent', {}),
    ('unit_non_decadic', 7): ('silent', {}),
    ('unit_non_decadic', 8): ('silent', {}),
    ('unit_non_decadic', 9): ('silent', {}),
    ('unit_non_decadic', 10): ('silent', {}),
    ('varying_fold_change', 4): ('silent', {}),
    ('varying_fold_change', 6): ('silent', {}),
    ('varying_fold_change', 7): ('silent', {}),
    ('varying_fold_change', 8): ('silent', {}),
    ('varying_fold_change', 9): ('silent', {}),
    ('varying_fold_change', 10): ('silent', {}),
}
MONOTONE_BASELINE = {
    ('sorted', 100, 2): ('silent', {}),
    ('sorted', 300, 2): ('silent', {}),
    ('sorted', 1000, 3): ('silent', {}),
    ('smooth', 100, 2): ('silent', {}),
    ('smooth', 100, 3): ('silent', {}),
}

DOSE_BASELINE = {
    (2, 0, 'once'): ('silent', {}),
    (2, 0, 'per_replicate'): ('silent', {}),
    (2, 0, 'twice'): ('silent', {}),
    (2, 3, 'once'): ('silent', {}),
    (2, 3, 'per_replicate'): ('silent', {}),
    (2, 3, 'twice'): ('silent', {}),
    (3, 0, 'once'): ('silent', {}),
    (3, 0, 'per_replicate'): ('silent', {}),
    (3, 0, 'twice'): ('silent', {}),
    (3, 3, 'once'): ('silent', {}),
    (3, 3, 'per_replicate'): ('silent', {}),
    (3, 3, 'twice'): ('silent', {}),
}

PARALLEL_BASELINE = {
    (1, 0.05): ('partial', {'local_offset_run': 'partial'}),
    (0, 0.5): ('partial', {'local_offset_run': 'partial'}),
    (0, 1.0): ('partial', {'local_offset_run': 'partial'}),
}

PLANTED_STRAIGHT_BASELINE = {
    (1, 0.05): ('partial', {'local_offset_run': 'partial'}),
    (0, 0.5): ('partial', {'local_offset_run': 'partial'}),
}

MIXED_BASELINE = {
    (20, 8): ('silent', {}),
    (40, 8): ('silent', {}),
    (60, 12): ('silent', {}),
}

DECAY_BASELINE = {
    (24, 2.0, 0.02, 2): ('silent', {}),
    (30, 3.0, 0.002, 3): ('silent', {}),
}

PLANTED_MONOTONE_BASELINE = {
    ('sorted', 2, 'ratio'): ('partial', {'local_ratio_run': 'partial'}),
    ('sorted', 2, 'offset'): ('partial', {'local_offset_run': 'partial'}),
    ('smooth', 2, 'ratio'): ('partial', {'local_ratio_run': 'partial'}),
    ('smooth', 2, 'offset'): ('partial', {'local_offset_run': 'partial'}),
    ('smooth', 3, 'ratio'): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('smooth', 3, 'offset'): ('pervasive', {'local_offset_run': 'pervasive'}),
}

KNOWN_COST_BASELINE = {
    (4, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (4, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (6, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (6, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (7, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (7, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (8, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (8, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (9, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (9, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (10, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    (10, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
}
PLANTED_COLUMNS_BASELINE = {
    ('ratio', 4, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 4, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 6, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 6, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 7, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 7, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 8, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 8, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 9, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 9, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 10, 4): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('ratio', 10, 6): ('pervasive', {'local_ratio_run': 'pervasive'}),
    ('offset', 4, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 4, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 6, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 6, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 7, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 7, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 8, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 8, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 9, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 9, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 10, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 10, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
}
PLANTED_ROWS_BASELINE = {
    ('ratio', 1, 4): ('silent', {}),
    ('ratio', 1, 6): ('partial', {'local_ratio_run': 'partial'}),
    ('ratio', 2, 4): ('silent', {}),
    ('ratio', 2, 6): ('partial', {'local_ratio_run': 'partial'}),
    ('ratio', 3, 4): ('partial', {'local_ratio_run': 'partial'}),
    ('ratio', 3, 6): ('partial', {'local_ratio_run': 'partial'}),
    ('offset', 1, 4): ('silent', {}),
    ('offset', 1, 6): ('partial', {'local_offset_run': 'partial'}),
    ('offset', 2, 4): ('silent', {}),
    ('offset', 2, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 3, 4): ('pervasive', {'local_offset_run': 'pervasive'}),
    ('offset', 3, 6): ('pervasive', {'local_offset_run': 'pervasive'}),
}


def _tables():
    return {
        "CURVE_BASELINE": {(lay, d): _measure_curves(lay, d) for lay, d in CV._STRATA},
        "COLUMN_BASELINE": {(f, d): _measure_column_family(f, d) for f in sorted(CP.BENIGN) for d in CP.SIG_FIGURES},
        "MONOTONE_BASELINE": {m: _measure_monotone(*m) for m in MONOTONE},
        "DOSE_BASELINE": {d: _measure_doses(*d) for d in DOSES},
        "PARALLEL_BASELINE": {p: _measure_parallel(*p) for p in PARALLEL},
        "MIXED_BASELINE": {m: _measure_mixed(*m) for m in MIXED},
        "DECAY_BASELINE": {d: _measure_decays(*d) for d in DECAYS},
        "KNOWN_COST_BASELINE": {(d, s): _measure_known_cost(d, s) for d in CP.SIG_FIGURES for s in SEGMENTS},
        "PLANTED_COLUMNS_BASELINE": {(r, d, s): _measure_planted_columns(r, d, s)
                                     for r in ("ratio", "offset") for d in CP.SIG_FIGURES for s in SEGMENTS},
        "PLANTED_ROWS_BASELINE": {(r, d, s): _measure_planted_rows(r, d, s)
                                  for r in ("ratio", "offset") for d in CV._DECIMALS for s in SEGMENTS},
        "PLANTED_MONOTONE_BASELINE": {m: _measure_planted_monotone(*m) for m in PLANTED_MONOTONE},
        "PLANTED_STRAIGHT_BASELINE": {p: _measure_planted_straight(*p) for p in PLANTED_STRAIGHT},
    }


def _check(table, measure, stratum, what):
    observed, expected = measure(*stratum), table[stratum]
    assert observed == expected, (
        f"{what} moved on {stratum}: expected {expected}, got {observed} "
        f"({CP._direction(expected, observed)}). Regenerate the tables only as a deliberate "
        "decision, saying in the commit which way it moved and why.")


@pytest.mark.parametrize("stratum", sorted(CURVE_BASELINE))
def test_curve_families_hold_their_frozen_verdict(stratum):
    _check(CURVE_BASELINE, _measure_curves, stratum, "local runs on curve data")


@pytest.mark.parametrize("stratum", sorted(COLUMN_BASELINE))
def test_column_families_hold_their_frozen_verdict(stratum):
    _check(COLUMN_BASELINE, _measure_column_family, stratum, "local runs on a benign column family")


@pytest.mark.parametrize("stratum", sorted(MONOTONE_BASELINE))
def test_independent_monotone_lines_hold_their_frozen_verdict(stratum):
    _check(MONOTONE_BASELINE, _measure_monotone, stratum, "local runs on two independent monotone lines")


@pytest.mark.parametrize("stratum", sorted(DOSE_BASELINE))
def test_dilution_series_hold_their_frozen_verdict(stratum):
    _check(DOSE_BASELINE, _measure_doses, stratum, "local runs on two dilution series")


@pytest.mark.parametrize("stratum", sorted(PARALLEL_BASELINE))
def test_parallel_then_diverging_trends_hold_their_frozen_verdict(stratum):
    _check(PARALLEL_BASELINE, _measure_parallel, stratum, "local runs on parallel, then diverging, trends")


@pytest.mark.parametrize("stratum", sorted(PLANTED_STRAIGHT_BASELINE))
def test_stretches_planted_in_straight_lines_are_found_as_often_as_recorded(stratum):
    _check(PLANTED_STRAIGHT_BASELINE, _measure_planted_straight, stratum, "recall of a stretch planted in a straight line")


@pytest.mark.parametrize("stratum", sorted(MIXED_BASELINE))
def test_lines_changing_precision_hold_their_frozen_verdict(stratum):
    _check(MIXED_BASELINE, _measure_mixed, stratum, "local runs on lines that change precision partway")


@pytest.mark.parametrize("stratum", sorted(DECAY_BASELINE))
def test_proportional_decays_hold_their_frozen_verdict(stratum):
    _check(DECAY_BASELINE, _measure_decays, stratum, "local runs on two proportional decays")


@pytest.mark.parametrize("stratum", sorted(PLANTED_MONOTONE_BASELINE))
def test_stretches_planted_in_monotone_lines_are_found_as_often_as_recorded(stratum):
    _check(PLANTED_MONOTONE_BASELINE, _measure_planted_monotone, stratum, "recall of a stretch planted in a monotone line")


@pytest.mark.parametrize("stratum", sorted(KNOWN_COST_BASELINE))
def test_the_known_cost_holds_its_frozen_verdict(stratum):
    _check(KNOWN_COST_BASELINE, _measure_known_cost, stratum, "the partial-normalisation cost")


@pytest.mark.parametrize("stratum", sorted(PLANTED_COLUMNS_BASELINE))
def test_planted_column_stretches_are_found_as_often_as_recorded(stratum):
    _check(PLANTED_COLUMNS_BASELINE, _measure_planted_columns, stratum, "recall of a planted column stretch")


@pytest.mark.parametrize("stratum", sorted(PLANTED_ROWS_BASELINE))
def test_planted_row_stretches_are_found_as_often_as_recorded(stratum):
    _check(PLANTED_ROWS_BASELINE, _measure_planted_rows, stratum, "recall of a planted row stretch")


def test_every_stratum_the_generators_define_is_frozen():
    """A stratum added to a generator and not to a table would never be checked."""
    assert set(CURVE_BASELINE) == set(CV._STRATA)
    assert set(COLUMN_BASELINE) == {(f, d) for f in CP.BENIGN for d in CP.SIG_FIGURES}
    assert set(MONOTONE_BASELINE) == set(MONOTONE)
    assert set(DOSE_BASELINE) == set(DOSES)
    assert set(PARALLEL_BASELINE) == set(PARALLEL)
    assert set(PLANTED_STRAIGHT_BASELINE) == set(PLANTED_STRAIGHT)
    assert set(MIXED_BASELINE) == set(MIXED)
    assert set(DECAY_BASELINE) == set(DECAYS)
    assert set(PLANTED_MONOTONE_BASELINE) == set(PLANTED_MONOTONE)
    assert set(KNOWN_COST_BASELINE) == {(d, s) for d in CP.SIG_FIGURES for s in SEGMENTS}
    assert set(PLANTED_COLUMNS_BASELINE) == {(r, d, s) for r in ("ratio", "offset")
                                             for d in CP.SIG_FIGURES for s in SEGMENTS}
    assert set(PLANTED_ROWS_BASELINE) == {(r, d, s) for r in ("ratio", "offset")
                                          for d in CV._DECIMALS for s in SEGMENTS}


def test_the_bench_still_has_teeth():
    """A bench of things that must not fire is passed perfectly by a detector that never fires.
    Anchored on the planted side, and on the far negative control staying silent."""
    assert any(v[0] != "silent" for v in PLANTED_COLUMNS_BASELINE.values())
    assert any(v[0] != "silent" for v in PLANTED_ROWS_BASELINE.values())
    assert all(COLUMN_BASELINE[("null_independent", d)][0] == "silent" for d in CP.SIG_FIGURES)


if __name__ == "__main__":
    for name, table in _tables().items():
        print(f"{name} = {{")
        for key, verdict in table.items():
            print(f"    {key!r}: {verdict!r},")
        print("}")
