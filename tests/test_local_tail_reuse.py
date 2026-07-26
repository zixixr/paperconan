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


def test_the_signal_survives_dilution_by_the_rest_of_the_sheet():
    """Reaching the old count meant widening scope, which killed the share.

    The reuse is the same; only unrelated values around it changed. A detector
    whose answer flips on that is measuring the sheet, not the signal.
    """
    core = _tail_concentrated(24)

    tight = detect_decimal_tail_clustering(core, "panel")
    diluted = detect_decimal_tail_clustering(core + _independent(4 * len(core)), "sheet")

    assert tight is not None
    assert diluted is not None, "the reuse vanished once unrelated values were added"


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
    """Lowering a floor is only safe if the noise does not follow it down.

    Measured over 400 trials per size before this was pinned: zero reports.
    """
    rng = np.random.default_rng(2024)
    for _ in range(40):
        vals = [round(float(v), 6) for v in rng.uniform(0.5, 5000, n)]
        assert detect_decimal_tail_clustering(vals, "x") is None


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
