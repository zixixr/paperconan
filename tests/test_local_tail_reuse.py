"""A locally strong decimal-tail reuse must survive dilution by the rest of the sheet.

`detect_decimal_tail_clustering` gated on two hard numbers: at least 100
high-precision values, and the top tails covering at least 40% of them. Together
they made a real signal unreachable from either direction — a panel with 24
reused values fails the count, and widening to the whole sheet to reach the count
dilutes the share below the threshold. Concentration is a property of a panel;
measuring it over a whole sheet measures the wrong thing.

Per the hard-threshold audit, this is a sample-size floor on an *exact
coincidence* test, not a power floor on a distributional one. Several
high-precision values sharing a fractional tail is already improbable; the
evidence does not come from having a hundred of them.

Every fixture here is synthetic.
"""
from __future__ import annotations

import numpy as np
import pytest

from paperconan._audit import detect_decimal_tail_clustering


def _tail_concentrated(n, n_tails=6, seed=9):
    """`n` values whose full fractional parts all differ, but whose last three
    digits fall on only `n_tails` values.

    That is what this detector looks for — a shared *tail* across otherwise
    independent numbers. Values that repeat the whole fraction are a different
    signal (and are deliberately excluded here by the distinct-fraction guard,
    which exists to keep quantized columns out).
    """
    rng = np.random.default_rng(seed)
    tails = [f"{int(rng.integers(0, 1000)):03d}" for _ in range(n_tails)]
    out = []
    for i in range(n):
        lead = f"{int(rng.integers(100, 999)):03d}"
        out.append(float(f"{int(rng.integers(1, 99))}.{lead}{tails[i % n_tails]}"))
    return out


def _independent(n, seed=11):
    rng = np.random.default_rng(seed)
    return [round(float(v), 6) for v in rng.uniform(50, 500, n)]


# ---------- the miss ----------

def test_a_local_tail_reuse_is_found_below_the_old_count_floor():
    """24 values, 8 tails each used three times — well under the old 100."""
    found = detect_decimal_tail_clustering(_tail_concentrated(24), "panel")

    assert found is not None, "a strong local tail reuse was not reported"


def test_dilution_still_hides_a_local_reuse_at_sheet_scope():
    """Records what this change does NOT fix, so the gap is not mistaken for closed.

    The audit asks for two things: significance instead of a count floor, and
    running at block/panel granularity instead of per sheet. Only the first is
    done here. Concentration is still measured over everything the sheet holds,
    so a reuse confined to one panel is diluted by the columns around it —
    which is the same failure mode as before, reached from the other side.

    Kept as a test rather than a comment because it should fail, loudly, on the
    day the granularity half lands.
    """
    core = _tail_concentrated(24)

    assert detect_decimal_tail_clustering(core, "panel") is not None
    assert detect_decimal_tail_clustering(core + _independent(96), "sheet") is None, (
        "dilution no longer hides the reuse — the granularity work has landed, "
        "so update this test and the module docstring"
    )


@pytest.mark.parametrize("n", [24, 40, 99])
def test_the_same_concentration_is_reported_at_every_size(n):
    found = detect_decimal_tail_clustering(_tail_concentrated(n), "panel")

    assert found is not None


# ---------- what must stay quiet ----------

def test_independent_values_report_nothing():
    assert detect_decimal_tail_clustering(_independent(120), "panel") is None


def test_a_quantized_column_reports_nothing():
    """Values on a common denominator share tails by construction, not by copying."""
    vals = [round(k / 8, 6) for k in range(1, 121)]

    assert detect_decimal_tail_clustering(vals, "panel") is None


def test_low_precision_values_report_nothing():
    """Two decimals cannot carry a distinctive tail; collisions are expected."""
    rng = np.random.default_rng(3)
    vals = [round(float(v), 2) for v in rng.uniform(1, 20, 120)]

    assert detect_decimal_tail_clustering(vals, "panel") is None


def test_a_single_reused_tail_is_not_enough():
    """One shared tail among many independent values is a coincidence, not a
    pattern — the validity floor the audit says to keep."""
    vals = [round(1.234567 + k * 100, 6) for k in range(3)] + _independent(60)

    assert detect_decimal_tail_clustering(vals, "panel") is None


