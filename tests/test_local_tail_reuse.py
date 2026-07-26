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
    """The size where a wrong null shows up — and where real supplements live.

    The tail is read off a `rstrip("0")` string, so its last digit is never 0
    and the space is 900, not 1000. Assuming 1000 understates the expectation by
    1.111x; the Poisson test gains power with n, so that fixed bias eventually
    crosses the alpha on data with nothing wrong with it. It did: three of four
    sheets of synthetic noise reported severity=high, with an evidence line
    reading "2% of values".

    A grid that stops at 200 cannot see this. This case is the reason the grid
    goes to 10000.
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

    assert not any(t.endswith("0") for t in seen), "a trailing zero survived rstrip"
    assert _TAIL_SPACE == 900, "the null must match the reachable tail space"


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
