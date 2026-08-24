"""A frozen behaviour baseline for column-pair relation findings.

`detect_relations` (src/paperconan/_audit.py) had no bench. tests/test_curve_bench_baseline.py
is one for the short-row detector and its own docstring says why: a redesign of a tolerance
has to be held against a number rather than against a hope. Without one, a change to this
arm was invisible to the suite, and an attempt to widen it was argued from corpus counts
that answered differently depending on which files were sampled: a small sample said one
half of the change cost nothing and a larger one said it cost dozens of findings.

The bench manufactures its own data, so the answer is known by construction:

  BENIGN  thirteen families, each an exact relation with an ordinary innocent cause -- a
          duplicated measurement, a baseline subtraction, complementary percentages, SD
          against SEM = SD/sqrt(n), a count against its share of its own total, a rate per
          thousand, a decadic and a non-decadic unit conversion, a column over one control
          value, sparse counts beside their library-size-normalised twin, a two-level coded
          pair, a fold change that DRIFTS, and two independent columns. Anything reported
          on these is a false positive.

WHAT THIRTEEN FAMILIES DOES AND DOES NOT BUY, because the count flatters the instrument.
Measured on the statistic the ratio gate itself compares, std(ratio)/|mean(ratio)|, they fall
into groups, and only one of them is on this arm's axis at all:

  * FIVE actually reach the ratio arm and sit in the sensitive band, agreeing with each other to
    within a factor of two at every rung: SD/SEM, percent-of-total, rate-per-thousand,
    non-decadic unit conversion and control normalisation. They are all y = k*x, which is the
    point (see the note over TRUE_BASELINE) but also means they are ONE cause sampled five ways.
    A change costing one benign cause and a change costing five look alike here. (How many
    STRATA that is worth depends on how many of theirs are already saturated at the committed
    baseline, so do not read a stratum count as a count of causes -- an earlier draft here read
    a clean multiple off one sweep and it does not survive a branch where the baseline differs.)
  * `sparse_shared_support` measures the same statistic and belongs to that band by it, and is
    NOT in the list above, which is the trap this note exists to flag. The arm's entry guard
    requires every divisor cell non-zero, and this family's divisor column is zero with
    probability 0.4 per row, so the arm runs on it in well under one draw in a hundred. Its
    ratio spread is therefore almost never evaluated. Read that as: what the gate does not
    compute cannot put a
    family in a band, however the statistic reads. (On a branch that splits the zero handling
    rather than voiding the pair, it does join the five -- which is the same point from the
    other side.)
  * `unit_decadic` is float-noise-constant and clears the gate on every draw; it is the one
    family here whose entire output is `constant_ratio`, so it pins that the arm still fires,
    not that it discriminates.
  * `duplicated_measurement` and `two_level_coded` have zero ratio spread and grade nothing
    about this tolerance. Not because they clear the gate -- `two_level_coded`'s divisor column
    is half zeros, so like `sparse_shared_support` it reaches the arm only on the rare draw
    with no zero in it; it reports
    through `small_diff_set` and `exact_linear`, and `duplicated_measurement` through
    `identical_column`.
  * FOUR sit orders above any plausible tolerance and are inert to it.

The resolution therefore comes from the LADDER, not from the family count: the rungs place the
same relation at different distances from the gate. Read a diff of this table accordingly, and
if a change needs to be told apart from "it cost the proportional families", the family that
would tell you apart is not in here yet.
  TRUE    copied-then-scaled columns, stored the way real exports store numbers, with and
          without an all-zero baseline row. A bench of false positives alone is passed
          perfectly by a detector that never fires, so these must stay found.

`null_independent` and `varying_fold_change` are the negative controls and are asserted
silent by name: the first holds no relation at all, the second holds a real one that is not
a constant ratio, so reporting it would be inventing a constant rather than catching a
benign generator.

Stratified by STORED SIGNIFICANT FIGURES, one rung per decade of residual spread, and one
test per (family, rung). The granularity is load-bearing rather than cosmetic: parametrised
per family instead, a whole family reddens as soon as any one rung moves, the failing count
saturates, and a three-fold widening and a hundred-million-fold one produce the identical
red. Per rung the count tracks the size of the change.

FROZEN AS A VERDICT, NOT A COUNT -- (bucket, {kind: bucket}), where a bucket is `silent`
(at or below RARE), `partial`, or `pervasive`. Counting was the first design and it
reproduced the corpus's own defect from a new source: re-deriving a count baseline at
neighbouring seeds moved a third of the strata with the detector untouched, because the
interesting strata sit ON a gate, where whether a draw fires is a coin flip that no number
of repeats turns into a constant. Note `silent` therefore means at or below RARE, not zero,
and that `partial` is a wide band: per-kind buckets narrow the blind spot rather than
removing it.

SCOPE, and what these numbers do NOT bound:

  * This measures the DETECTOR. Some of _prefilter's suppression is label-driven and cannot
    be exercised here, and some is not -- `_common_unit_scale` takes only (kind, samples).
    Treat every verdict as an upper bound on report volume, not as report volume.
  * The ratio arm has TWO tolerances and the second is a default argument:
    `np.std(ratio) < ratio_tol` and `_allclose_rowwise(y, mean_ratio * x)`, whose rtol is
    not the constant beside it. Widening only the visible one moves nothing here. Anyone
    sweeping "the ratio tolerance" has to move both or will conclude, wrongly, that this
    bench is insensitive.
  * Every block is two columns and one height, so the quadratic cost in block width and
    every row-count gate are unmeasured. Six of nine finding kinds are exercised; of the
    three absent, one needs more rows than these blocks have and two are reachable and
    simply not generated by any family here.
  * One magnitude. Magnitude is a real axis for this detector and it is not tested here at
    all -- see the note over SIG_FIGURES for what was measured and why it was not kept.

Data is generated from SEED by `random.Random`, chosen over numpy's Generator because only
the stdlib Mersenne Twister stream is stable across versions, and a gate that drifts with a
dependency upgrade is not a gate. Seeds are derived from a dense family index, never from
`hash()` (randomised per process) and never from summed code points (that checksum
collides). No real data: every value here is invented.

To iterate locally without paying for this file, `pytest -k "not bench_baseline"` deselects
it and tests/test_curve_bench_baseline.py -- but NOT tests/test_ratio_structure_bench.py, whose
name does not match. It is deliberately NOT marked for opt-in: a bench skipped by default is
green while the detector moves, which is the condition it was written to end.

Regenerate both tables with `python tests/test_column_pair_bench_baseline.py`.

Baseline measured on: eee615b (paperconan main)

Signal, not verdict: the benign strata are benign by construction, which is the point --
they bound what the detector says about data that has an ordinary explanation.
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
# fall inside the ladder rather than off one end, and a tolerance change moves some rungs and
# not others. A coarser ladder could not resolve the SIZE of a change at all -- an earlier
# three-rung version returned the identical failure set over more than seven orders of
# magnitude of tolerance. Deliberately no figures here: the spread has to be measured as
# std(ratio)/|mean(ratio)|, the statistic the ratio gate itself compares, and two earlier
# drafts of this comment quoted a different one -- so name what to measure and let a reader
# run it rather than carry a number that decays.
SIG_FIGURES = (4, 6, 7, 8, 9, 10)
# Eleven figures is excluded because it is past what a paper's exported table carries, and ten
# already sits inside the shipped ratio gate, so the ladder loses no reach by stopping there.
#
# An earlier note here gave a different and wrong reason -- that both negative controls start
# firing at eleven. They do not, at the one magnitude this bench uses: both are silent there.
# Firing appears only at a magnitude this bench never runs, and the kind that appears is
# `integer_diff_shared_fraction`, i.e. the large-magnitude diff-tolerance inflation the
# detector's own comment documents, not a ratio arm inventing a relation. Worth filing
# separately; not a reason to drop a rung.
#
# What dropping it costs, stated because the ladder is asymmetric about the gate: the shipped
# ratio tolerance sits between the two finest rungs, so the ladder grades a WIDENING across
# most of its length and a TIGHTENING hardly at all -- past a small factor the whole ratio side
# goes silent at once with no gradation. A rung finer than ten is what would fix that.
#
# One magnitude, not an axis. Three were tried and every (family, precision) group gave
# the same verdict at 1e0, 1e3 and 1e8 -- over INDEPENDENT samples, since the magnitude
# entered the seed, so the right reading is 'the verdict did not depend on it', not the
# stronger 'identical draw for draw' an earlier draft claimed and could not have meant.
# Whether the detector is scale-invariant is a real question and a different test; it
# does not belong bundled in here.



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

    NOT drawn as integers, though counts are: see the comment below. An earlier version of
    this docstring said they were, three lines above the comment explaining why they are not.
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

    NOT integers, though counts are: drawn with a fractional part so `digits` bites, for the
    same reason `_percent_of_total` is, and an earlier docstring here said "integer counts"
    while the code below drew uniforms. `magnitude` scales the counts, as it does everywhere
    else here.
    """
    counts = [0.0 if rng.random() < 0.4 else _sig(rng.uniform(1.0, 500.0) * magnitude, digits)
              for _ in range(ROWS)]
    library_size = sum(counts) or 1.0
    return counts, [_sig(v / library_size * 1e6, digits) if v else 0.0 for v in counts]