def test_too_few_values_report_nothing():
    """Below a handful there is nothing to be concentrated."""
    assert detect_decimal_tail_clustering(_tail_concentrated(4, n_tails=2), "panel") is None


# ---------- false-positive pressure ----------

@pytest.mark.parametrize("n", [12, 24, 40, 99, 200])
def test_independent_values_stay_quiet_across_sizes(n):
    """Lowering a floor is only safe if the noise does not follow it down."""
    rng = np.random.default_rng(2024)
    for _ in range(40):
        vals = [round(float(v), 6) for v in rng.uniform(0.5, 5000, n)]
        assert detect_decimal_tail_clustering(vals, "x") is None


@pytest.mark.parametrize("n", [1000, 3000, 10000])
def test_large_sheets_of_pure_noise_stay_quiet(n):
    """Noise stays quiet at the sizes real supplements reach.

    What holds it quiet here is the concentration gate, not the null: on noise
    the top-6 share is 1-4% at these sizes, so the Poisson test is never
    reached. Setting _TAIL_SPACE back to the wrong 1000 leaves this test green,
    so it does not pin the null -- test_the_tail_space_matches_what_the_extraction_can_produce
    does that, by measuring the reachable space. Stated because an earlier
    version of this docstring claimed the opposite, and a reviewer reading it
    would take the wrong-null regime for covered.
    """
    rng = np.random.default_rng(2024)
    for _ in range(3):
        vals = [round(float(v), 6) for v in rng.uniform(0.5, 5000, n)]
        assert detect_decimal_tail_clustering(vals, "x") is None


def test_the_tail_space_matches_what_the_extraction_can_produce():
    """Pins the null itself, not just its consequences."""
    from paperconan._audit import _TAIL_SPACE

    rng = np.random.default_rng(4)
    seen = set()
    for v in rng.uniform(0.5, 5000, 50000):
        frac = f"{abs(float(v)):.10f}".rstrip("0").split(".")[-1]
        if len(frac) >= 3:
            seen.add(frac[-3:])

    # The measurement above has to reach the assertion, or this test passes for
    # a space of 810 or 729 just as happily. The trailing-zero check alone is
    # true by construction of rstrip, and comparing the constant with a literal
    # compares two literals.
    assert len(seen) == _TAIL_SPACE, (
        f"the extraction reaches {len(seen)} distinct tails, not {_TAIL_SPACE}"
    )
    assert not any(t.endswith("0") for t in seen), "a trailing zero survived rstrip"


def _benign_shapes():
    rng = np.random.default_rng(31)
    return {
        "mean_of_three_replicates": [
            round(sum(rng.uniform(1, 100) for _ in range(3)) / 3, 6) for _ in range(120)
        ],
        "common_denominator_sevenths": [round(i + (i % 7) / 7, 6) for i in range(120)],
        "percentages_summing_to_100": list(
            (lambda a: [round(float(x), 4) for x in a / a.sum() * 100])(rng.uniform(1, 9, 120))
        ),
        "log_transformed": [round(float(np.log10(v)), 6) for v in rng.uniform(1, 1e6, 120)],
        "two_decimal_readings": [round(float(v), 2) for v in rng.uniform(1, 500, 300)],
        "serial_dilution": [round(1000 / (2 ** (i % 12)), 6) for i in range(120)],
        "ratio_of_two_measurements": [
            round(float(rng.uniform(1, 20) / rng.uniform(20, 100)), 6) for _ in range(120)
        ],
    }


@pytest.mark.parametrize("name", sorted(_benign_shapes()))
def test_ordinary_derived_data_stays_quiet(name):
    """Shapes that legitimately concentrate fractional digits.

    Averaging pins tails to the residues of 1/d; a common denominator shares
    them by construction; low-precision readings have no distinctive tail at
    all. Each has to stay silent, or the notice stops meaning anything.
    """
    assert detect_decimal_tail_clustering(_benign_shapes()[name], "x") is None


