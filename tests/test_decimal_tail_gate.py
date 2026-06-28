from __future__ import annotations

from paperconan._audit import (
    _decimal_tail_constant_transform,
    _decimal_tail_low_reason,
    _decimal_tail_note_reason,
    _dt_axis,
    _dt_few_tails,
    _dt_fixed_denominator,
    _dt_log_dilution_candidate,
    _dt_per_column_constant,
)


def _pairs(va_vb):
    # detector pairs are (key_a, key_b, value_a, value_b, tail_sig)
    return [((0, 0), (0, 0), va, vb, "sig") for va, vb in va_vb]


def _pairs_with_tails(rows):
    return [
        ((r, ca), (r, cb), va, vb, sig)
        for r, ca, cb, va, vb, sig in rows
    ]


def test_constant_offset_is_gated():
    # vb = va + 0.1 for every pair -> benign offset, tails preserved incidentally
    p = _pairs([(0.1129167, 0.2129167), (0.1195833, 0.2195833),
                (0.1329167, 0.2329167), (0.1529167, 0.2529167)])
    assert _decimal_tail_constant_transform(p) is True


def test_large_constant_offset_is_gated():
    p = _pairs([(10.5, 156.5), (20.5, 166.5), (30.5, 176.5), (40.5, 186.5)])  # +146
    assert _decimal_tail_constant_transform(p) is True


def test_constant_ratio_is_gated():
    p = _pairs([(2.0, 4.0), (3.0, 6.0), (5.0, 10.0), (7.0, 14.0)])  # x2
    assert _decimal_tail_constant_transform(p) is True


def test_irregular_differences_not_gated():
    # genuine leading-digit fabrication (38842-6 style): irregular per-pair diffs
    p = _pairs([(14.70300997, 6.70300997), (7.592733983, 4.592733983), (9.123456, 2.123456)])
    assert _decimal_tail_constant_transform(p) is False


def test_too_few_pairs_not_gated():
    assert _decimal_tail_constant_transform(_pairs([(1.0, 2.0), (2.0, 3.0)])) is False


def test_fixed_denominator_gate_detects_fractional_rate_values():
    rows = []
    for r, delta in enumerate([1, 3, 2, 4, 1, 5, 2, 6]):
        va = (r + 1) / 180
        rows.append((r, 0, 0, va, va + delta, f"tail{r}"))

    pairs = _pairs_with_tails(rows)

    assert _dt_fixed_denominator(pairs) == "fixed_denominator:1/180"
    assert _decimal_tail_low_reason(pairs) == "fixed_denominator:1/180"


def test_fixed_denominator_gate_ignores_integer_and_zero_only_values():
    pairs = _pairs([(0.0, 3.0), (1.0, 4.0), (2.0, 5.0), (10.0, 11.0)])

    assert _dt_fixed_denominator(pairs) is None


def test_axis_note_requires_monotonic_progression():
    pairs = _pairs_with_tails([
        (0, 0, 0, 0.1, 10.1, "a"),
        (1, 0, 0, 0.2, 20.1, "b"),
        (2, 0, 0, 0.3, 30.1, "c"),
        (3, 0, 0, 0.4, 40.1, "d"),
        (4, 0, 0, 0.5, 50.1, "e"),
        (5, 0, 0, 0.6, 60.1, "f"),
    ])

    assert _dt_axis(pairs) is True
    assert _decimal_tail_note_reason(pairs) == "axis_progression"


def test_axis_note_rejects_nonmonotonic_discrete_readings():
    pairs = _pairs_with_tails([
        (0, 0, 0, 1.0, 2.0, "a"),
        (1, 0, 0, 2.0, 4.0, "b"),
        (2, 0, 0, 1.0, 2.0, "c"),
        (3, 0, 0, 3.0, 6.0, "d"),
        (4, 0, 0, 2.0, 4.0, "e"),
        (5, 0, 0, 4.0, 8.0, "f"),
    ])

    assert _dt_axis(pairs) is False


def test_few_tails_note_requires_enough_pairs_and_dominant_tail():
    order = [4, 1, 8, 3, 7, 2, 9, 5, 0, 6]
    dominant = [
        (r, 0, 0, 10 + order[r] + 0.6438561897747, 20 + order[-r - 1] + 0.6438561897747, "6438561897747")
        for r in range(10)
    ]
    minority = [
        (10, 0, 0, 30.3105228564414, 40.3105228564414, "3105228564414"),
        (11, 0, 0, 31.3105228564414, 41.3105228564414, "3105228564414"),
    ]
    pairs = _pairs_with_tails(dominant + minority)

    assert _dt_few_tails(pairs) is True
    assert _decimal_tail_note_reason(pairs) == "constant_fraction_tail"
    assert _dt_few_tails(_pairs_with_tails(dominant[:8])) is False