def _two_level_coded(rng, magnitude, digits):
    """A coded pair whose levels sit at the stratum's magnitude and precision.

    Hard-coding 0/5 against 0/10 made every rung of this family the same experiment, counted
    once per rung; the finding count was unchanged by the fix, but independent strata were not
    what the table had been reporting. The literal wants MORE significant figures than the
    widest rung, or rounding it is a no-op -- it currently carries exactly as many, so the
    widest rung is that no-op. One digit short, and left alone because moving it moves the
    frozen table for no gain in what this family measures.

    Which is: not the ratio tolerance, and not for the reason an earlier draft gave. It does
    not "clear the ratio gate" -- it hardly ever reaches it. Column A is half zeros by
    construction,
    and the arm's entry guard requires every divisor cell non-zero, so the arm is skipped on
    essentially every draw. (Column A also holds exactly ONE distinct non-zero level, so y/x
    would be constant at every rung if it did run.) It grades the small-diff-set and linear
    arms, and its rows are here to hold those steady -- read a move in it as a change to one of
    those, not to this arm.
    """
    hi = _sig(magnitude * 5.372681943, digits)
    x = [rng.choice((0.0, hi)) for _ in range(ROWS)]
    return x, [_sig(v * 2.0, digits) for v in x]


def _null_independent(rng, magnitude, digits):
    return _base(rng, magnitude, digits), _base(rng, magnitude, digits)


