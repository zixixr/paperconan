"""A frozen behaviour baseline for column-pair relation findings.

`detect_relations` has no other instrument. Corpus counts are not one: the same change
measured over a small slice and a larger one gave opposite answers about part of itself.
This file manufactures its own data instead, so the answer is known by construction.

  BENIGN  thirteen families, each an exact relation with an innocent cause -- a duplicated
          measurement, a baseline subtraction, complementary percentages, SD against
          SEM = SD/sqrt(n), a count against its share of its own total, a rate per thousand,
          a decadic and a non-decadic unit conversion, a column over one control value,
          sparse counts beside their library-size-normalised twin, a two-level coded pair, a
          fold change that DRIFTS, and two independent columns. Anything reported on these
          is a false positive.
  TRUE    copied-then-scaled columns, stored the way real exports store numbers, with and
          without an all-zero baseline row. A bench of false positives alone is passed
          perfectly by a detector that never fires, so these must stay found.

Stratified by stored significant figures, one test per (family, rung), so the number of red
tests tracks the SIZE of a change rather than saturating at the first family that moves.

FROZEN AS A VERDICT, NOT A COUNT -- (band, {kind: band}) with bands silent / partial /
pervasive. Counts of a sampled quantity are not freezable here: the interesting strata sit ON
a gate, where whether a draw fires is a coin flip. Reseed and re-measure to see the
difference; `silent` means at or below RARE, not zero.

HOW TO USE IT
  * `python tests/test_column_pair_bench_baseline.py` reprints both tables paste-ready.
    Regenerate that way; do not hand-edit a table.
  * `pytest -k "not bench_baseline"` deselects this file and tests/test_curve_bench_baseline.py
    while iterating. It does NOT deselect tests/test_ratio_structure_bench.py.
  * When a table moves, reseed before reading the movement as a size. Rates near a band edge
    flip under reseeding; `_measure_benign` returns bands and discards rates, so count hits
    yourself if you need them.

WHAT IT DOES NOT BOUND
  * The DETECTOR, not the report: some prefilter suppression is label-driven and cannot be
    exercised here, so every verdict is an upper bound on report volume.
  * Thirteen families is fewer experiments than it sounds. On the statistic the ratio gate
    compares, std(ratio)/|mean(ratio)|, only five reach that arm and sit in its sensitive
    band, and they agree with each other closely -- one cause sampled five ways. Two more
    measure the same statistic but are held out by the arm's non-zero-divisor guard; two are
    exactly constant; four sit orders above any tolerance. The resolution comes from the
    LADDER, not the family count, and a change that needs telling apart from "it cost the
    proportional families" has no family here that would tell you.
  * No affine `y = k*x + b` family and no clustered-magnitude family, which is where this
    arm's known false positives live.
  * Every block is two columns and one height, so block width and the row-count gates are
    unmeasured. One magnitude.
  * The ladder is asymmetric about the shipped gate, which sits between its two finest rungs:
    it grades a WIDENING across most of its length and a TIGHTENING hardly at all.

Data comes from SEED via `random.Random` -- the stdlib stream is stable across versions, and
a gate that drifts with a dependency upgrade is not a gate. Seeds are derived from a dense
family index, never `hash()` and never summed code points. No real data: every value here is
invented.

Signal, not verdict: the benign strata are benign by construction, which is the point -- they
bound what the detector says about data that has an ordinary explanation.

The history behind each of these choices -- what was tried, measured and rejected -- is in the
commit messages for this file, deliberately not here.
"""
from __future__ import annotations

import collections
import functools
import math
import random

import pytest

from paperconan._audit import detect_relations
from paperconan._sheet import Sheet

SEED = 20260824
ROWS = 12
# Roughly one decade of residual ratio spread per rung, so the gates this arm is judged by
# fall inside the ladder rather than off one end. Measure the spread as std(ratio)/|mean|,
# the statistic the ratio gate itself compares -- earlier notes here used two others.
SIG_FIGURES = (4, 6, 7, 8, 9, 10)
# Eleven figures is excluded: past what a paper's exported table carries, and ten already
# sits inside the shipped ratio gate. One magnitude, not an axis -- whether this detector is
# scale-invariant is a real question and a different test.



def _sig(v, digits):
    return float(f"{v:.{digits}g}")