def test_per_column_constant_gate_reports_offsets():
    pairs = _pairs_with_tails([
        (0, 1, 1, 0.123456789, -4.076543211, "123456789"),
        (1, 1, 1, 0.234567891, -3.965432109, "234567891"),
        (2, 1, 1, 0.345678912, -3.854321088, "345678912"),
        (0, 2, 2, 1.123456789, 0.423456789, "123456789"),
        (1, 2, 2, 1.234567891, 0.534567891, "234567891"),
        (2, 2, 2, 1.345678912, 0.645678912, "345678912"),
    ])

    reason = _dt_per_column_constant(pairs)

    assert reason == "per_column_constant:[c1:-4.2,c2:-0.7]"
    assert _decimal_tail_low_reason(pairs) == reason


def test_per_column_constant_gate_rejects_irregular_columns():
    pairs = _pairs_with_tails([
        (0, 1, 1, 0.11, -4.09, "11"),
        (1, 1, 1, 0.22, -3.78, "22"),
        (2, 1, 1, 0.33, -3.87, "33"),
        (0, 2, 2, 1.11, 0.41, "11"),
        (1, 2, 2, 1.22, 0.52, "22"),
        (2, 2, 2, 1.33, 0.63, "33"),
    ])

    assert _dt_per_column_constant(pairs) is None


def test_log_dilution_candidate_is_label_gated_and_note_only():
    pairs = _pairs_with_tails([
        (0, 0, 0, 3.37675071, 5.37675071, "37675071"),
        (1, 0, 0, 3.774690718, 6.774690718, "774690718"),
        (2, 0, 0, 3.552841969, 6.552841969, "552841969"),
        (3, 0, 0, 3.677780705, 7.677780705, "677780705"),
        (4, 0, 0, 4.029963223, 6.029963223, "029963223"),
        (5, 0, 0, 3.075720714, 5.075720714, "075720714"),
    ])
    labels = ({
        "column_labels": ["Growth"],
        "row_labels": ["Pst hrcC bacterial titers"],
        "nearby_labels": [],
        "text": "Growth Pst hrcC bacterial titers",
    },)

    assert _decimal_tail_low_reason(pairs) is None
    assert _dt_log_dilution_candidate(pairs, labels) == "log_or_dilution_integer_shift_candidate"
    assert _decimal_tail_note_reason(pairs, labels) == "log_or_dilution_integer_shift_candidate"
    assert _dt_log_dilution_candidate(pairs, ({"text": "unrelated endpoint"},)) is None


def test_log_dilution_candidate_matches_qpcr_and_ct_labels():
    shifts = [2, 3, 2, 4, 3, 5]
    pairs = _pairs_with_tails([
        (r, 0, 0, 20.123456 + r * 0.01, 20.123456 + r * 0.01 + shifts[r], "123456")
        for r in range(6)
    ])

    assert _dt_log_dilution_candidate(pairs, ({"text": "RT-qPCR Ct values"},)) == (
        "log_or_dilution_integer_shift_candidate"
    )


def test_genuine_seed_38842_6_is_never_gated():
    """HARD regression anchor: the user-confirmed genuine seed (10.1038/s41467-023-38842-6,
    Figure 5 <-> sp Figure 6) must NEVER be demoted. These are the real source-data pairs;
    leading integer/decimal digits were changed while the long fractional tail was preserved,
    with irregular per-pair differences and many distinct tails -- the fabrication fingerprint.
    If any threshold change starts gating this, that is a false negative and this test fails."""
    pairs = [
        ((ra, ca), (rb, cb), va, vb, sig)
        for ra, ca, rb, cb, va, vb, sig in [
            (23, 2, 20, 2, 0.808902488, 0.908902488, "08902488"),
            (23, 3, 20, 3, 0.943466105, 0.743466105, "43466105"),
            (23, 4, 20, 4, 0.98755885, 1.18755885, "8755885"),
            (23, 5, 20, 5, 0.796357993, 0.996357993, "96357993"),
            (23, 6, 20, 6, 1.463714565, 1.263714565, "63714565"),
            (23, 7, 20, 7, 14.70300997, 6.70300997, "0300997"),
            (23, 9, 20, 9, 7.592733983, 4.592733983, "92733983"),
            (23, 10, 20, 10, 9.324329476, 7.324329476, "24329476"),
        ]
    ]
    seed_labels = ({"column_labels": ["Figure 5"], "row_labels": [],
                    "nearby_labels": [], "text": ""},
                   {"column_labels": ["sp Figure 6"], "row_labels": [],
                    "nearby_labels": [], "text": ""})

    assert _decimal_tail_low_reason(pairs) is None
    assert _decimal_tail_note_reason(pairs, seed_labels) is None