def _duplicated_measurement(rng, magnitude, digits):
    """The same measurement written into two panels. Exercises `identical_column`, which
    nothing else here touches -- and which runs BEFORE the ratio arm and ends the pair, so
    this family never reaches it. (Not "the highest-severity arm": nearly every kind this
    detector emits is `high`.)"""
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
# A stratum is frozen as a VERDICT, not a count. Counts of a sampled quantity are not
# freezable here: re-deriving the baseline at neighbouring seeds moved a third of the
# strata and reddened family tests each time, without the detector changing at all,
# because the interesting strata sit ON the tolerance boundary -- whether a given draw
# fires there is a coin flip, and no number of repeats makes a coin flip a constant. That
# is the same defect the corpus sampling had, moved to a new source.
#
# The verdict is (bucket, kinds), where the bucket is how often the pair produced anything
# and `kinds` lists only kinds seen in more than RARE of the draws. Both parts hold up under
# reseeding -- re-run `_measure_benign` / `_measure_true` with SEED perturbed to check, which
# is cheap and is how the claim should be settled rather than by a figure quoted here. Rare
# kinds are excluded because a one-in-two-hundred branch of a stratum is not its behaviour.

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
    """(bucket, {kind: bucket}) for an iterable of per-draw finding lists.

    Each kind carries its OWN bucket rather than merely being present. That NARROWS the
    blind spot and does not remove it: membership alone certified any non-zero rate as
    unchanged, per-kind bands certify anything inside a band, and `partial` is a wide one, so
    a kind can move a long way inside it and produce identical output here. Narrowing further
    was tried and rejected -- the observed rates sit inside `partial` rather than at its ends,
    so an extra edge would fall under several strata and bring back the seed-sensitivity that
    freezing counts had.
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
# RECORDED, NOT ENDORSED. Read as behaviour, not as a score.
#
# `silent` means a hit rate at or below RARE, NOT zero -- the sibling bench writes `{}` for
# "nothing at all" and that reading does not carry over here. Each kind carries its own
# bucket, so a kind can neither appear nor change rate band unnoticed.
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
    ("two_level_coded", 4): ('pervasive', {'exact_linear': 'partial', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 6): ('pervasive', {'exact_linear': 'partial', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 7): ('pervasive', {'exact_linear': 'partial', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 8): ('pervasive', {'exact_linear': 'partial', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 9): ('pervasive', {'exact_linear': 'partial', 'small_diff_set': 'pervasive'}),
    ("two_level_coded", 10): ('pervasive', {'exact_linear': 'partial', 'small_diff_set': 'pervasive'}),
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
# A RISE HERE IS THE GOAL of working on this arm, not a regression. When it rises, update
# this table and move the benign verdicts above in the same commit, so the trade is argued
# once rather than assumed twice.
#
# Two gates are visible here, with different causes. The precision one is a ladder:
# `exact_linear` fits to rtol 1e-7 and an exact relation's residual spread is about one
# decade per significant figure, so detection switches on between rungs rather than
# everywhere at once. The other is not a precision effect at all -- an all-zero baseline
# row silences a rung that is otherwise found, and the intuitive culprit, the ratio arm's
# non-zero-divisor guard, is the wrong one: deleting that guard changes nothing. The gate
# is `_isclose_rowwise`, which scales each row's tolerance by that row's own magnitude. For
# a zero row that scale is zero, the tolerance collapses to the absolute term the function
# adds -- `eps * typical_scale * 64`, stated rather than evaluated because it moves with the
# block -- and the fitted intercept clears it by orders. The effect is real but it is NOT
# uniform across the ladder, and the qualitative split is the part that holds: at eight,
# nine and ten figures every other row passes its own tolerance, so there the zero row
# really is one uninformative row vetoing eleven informative ones; at four, six and seven
# figures the other rows fail too, so the zero row is not what is holding those rungs
# silent. Per-rung margin figures are deliberately not quoted: they span several orders across
# the ladder, and three earlier drafts quoted single numbers that were not any rung's -- the
# last of them said the intercept clears the floor "by three orders", which is true at the
# finest rung and six orders out at the coarsest.
#
# Some rows sit near a band edge, and an earlier draft named the wrong ones -- it said
# `sparse_shared_support` runs "just under RARE on every rung" when most of its rungs are well
# below the cut, and it named a count of missed strata that holds at no cut. Rather than carry a
# list that decays with every reseed, the recipe -- and it is spelled out because the version
# before this one pointed at `_measure_benign`, which returns BANDS and discards every rate, so
# it could not be run:
#
#     rng = random.Random(_seed(family, digits))
#     hits = sum(1 for _ in range(REPEATS) if _pair_findings(*BENIGN[family](rng, 1.0, digits)))
#     fragile = min(abs(hits / REPEATS - RARE), abs(hits / REPEATS - (1 - RARE))) < 0.02
#
# Reseeding is the check the docstring already recommends, and it is what separates a fragile row
# from a real move. Rates are not quoted -- a quoted rate here has been wrong twice.
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

    Parametrising per family alone hid it: a family reddens as soon as any one of its rungs
    moves, so the failing-test count saturates at once -- over more than seven orders of
    magnitude of ratio tolerance the SAME families redden and the count never changes. At
    this granularity the same sweep moves a monotonically growing number of tests, so a
    proposal's size is legible and not merely its existence. Reproducing that needs the
    configuration named as well as the constants: both ratio tolerances move together, and
    the branch proposing the widening wires them to one env var. No figure is quoted here
    for that reason -- a count without its configuration is not falsifiable.
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

    Independent columns hold no relation. A fold change drifting 50% across the rows holds
    a real one that is emphatically not a constant ratio, so reporting either would not be
    catching a benign generator but inventing a relation.

    A control drifting only slightly -- close enough to a gate to say WHERE a widening
    starts calling a drifting ratio constant -- would be worth more than either of these,
    and one was tried. It was calibrated to the wrong gate: its ratio scatter floored well
    above the ratio arm's tolerance, so it did not reach that arm and tracked
    `exact_linear` instead, and the quantity frozen for it discarded the kind, so the ratio
    arm taking over left the assertion green. The margin is not quoted because the control
    it describes was deleted, which puts the figure beyond reach of anyone reading this.
    Building that control correctly means deriving the drift from the ratio gate's own
    statistic; it is not attempted here rather than attempted wrongly again.
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