def _base(rng, magnitude, digits, n=ROWS):
    """Irregular positive values, one decade wide, at the given stored precision."""
    return [_sig(magnitude * rng.uniform(1.0, 9.9), digits) for _ in range(n)]


def _pair_findings(col_a, col_b, header=("Group A", "Group B")):
    """Raw detector output for one two-column block, headers deliberately neutral."""
    rows = [list(header)] + [[a, b] for a, b in zip(col_a, col_b)]
    sheet = Sheet.from_rows(rows)
    return list(detect_relations(sheet, 1, sheet.nrows, 0, 2, list(header)))


# --- the families -------------------------------------------------------------------

def _sd_sem(rng, magnitude, digits):
    sd = _base(rng, magnitude, digits)
    n = rng.choice((3, 9, 16))
    return sd, [_sig(v / math.sqrt(n), digits) for v in sd]


def _percent_of_total(rng, magnitude, digits):
    """Real counts against their real share of their own total, so the B column sums to 100.

    Not integers, though counts are -- see the comment below.
    """
    # Drawn with a fractional part so `digits` bites: integer counts carry two or three
    # significant figures, rounding to any rung is a no-op, and the precision axis --
    # the only axis this table has -- goes inert for the family.
    counts = [_sig(rng.uniform(1.0, 90.0) * magnitude, digits) for _ in range(ROWS)]
    total = sum(counts)
    return counts, [_sig(v / total * 100.0, digits) for v in counts]


def _rate_per_thousand(rng, magnitude, digits):
    counts = _base(rng, magnitude, digits)
    denom = magnitude * 431.0
    return counts, [_sig(v / denom * 1000.0, digits) for v in counts]


def _unit_decadic(rng, magnitude, digits):
    x = _base(rng, magnitude, digits)
    return x, [_sig(v * 1000.0, digits) for v in x]


def _unit_non_decadic(rng, magnitude, digits):
    x = _base(rng, magnitude, digits)
    return x, [_sig(v * 2.54, digits) for v in x]


def _control_normalised(rng, magnitude, digits):
    x = _base(rng, magnitude, digits)
    control = _sig(magnitude * 4.73, digits)
    return x, [_sig(v / control, digits) for v in x]


def _sparse_shared_support(rng, magnitude, digits):
    """Counts beside their library-size-normalised twin, sharing a zero support.

    Not integers, though counts are: drawn with a fractional part so `digits` bites.
    `magnitude` scales the counts, as it does everywhere else here.
    """
    counts = [0.0 if rng.random() < 0.4 else _sig(rng.uniform(1.0, 500.0) * magnitude, digits)
              for _ in range(ROWS)]
    library_size = sum(counts) or 1.0
    return counts, [_sig(v / library_size * 1e6, digits) if v else 0.0 for v in counts]


def _two_level_coded(rng, magnitude, digits):
    """A coded pair whose levels sit at the stratum's magnitude and precision.

    Does NOT grade the ratio tolerance: column A is half zeros, so the arm's non-zero-divisor
    guard holds it out, and its one distinct non-zero level would make y/x constant anyway.
    Read a move here as a change to `small_diff_set` or the linear arm.

    Its `exact_linear` verdict moved partial -> pervasive when the linear arm regained an
    absolute margin at the fit's own scale. `y = 2x` here is exactly affine, so the arm was
    always right to report it; half the rows being zero left them with no row-relative
    tolerance at all, so any residual in the fit rejected them and the family reported
    only sometimes. Louder, and the same pairs -- not a new false positive but a
    previously random one made consistent. The family's overall verdict did not move, no
    silent family started speaking, and TRUE_BASELINE did not move at all.
    """
    hi = _sig(magnitude * 5.372681943, digits)
    x = [rng.choice((0.0, hi)) for _ in range(ROWS)]
    return x, [_sig(v * 2.0, digits) for v in x]


def _null_independent(rng, magnitude, digits):
    return _base(rng, magnitude, digits), _base(rng, magnitude, digits)


def _duplicated_measurement(rng, magnitude, digits):
    """The same measurement written into two panels. `identical_column` fires and ends the
    pair before the ratio arm is reached."""
    x = _base(rng, magnitude, digits)
    return x, list(x)