@pytest.mark.parametrize("d,dec,n", [(3, 2, 30), (3, 3, 30), (6, 2, 60), (3, 3, 150)])
def test_a_mean_of_limited_precision_replicates_stays_quiet(d, dec, n):
    """The shape the count floor was accidentally hiding.

    A mean of d readings recorded at a few decimals, stored at 6dp, is an
    ordinary supplement. Its tails concentrate on the residues of 1/d, which is
    what the terminating-decimal guard exists to recognise -- but that guard
    compared against a fixed 1e-6 while a d-fold mean carries up to d * 5e-7 of
    storage error, so it admitted every d >= 3. Below 100 values the count floor
    hid it; removing the floor turned this into severity=high on a 30-row panel.

    None of the other benign fixtures reaches that guard -- four stop at the
    share gate, two at the distinct-fraction gate, one yields no high-precision
    values -- so without this the guard has no coverage at all.
    """
    rng = np.random.default_rng(20260728 + d * 100 + dec * 10 + n)
    vals = [round(float(np.mean(np.round(rng.uniform(1, 100, d), dec))), 6)
            for _ in range(n)]

    assert detect_decimal_tail_clustering(vals, "Fig 1a") is None, (
        f"a mean of {d} replicates at {dec}dp was reported as a tail signal"
    )


def test_the_distinct_fraction_ratio_did_not_tighten():
    """Lowering a floor must not raise a ratio on the way past.

    The gate is max(validity_floor, n//2). Raising the ratio to 2n/3 is stricter
    above n=75, so a panel with just over half its fractions distinct stops
    firing -- a recall loss inside a change whose whole purpose is recall.
    """
    import paperconan._audit as audit

    n, distinct = 200, 131
    assert distinct >= n // 2, "fixture must sit above the n//2 gate"
    assert distinct < (2 * n) // 3, "fixture must sit below the 2n/3 gate"

    rng = np.random.default_rng(31)
    tails = ["137", "409", "163", "829", "371", "508"]
    vals = []
    for i in range(n):
        if i < distinct:
            # `distinct` different fractional parts, each carrying one of six
            # tails so the concentration clears the share gate.
            vals.append(float(f"{round(float(rng.uniform(1, 1000)), 3):.3f}"
                              f"{tails[i % len(tails)]}"))
        else:
            # One repeated value, not six: cycling the tails here would add six
            # distinct fractions and lift the count past both gates, which is
            # how the first version of this fixture passed under either ratio.
            vals.append(float(f"{12.345:.3f}{tails[0]}"))

    n_distinct = len({f"{abs(v):.10f}".rstrip("0").split(".")[-1] for v in vals})
    assert n // 2 <= n_distinct < (2 * n) // 3, (
        f"fixture must sit between the two ratios; it has {n_distinct} distinct"
    )
    assert detect_decimal_tail_clustering(vals, "Fig 2b") is not None, (
        "a panel above the n//2 gate went silent; the ratio tightened"
    )


def test_widening_the_averaging_tolerance_did_not_cost_partial_reuse():
    """The tolerance alone would explain away 400 of 900 tails.

    Acceptance by the averaging guard depends only on the 3-digit tail, so
    widening the tolerance from 1e-6 took the unconditionally-explainable tails
    from 20 of 900 to 400 -- and a panel dominated by one of those went silent
    however improbable its concentration. The guard also requires the implied
    sums to sit on the grid a recorded reading lives on, which is what keeps the
    false-positive fix from being a recall regression.
    """
    def partial(tail, seed):
        rng = np.random.default_rng(seed)
        out = []
        for i in range(200):
            if i < 120:                      # 60% carrying one copied tail
                out.append(float(f"{round(float(rng.uniform(1, 1000)), 3):.3f}{tail}"))
            else:
                out.append(round(float(rng.uniform(1, 1000)), 6))
        return out

    tails = [f"{t:03d}" for t in range(100, 400)]
    found = sum(1 for t in tails if detect_decimal_tail_clustering(partial(t, int(t)), "p"))

    # Measured 172/300 on main and 285/300 here; the floor is set below that with
    # room for fixture drift but far above what the tolerance alone would leave.
    assert found >= 260, (
        f"only {found}/300 single-tail panels were reported; the averaging guard "
        f"is explaining away tails it should not"
    )
