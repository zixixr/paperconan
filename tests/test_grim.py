"""Unit tests for the GRIM/GRIMMER pure math helpers.

The decisive test is the brute-force oracle: every (mean, sd) reachable by an
actual integer dataset MUST be reported consistent, so the detector can never
false-positive on real integer data.
"""
from __future__ import annotations

import itertools
import math

from paperconan._audit import _decimals_of, grim_consistent, grimmer_consistent


def test_decimals_of_counts_displayed_places():
    assert _decimals_of(3.45) == 2
    assert _decimals_of(3.4) == 1
    assert _decimals_of(2.0) == 0
    assert _decimals_of(5) == 0
    assert _decimals_of(0.125) == 3


def test_grim_hand_oracles():
    # mean 3.45 with n=10 is impossible: integer totals give only x.x0 means.
    assert grim_consistent(3.45, 10, 2) is False
    assert grim_consistent(3.40, 10, 1) is True
    # n=3: achievable 2-dp means are round(t/3, 2); 3.50 is not one, 3.33 is.
    assert grim_consistent(3.50, 3, 2) is False
    assert grim_consistent(3.33, 3, 2) is True


def test_grim_never_flags_achievable_integer_means():
    # Brute-force oracle: any mean from a real integer dataset must be consistent.
    for n in range(2, 8):
        for combo in itertools.combinations_with_replacement(range(0, 7), n):
            for d in (1, 2):
                mean = round(sum(combo) / n, d)
                assert grim_consistent(mean, n, d) is True, (combo, n, d, mean)


def test_grimmer_hand_oracles():
    # dataset {1,2,3}: mean=2.00, sample sd=1.00, n=3 -> consistent.
    assert grimmer_consistent(2.00, 1.00, 3, 2, 2) is True
    # same mean & n but sd 1.05 is unreachable by any integer triple -> inconsistent.
    assert grimmer_consistent(2.00, 1.05, 3, 2, 2) is False


def _sample_sd(combo, n):
    m = sum(combo) / n
    var = sum((x - m) ** 2 for x in combo) / (n - 1)
    return math.sqrt(var)


def test_grimmer_never_flags_achievable_integer_sds():
    # Brute-force oracle: any (mean, sd) from a real integer dataset that already
    # passes GRIM must also pass GRIMMER. Guarantees no false positives.
    for n in range(2, 7):
        for combo in itertools.combinations_with_replacement(range(0, 7), n):
            for d in (1, 2):
                mean = round(sum(combo) / n, d)
                sd = round(_sample_sd(combo, n), d)
                if not grim_consistent(mean, n, d):
                    continue
                assert grimmer_consistent(mean, sd, n, d, d) is True, (combo, n, d, mean, sd)