def _baseline_subtracted(rng, magnitude, digits):
    """y = x - c: an ordinary baseline correction. Exercises `constant_offset`."""
    x = _base(rng, magnitude, digits)
    c = _sig(magnitude * 1.83, digits)
    return x, [_sig(v - c, digits) for v in x]


def _complementary_percentages(rng, magnitude, digits):
    """x + y = 100, the responder/non-responder split. Exercises `sum_constant`, and it is
    a benign generator this project has already had to reclassify once."""
    # Scaled by `magnitude` like every other family.
    x = [_sig(rng.uniform(5.0, 95.0) * magnitude, digits) for _ in range(ROWS)]
    return x, [_sig(100.0 * magnitude - v, digits) for v in x]


def _varying_fold_change(rng, magnitude, digits):
    """The negative control that matters: LOOKS proportional, is not.

    Two columns whose ratio drifts smoothly from ~1.5 to ~2.5 across the rows. Reporting a
    constant ratio here would be inventing one, and unlike `null_independent` it is not
    trivially separable -- the columns really are related, just not by a constant.
    """
    x = _base(rng, magnitude, digits)
    return x, [_sig(v * (1.5 + i / (ROWS - 1)), digits) for i, v in enumerate(x)]


BENIGN = {
    "baseline_subtracted": _baseline_subtracted,
    "complementary_percentages": _complementary_percentages,
    "duplicated_measurement": _duplicated_measurement,
    "varying_fold_change": _varying_fold_change,
    "sd_sem": _sd_sem,
    "percent_of_total": _percent_of_total,
    "rate_per_thousand": _rate_per_thousand,
    "unit_decadic": _unit_decadic,
    "unit_non_decadic": _unit_non_decadic,
    "control_normalised": _control_normalised,
    "sparse_shared_support": _sparse_shared_support,
    "two_level_coded": _two_level_coded,
    "null_independent": _null_independent,
}


def _copy_then_scale(rng, magnitude, digits, zero_baseline=False):
    x = _base(rng, magnitude, digits, ROWS - (1 if zero_baseline else 0))
    k = rng.uniform(1.05, 3.0)
    y = [_sig(v * k, digits) for v in x]
    if zero_baseline:
        x, y = [0.0] + x, [0.0] + y
    return x, y


# --- measurement --------------------------------------------------------------------
#
# A stratum is frozen as a VERDICT, not a count: the interesting strata sit ON a gate, where
# whether a draw fires is a coin flip that no number of repeats turns into a constant. Bands
# hold up under reseeding; counts do not. Kinds seen in at most RARE of the draws are dropped,
# because a one-in-two-hundred branch of a stratum is not its behaviour.
REPEATS = 200
RARE = 0.05


_ORDER = ("silent", "partial", "pervasive")


def _direction(expected, observed):
    """LOUDER / QUIETER / SAME VOLUME, so the reader is not left to classify the diff.

    The third case is real and common: an arm taking over from another reports the same
    pairs under a different kind, which is neither a new false positive nor a suppressed
    one. A message offering only two directions asks a question its own output cannot
    answer.
    """
    was, now = _ORDER.index(expected[0]), _ORDER.index(observed[0])
    if now > was:
        return "LOUDER"
    if now < was:
        return "QUIETER"
    return "SAME VOLUME, DIFFERENT KINDS"


def _bucket(rate):
    return "silent" if rate <= RARE else ("pervasive" if rate >= 1 - RARE else "partial")


def _verdict(pairs):
    """(band, {kind: band}) for an iterable of per-draw finding lists.

    Each kind carries its OWN band rather than merely being present, which narrows the blind
    spot without removing it: a kind can still move a long way inside `partial` and produce
    identical output here. Narrowing further was tried and rejected -- an extra edge would
    fall under several strata and bring back the seed-sensitivity that freezing counts had.
    """
    hit, per, total = 0, collections.Counter(), 0
    for findings in pairs:
        total += 1
        if findings:
            hit += 1
        for kind in {f["kind"] for f in findings}:
            per[kind] += 1
    kinds = {k: _bucket(c / total) for k, c in sorted(per.items()) if c / total > RARE}
    return _bucket(hit / total), kinds


def _seed(family, digits):
    """Integer-derived, never hash(): string hashing is randomised per process, which would
    make a committed baseline unreproducible. Indexed rather than summed over the name's
    code points -- that checksum collided (`percent_of_total` and `null_independent` both
    summed to 1704), so two families shared one random stream, one of them the stratum
    singled out as an invariant."""
    return SEED * 1_000_003 + digits * 1009 + sorted(BENIGN).index(family) * 7_919


@functools.lru_cache(maxsize=None)
def _measure_benign(family, digits):
    rng = random.Random(_seed(family, digits))
    return _verdict(_pair_findings(*BENIGN[family](rng, 1.0, digits))
                    for _ in range(REPEATS))


@functools.lru_cache(maxsize=None)
def _measure_true(digits, zero_baseline):
    rng = random.Random(SEED * 7_919 + digits * 601 + int(zero_baseline))
    # ANY kind. Counting only `constant_ratio` recorded "nothing found" where the detector
    # in fact reports every one of these pairs as `exact_linear`, and freezing that would
    # have let the arm actually carrying the detection be deleted with the suite green.
    return _verdict(_pair_findings(*_copy_then_scale(rng, 1.0, digits, zero_baseline))
                    for _ in range(REPEATS))




# What the detector does today, per (benign family, stored significant figures).
# RECORDED, NOT ENDORSED. `silent` means at or below RARE, not zero.
BENIGN_BASELINE = {
    ("baseline_subtracted", 4): ('pervasive', {'constant_offset': 'pervasive'}),
    ("baseline_subtracted", 6): ('pervasive', {'constant_offset': 'pervasive'}),
    ("baseline_subtracted", 7): ('pervasive', {'constant_offset': 'pervasive'}),
    ("baseline_subtracted", 8): ('pervasive', {'constant_offset': 'pervasive'}),
    ("baseline_subtracted", 9): ('pervasive', {'constant_offset': 'pervasive'}),
    ("baseline_subtracted", 10): ('pervasive', {'constant_offset': 'pervasive'}),
    ("complementary_percentages", 4): ('partial', {'exact_linear': 'partial', 'sum_constant': 'partial'}),
    ("complementary_percentages", 6): ('partial', {'exact_linear': 'partial', 'sum_constant': 'partial'}),
    ("complementary_percentages", 7): ('pervasive', {'exact_linear': 'pervasive', 'sum_constant': 'partial'}),
    ("complementary_percentages", 8): ('pervasive', {'exact_linear': 'pervasive', 'sum_constant': 'partial'}),
    ("complementary_percentages", 9): ('pervasive', {'exact_linear': 'pervasive', 'sum_constant': 'pervasive'}),
    ("complementary_percentages", 10): ('pervasive', {'exact_linear': 'pervasive', 'sum_constant': 'pervasive'}),
    ("control_normalised", 4): ('silent', {}),
    ("control_normalised", 6): ('silent', {}),
    ("control_normalised", 7): ('silent', {}),
    ("control_normalised", 8): ('pervasive', {'exact_linear': 'pervasive'}),
    ("control_normalised", 9): ('pervasive', {'exact_linear': 'pervasive'}),
    ("control_normalised", 10): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("duplicated_measurement", 4): ('pervasive', {'identical_column': 'pervasive'}),
    ("duplicated_measurement", 6): ('pervasive', {'identical_column': 'pervasive'}),
    ("duplicated_measurement", 7): ('pervasive', {'identical_column': 'pervasive'}),
    ("duplicated_measurement", 8): ('pervasive', {'identical_column': 'pervasive'}),
    ("duplicated_measurement", 9): ('pervasive', {'identical_column': 'pervasive'}),
    ("duplicated_measurement", 10): ('pervasive', {'identical_column': 'pervasive'}),
    ("null_independent", 4): ('silent', {}),
    ("null_independent", 6): ('silent', {}),
    ("null_independent", 7): ('silent', {}),
    ("null_independent", 8): ('silent', {}),
    ("null_independent", 9): ('silent', {}),
    ("null_independent", 10): ('silent', {}),
    ("percent_of_total", 4): ('silent', {}),
    ("percent_of_total", 6): ('silent', {}),
    ("percent_of_total", 7): ('silent', {}),
    ("percent_of_total", 8): ('partial', {'exact_linear': 'partial'}),
    ("percent_of_total", 9): ('pervasive', {'exact_linear': 'pervasive'}),
    ("percent_of_total", 10): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("rate_per_thousand", 4): ('silent', {}),
    ("rate_per_thousand", 6): ('silent', {}),
    ("rate_per_thousand", 7): ('silent', {}),
    ("rate_per_thousand", 8): ('pervasive', {'exact_linear': 'pervasive'}),
    ("rate_per_thousand", 9): ('pervasive', {'exact_linear': 'pervasive'}),
    ("rate_per_thousand", 10): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("sd_sem", 4): ('silent', {}),
    ("sd_sem", 6): ('silent', {}),
    ("sd_sem", 7): ('silent', {}),
    ("sd_sem", 8): ('pervasive', {'exact_linear': 'pervasive'}),
    ("sd_sem", 9): ('pervasive', {'exact_linear': 'pervasive'}),
    ("sd_sem", 10): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("sparse_shared_support", 4): ('silent', {}),
    ("sparse_shared_support", 6): ('silent', {}),
    ("sparse_shared_support", 7): ('silent', {}),
    ("sparse_shared_support", 8): ('silent', {}),
    ("sparse_shared_support", 9): ('silent', {}),
    ("sparse_shared_support", 10): ('silent', {}),
    ("two_level_coded", 4): ('pervasive', {'exact_linear': 'pervasive', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 6): ('pervasive', {'exact_linear': 'pervasive', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 7): ('pervasive', {'exact_linear': 'pervasive', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 8): ('pervasive', {'exact_linear': 'pervasive', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 9): ('pervasive', {'exact_linear': 'pervasive', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 10): ('pervasive', {'exact_linear': 'pervasive', 'small_diff_set': 'pervasive'}),
    ("unit_decadic", 4): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("unit_decadic", 6): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("unit_decadic", 7): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("unit_decadic", 8): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("unit_decadic", 9): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("unit_decadic", 10): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("unit_non_decadic", 4): ('silent', {}),
    ("unit_non_decadic", 6): ('silent', {}),
    ("unit_non_decadic", 7): ('silent', {}),
    ("unit_non_decadic", 8): ('pervasive', {'exact_linear': 'pervasive'}),
    ("unit_non_decadic", 9): ('pervasive', {'exact_linear': 'pervasive'}),
    ("unit_non_decadic", 10): ('pervasive', {'constant_ratio': 'pervasive'}),
    ("varying_fold_change", 4): ('silent', {}),
    ("varying_fold_change", 6): ('silent', {}),
    ("varying_fold_change", 7): ('silent', {}),
    ("varying_fold_change", 8): ('silent', {}),
    ("varying_fold_change", 9): ('silent', {}),
    ("varying_fold_change", 10): ('silent', {}),
}


# Copied-then-scaled columns, per (stored significant figures, all-zero baseline row).
# A RISE HERE IS THE GOAL of working on this arm; when it rises, move the benign verdicts
# above in the same commit so the trade is argued once rather than assumed twice.
#
# Two effects are visible. Precision is a ladder: an exact relation's residual spread is about
# a decade per significant figure short of full precision, so whichever arm judges the pair
# switches on between rungs. Separately, an all-zero baseline row silences rungs that are
# otherwise found, and BOTH arms are blind to it -- the ratio arm through its non-zero-divisor
# guard, the linear arm through `_isclose_rowwise`, which scales each row's tolerance by that
# row's own magnitude and so collapses to the absolute term `eps * typical_scale * 64`.
#
# Rows near a band edge flip under reseeding. Reseed before reading a movement as a size; the
# module docstring says how.
TRUE_BASELINE = {
    (4, False): ('silent', {}),
    (4, True): ('silent', {}),
    (6, False): ('silent', {}),
    (6, True): ('silent', {}),
    (7, False): ('silent', {}),
    (7, True): ('silent', {}),
    (8, False): ('pervasive', {'exact_linear': 'pervasive'}),
    (8, True): ('silent', {}),
    (9, False): ('pervasive', {'exact_linear': 'pervasive'}),
    (9, True): ('silent', {}),
    (10, False): ('pervasive', {'constant_ratio': 'pervasive'}),
    (10, True): ('silent', {}),
}

@pytest.mark.parametrize("family,digits", sorted(BENIGN_BASELINE))
def test_benign_generators_hold_their_frozen_verdict(family, digits) -> None:
    """One test per (family, PRECISION), which is what makes the size of a change legible.

    Parametrised per family alone, a family reddens as soon as any one of its rungs moves, so
    the failing count saturates at once and a threefold widening looks like a
    hundred-million-fold one. At this granularity the count grows with the size of the change.
    """
    observed = _measure_benign(family, digits)
    expected = BENIGN_BASELINE[(family, digits)]

    assert observed == expected, (
        f"benign-generator behaviour moved for {family} at {digits} significant figures: "
        f"{_direction(expected, observed)}.\n"
        f"  was: {expected}\n  now: {observed}\n"
        "Every pair in this family has an ordinary explanation and is exact by "
        "construction. LOUDER is a false-positive cost something in the same commit has to "
        "pay for. QUIETER is the goal of working on this arm, and should arrive with "
        "TRUE_BASELINE updated in the same commit. SAME VOLUME is neither -- the same pairs "
        "reported under a different kind, which is what an arm taking over from another "
        "looks like and is not by itself a cost. Regenerate with: "
        "python tests/test_column_pair_bench_baseline.py"
    )


def test_the_far_negative_controls_stay_silent() -> None:
    """The two strata that must never move at any tolerance anyone would propose.

    Independent columns hold no relation; a fold change that drifts holds a real one that is
    emphatically not a constant ratio. Reporting either would be inventing a relation rather
    than catching a benign generator.

    A control drifting only slightly -- close enough to the gate to say WHERE a widening starts
    calling a drifting ratio constant -- would be worth more than either. It has to be derived
    from the ratio gate's own statistic; one was tried against the wrong gate, so it is not
    attempted here rather than attempted wrongly again.
    """
    for family in ("null_independent", "varying_fold_change"):
        for digits in SIG_FIGURES:
            assert _measure_benign(family, digits) == ("silent", {}), (
                f"{family} reported a relation at {digits} significant figures")


def test_copied_then_scaled_detection_matches_the_recorded_gap() -> None:
    """Not a specification: a record of how much of this the detector currently sees."""
    observed = {(d, z): _measure_true(d, z) for d in SIG_FIGURES for z in (False, True)}

    assert observed == TRUE_BASELINE, (
        "detection of copied-then-scaled columns moved. If it ROSE that is the goal of this "
        "arm -- update TRUE_BASELINE and move the benign verdicts in the same commit."
    )


def test_the_bench_still_has_teeth() -> None:
    """A bench of things that must not fire is passed perfectly by a detector that never
    fires. Anchored on the copied-then-scaled side on purpose: requiring the BENIGN strata
    to keep firing would make "this arm got quieter about data with an ordinary
    explanation" -- the outcome worth working towards -- read as the bench breaking.
    """
    # Only the ratchet. Re-measuring would duplicate
    # test_copied_then_scaled_detection_matches_the_recorded_gap, which already pins the
    # whole TRUE table exactly, so `found == expected` there could not fail unless that
    # test failed first -- 1200 detector runs for no additional bit.
    detected = [d for d in SIG_FIGURES if TRUE_BASELINE[(d, False)][0] == "pervasive"]

    assert detected, (
        "TRUE_BASELINE records no detection at any precision. A bench of things that must "
        "not fire is passed perfectly by a detector that never fires, so a table "
        "regenerated to all-silent would certify exactly that. Whatever made the "
        "copied-then-scaled columns undetectable has to be understood before this table is "
        "committed in that state."
    )


if __name__ == "__main__":       # pragma: no cover - regeneration helper, not a test
    # Paste-ready tables. Iterates the FAMILIES and the LADDER, never the committed table's
    # own keys: doing the latter silently drops any family or rung added since, which is the
    # obvious mistake and a quiet one.
    print("BENIGN_BASELINE = {")
    for _f in sorted(BENIGN):
        for _d in SIG_FIGURES:
            print(f'    ("{_f}", {_d}): {_measure_benign(_f, _d)!r},')
    print("}\n")
    print("TRUE_BASELINE = {")
    for _d in SIG_FIGURES:
        for _z in (False, True):
            print(f'    ({_d}, {_z}): {_measure_true(_d, _z)!r},')
    print("}\n")
