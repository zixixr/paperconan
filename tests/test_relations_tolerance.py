"""Scale-relative tolerances: a fixed absolute atol (1e-9) falsely flagged tiny-magnitude
data (e.g. MEG fields ~1e-14 T) as identical/linear/arithmetic. The relation detectors must
use relative precision so small-magnitude columns aren't trivially 'equal'."""
import numpy as np
from paperconan._sheet import Sheet
from paperconan._audit import detect_relations, detect_arithmetic_progression, detect_equal_pairs

def _sheet(cols):
    rows = [[f"c{j}" for j in range(len(cols))]]
    for k in range(len(cols[0])):
        rows.append([cols[j][k] for j in range(len(cols))])
    return Sheet.from_rows(rows)

def test_no_fp_on_femtotesla_scale():
    # two GENUINELY DIFFERENT columns at ~1e-14: a fixed atol=1e-9 wrongly called these identical
    a = [1.0e-14, 2.0e-14, 3.0e-14, 4.0e-14, 5.0e-14, 6.0e-14, 7.0e-14]
    b = [1.3e-14, 0.7e-14, 5.1e-14, 2.2e-14, 4.9e-14, 3.3e-14, 6.6e-14]
    s = _sheet([a, b])
    f = detect_relations(s, 1, 8, 0, 2, ["c0", "c1"])
    bad = [x for x in f if x['kind'] in ('identical_column','constant_offset','exact_linear','sum_constant')]
    assert not bad, f"false relation flags on tiny-magnitude data: {bad}"
    assert not detect_equal_pairs(s, 1, 8, 0, 2, ["c0","c1"]), "false equal-pairs on tiny data"

def test_genuine_identical_still_flags_at_any_scale():
    # identical columns must STILL flag — at tiny scale AND normal scale
    tiny = [1.0e-14, 2.0e-14, 3.0e-14, 4.0e-14, 5.0e-14, 6.0e-14]
    s1 = _sheet([tiny, list(tiny)])
    assert any(x['kind']=='identical_column' for x in detect_relations(s1,1,7,0,2,["c0","c1"]))
    normal = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]
    s2 = _sheet([normal, list(normal)])
    assert any(x['kind']=='identical_column' for x in detect_relations(s2,1,7,0,2,["c0","c1"]))

def test_arithmetic_progression_not_fp_on_tiny_noise():
    # tiny non-progression noise must NOT read as an arithmetic progression
    noise = [1.0e-14, 3.7e-14, 2.1e-14, 8.3e-14, 4.4e-14, 6.9e-14]
    s = _sheet([noise])
    assert not detect_arithmetic_progression(s,1,7,0,1,["c0"])
    # a real progression at normal scale still flags
    s2 = _sheet([[2.0,4.0,6.0,8.0,10.0,12.0]])
    assert any(x['kind']=='arithmetic_progression' for x in detect_arithmetic_progression(s2,1,7,0,1,["c0"]))

def test_nearby_genomic_position_does_not_create_constant_ratio_with_maf():
    position = [
        26030666.0, 26030328.0, 26531117.0, 26030701.0, 26030654.0,
        26531317.0, 26477782.0, 25594855.0, 26365765.0, 26631621.0,
    ]
    maf = [
        0.479865771812081, 0.483221476510067, 0.476510067114094,
        0.483221476510067, 0.483221476510067, 0.48993288590604,
        0.48993288590604, 0.493288590604027, 0.466442953020134,
        0.496644295302013,
    ]
    s = _sheet([position, maf])

    findings = detect_relations(s, 1, len(position) + 1, 0, 2, ["Position", "maf"])

    bad = [x for x in findings if x["kind"] in {"constant_ratio", "exact_linear"}]
    assert not bad, f"nearby genomic coordinates and bounded MAF values are not fixed transforms: {bad}"

def test_metadata_coordinate_row_does_not_loosen_exact_linear_tolerance():
    cg41120748_bc21 = [
        89276696.0, 0.192189246698232, 0.830456994543439, 0.471174254359565,
        0.128797390103186, 0.0840155637834975, 0.0697729457989007,
        0.488260981357202, 0.804403139030435, 0.426880065569606,
        0.805091030159345,
    ]
    cg41120749_bc11 = [
        89276717.0, 0.217214016881297, 0.897669027613707, 0.526408804387175,
        0.196871166522583, 0.0359387519803685, 0.0116139589367601,
        0.608879541042533, 0.908971334297428, 0.491393656716141,
        0.89279878393404,
    ]
    s = _sheet([cg41120748_bc21, cg41120749_bc11])

    findings = detect_relations(
        s, 1, len(cg41120748_bc21) + 1, 0, 2,
        ["cg41120748_BC21", "cg41120749_BC11"],
    )

    bad = [x for x in findings if x["kind"] in {"constant_offset", "exact_linear"}]
    assert not bad, f"one large Pos.start row must not make beta-value columns look linear: {bad}"

def test_metadata_coordinate_row_does_not_loosen_many_equal_pair_tolerance():
    cg41120748_bc21 = [
        89276696.0, 0.192189246698232, 0.830456994543439, 0.471174254359565,
        0.128797390103186, 0.0840155637834975, 0.0697729457989007,
        0.488260981357202, 0.804403139030435, 0.426880065569606,
        0.805091030159345,
    ]
    cg41120749_bc11 = [
        89276717.0, 0.217214016881297, 0.897669027613707, 0.526408804387175,
        0.196871166522583, 0.0359387519803685, 0.0116139589367601,
        0.608879541042533, 0.908971334297428, 0.491393656716141,
        0.89279878393404,
    ]
    s = _sheet([cg41120748_bc21, cg41120749_bc11])

    findings = detect_equal_pairs(
        s, 1, len(cg41120748_bc21) + 1, 0, 2,
        ["cg41120748_BC21", "cg41120749_BC11"],
    )

    assert not findings, f"one large Pos.start row must not make beta values look equal: {findings}"


# ---------------------------------------------------------------------------
# B5: integer_diff_shared_fraction — two columns reproduce each other's
# high-precision decimal fractions but differ by whole numbers that vary per row.
# ---------------------------------------------------------------------------

def _b5_oracle(a, b):
    """Independent ground truth: >=max(5,0.8n) rows differ by a (near-)integer, >=3 distinct
    high-precision (>=4 sig frac digit) shared fractions, and >=2 distinct integer offsets.
    Tolerance is per-row (at each row's own magnitude), so one extreme value can't inflate it."""
    n = len(a)
    def _is_int_diff(aa, bb):
        tol = 1e-9 * max(abs(aa), abs(bb), 1e-300)
        d = bb - aa
        return abs(d - round(d)) < tol
    diffs = [bb - aa for aa, bb in zip(a, b)]
    int_diffs = [bb - aa for aa, bb in zip(a, b) if _is_int_diff(aa, bb)]
    def sig(v):
        fv = abs(v - round(v))
        return 0 if fv < 1e-9 else len(f"{fv:.9f}".split(".")[1].rstrip("0"))
    hp = {round(aa - round(aa), 6) for aa, bb in zip(a, b)
          if _is_int_diff(aa, bb) and sig(aa) >= 4}
    return (len(int_diffs) >= max(5, round(0.8 * n))
            and len(hp) >= 3
            and len({round(d) for d in int_diffs}) >= 2)


def _kinds(a, b, labels=("c0", "c1")):
    s = _sheet([a, b])
    return detect_relations(s, 1, len(a) + 1, 0, 2, list(labels))


def test_b5_flags_shared_fraction_integer_diff_and_matches_oracle():
    a = [12.3456, 45.6789, 7.1234, 88.9012, 33.4455, 61.2367, 19.8801, 50.5051]
    shift = [3, -5, 10, 2, -7, 4, 11, -3]           # varying whole numbers
    b = [aa + s for aa, s in zip(a, shift)]         # b reproduces a's fractions exactly
    f = _kinds(a, b)
    assert any(x["kind"] == "integer_diff_shared_fraction" and x["severity"] == "high" for x in f), f
    assert _b5_oracle(a, b) is True
    # not double-reported as small_diff_set
    assert not any(x["kind"] == "small_diff_set" for x in f)


def test_b5_no_fp_on_constant_integer_offset():
    # a CONSTANT integer offset is a benign constant_offset, not the varying-shift fingerprint
    a = [12.3456, 45.6789, 7.1234, 88.9012, 33.4455, 61.2367, 19.8801, 50.5051]
    b = [aa + 5 for aa in a]
    f = _kinds(a, b)
    assert not any(x["kind"] == "integer_diff_shared_fraction" for x in f)
    assert any(x["kind"] == "constant_offset" for x in f)
    assert _b5_oracle(a, b) is False


def test_b5_no_fp_on_low_precision_half_grid():
    # shared .0/.5 fractions are low-precision (dose/score grids) — must NOT trigger B5
    a = [2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0]
    b = [aa + s for aa, s in zip(a, [3, -5, 10, 2, -7, 4, 11, -3])]
    f = _kinds(a, b)
    assert not any(x["kind"] == "integer_diff_shared_fraction" for x in f)
    assert _b5_oracle(a, b) is False


def test_b5_no_fp_on_independent_measurements():
    a = [12.3456, 45.6789, 7.1234, 88.9012, 33.4455, 61.2367, 19.8801, 50.5051]
    b = [9.8765, 41.2093, 3.5561, 82.7788, 30.1199, 58.4471, 15.2230, 47.9987]
    f = _kinds(a, b)
    assert not any(x["kind"] == "integer_diff_shared_fraction" for x in f)
    assert _b5_oracle(a, b) is False


def test_b5_no_fp_when_one_extreme_value_inflates_tolerance():
    # regression (M2-1): a single extreme value (an inf/placeholder like a 1e99 fold-change)
    # must NOT inflate the column-wide tolerance so that every row's diff reads as a whole
    # number. x and y are independent measurements whose diffs are genuinely non-integer, plus
    # one 1e99 placeholder row. With a column-wide tol = 1e-9 * max|value| ~ 1e90 every row
    # passed vacuously and the detector emitted a spurious whole-sheet integer_diff_shared_fraction;
    # a per-row tolerance rejects the normal rows.
    x = [2.3456, -1.7890, 3.0112, 0.5561, -2.8834, 1.2207, 4.9019, 1e99]
    y = [100.0, 50.0, 3000.0, 7.0, 12.0, 900.0, 45.0, 0.0]
    f = _kinds(x, y)
    assert not any(k["kind"] == "integer_diff_shared_fraction" for k in f), f
    assert _b5_oracle(x, y) is False


def test_b5_no_fp_on_large_integer_coordinate_column():
    # regression (M2-1): a big integer column (e.g. distanceToTSS up to ~3.6e6) paired with a
    # small fractional score column must not read as shared-fraction integer-diff on every row.
    frac_score = [0.31, 0.47, 0.12, 0.58, 0.09, 0.66, 0.23, 0.41, 0.77, 0.05]
    dist_to_tss = [5000.0, 128000.0, 3600000.0, 42.0, 990000.0, 17.0, 250000.0,
                   8100.0, 33.0, 1400000.0]
    f = _kinds(frac_score, dist_to_tss)
    assert not any(k["kind"] == "integer_diff_shared_fraction" for k in f), f
    assert _b5_oracle(frac_score, dist_to_tss) is False


# ---------------------------------------------------------------------------
# B4: partial_constant_offset — a long CONSECUTIVE run where B = A + k (k const,
# non-zero) while the rest of the column diverges.
# ---------------------------------------------------------------------------

def _b4_longest_run(a, b):
    diffs = [round(bb - aa, 6) for aa, bb in zip(a, b)]
    best = cur = 1
    bestval = diffs[0]
    for t in range(1, len(diffs)):
        if abs(diffs[t] - diffs[t - 1]) < 1e-9:
            cur += 1
        else:
            if cur > best:
                best, bestval = cur, diffs[t - 1]
            cur = 1
    if cur > best:
        best, bestval = cur, diffs[-1]
    return best, bestval


def _b4_oracle(a, b):
    n = len(a)
    run, val = _b4_longest_run(a, b)
    non_trivial = abs(val - round(val)) > 1e-9
    return run >= max(20, round(0.5 * n)) and run < n and abs(val) > 1e-9 and non_trivial


def test_b4_flags_partial_offset_run_and_matches_oracle():
    base = [round(0.13 * i + (i * i % 7) * 0.31, 4) for i in range(40)]
    b = list(base)
    for i in range(25):                     # first 25 rows shifted by a fixed -0.3
        b[i] = round(base[i] - 0.3, 4)
    for i in range(25, 40):                 # remaining rows diverge independently
        b[i] = round(base[i] + 0.4 + 0.05 * i, 4)
    f = _kinds(base, b)
    pc = [x for x in f if x["kind"] == "partial_constant_offset"]
    assert pc and pc[0]["severity"] == "high", f
    assert pc[0]["run_length"] >= 20
    assert _b4_oracle(base, b) is True
    # whole-column offset is constant_offset, not partial
    assert not any(x["kind"] == "constant_offset" for x in f)


def test_b4_whole_column_offset_is_constant_not_partial():
    base = [round(0.13 * i + 1.0, 4) for i in range(40)]
    b = [round(v - 0.3, 4) for v in base]
    f = _kinds(base, b)
    assert any(x["kind"] == "constant_offset" for x in f)
    assert not any(x["kind"] == "partial_constant_offset" for x in f)
    assert _b4_oracle(base, b) is False        # run == n excluded


def test_b4_short_run_not_flagged():
    base = [round(0.13 * i + (i * i % 5) * 0.27, 4) for i in range(40)]
    b = list(base)
    for i in range(12):                        # only 12 consecutive (< 20 floor)
        b[i] = round(base[i] - 0.3, 4)
    for i in range(12, 40):
        b[i] = round(base[i] + 0.5 + 0.03 * i, 4)
    f = _kinds(base, b)
    assert not any(x["kind"] == "partial_constant_offset" for x in f)
    assert _b4_oracle(base, b) is False


def test_b4_fires_at_small_magnitude_scale_relative():
    # regression: B4 used a scale-ABSOLUTE round(diff,6)/1e-9 offset test, so a genuine
    # copy-then-shift at MEG scale (~1e-14) was silently inert. It must fire scale-relatively.
    base = [round((0.13 * i + (i * i % 7) * 0.31) * 1e-14, 20) for i in range(30)]
    b = list(base)
    for i in range(22):
        b[i] = base[i] - 3e-14
    for i in range(22, 30):
        b[i] = base[i] + (0.4 + 0.05 * i) * 1e-14
    f = _kinds(base, b)
    assert any(x["kind"] == "partial_constant_offset" for x in f), "B4 must fire at ~1e-14 scale"


def test_b4_benign_integer_shift_low_precision_not_flagged():
    # a run shifted by a whole number on low-precision data (B = A + 5) is the benign case
    a = [float(10 + i) for i in range(30)]
    b = [a[i] + 5 for i in range(22)] + [a[i] + 9 + i for i in range(22, 30)]
    f = _kinds(a, b)
    assert not any(x["kind"] == "partial_constant_offset" for x in f)


def test_b5_reports_real_shared_fraction_count_not_integer_rows():
    # regression: n_shared_fraction / rule counted ALL integer-diff rows, including
    # integer-on-integer rows with no fraction. It must report only genuine shared-fraction rows.
    a = [12.3456, 45.6789, 7.1234, 88.9012, 33.4455, 61.2367, 19.8801, 50.5051,
         5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0]   # 8 fractional + 10 integer rows
    shift = [3, -5, 10, 2, -7, 4, 11, -3, 1, -2, 3, -1, 2, -3, 4, -1, 2, -3]
    b = [a[i] + shift[i] for i in range(len(a))]
    f = [x for x in _kinds(a, b) if x["kind"] == "integer_diff_shared_fraction"]
    assert f, "B5 should still fire"
    assert f[0]["n_shared_fraction"] == 8, f[0]           # not 18
    assert "8/18" in f[0]["rule"] and "high-precision" in f[0]["rule"]

def test_metadata_coordinate_row_does_not_loosen_identical_column_tolerance():
    left = [
        3_000_000_000.0, 0.10, 0.25, 0.44, 0.72, 0.91,
    ]
    right = [
        3_000_000_002.0, 0.15, 0.29, 0.41, 0.66, 0.84,
    ]
    s = _sheet([left, right])

    findings = detect_relations(s, 1, len(left) + 1, 0, 2, ["probe_a", "probe_b"])

    bad = [x for x in findings if x["kind"] == "identical_column"]
    assert not bad, f"one huge metadata row must not make small measurement rows identical: {bad}"

def test_mixed_scale_true_exact_linear_still_flags():
    x = [1_000_000_000.0, 0.10, 0.25, 0.44, 0.72, 0.91]
    y = [3 * v + 7 for v in x]
    s = _sheet([x, y])

    findings = detect_relations(s, 1, len(x) + 1, 0, 2, ["x", "y"])

    assert any(f["kind"] == "exact_linear" for f in findings), findings


# ---------------------------------------------------------------------------
# Dedupe: a pure proportional relationship (y = k*x, k != 1) is a constant_ratio.
# The linear fit of the same columns yields the same slope with a zero intercept
# (down to floating-point round-off ~ eps*scale) — an exact_linear finding that
# carries no information beyond the constant_ratio and only inflates the count.
# exact_linear must be reserved for a GENUINE (scale-significant) non-zero intercept.
# ---------------------------------------------------------------------------

def test_pure_scaling_not_double_reported_as_exact_linear():
    # mirrors the paper's Fig4g columns: y = 2.39 * x row-wise, no near-zero x.
    x = [467.61905, 453.14286, 404.38095, 364.0, 598.66667, 538.47619,
         532.38095, 510.28571, 544.57143, 375.42857, 619.2381, 715.2381]
    y = [2.39 * v for v in x]
    f = _kinds(x, y)
    assert any(k["kind"] == "constant_ratio" for k in f), f
    assert not any(k["kind"] == "exact_linear" for k in f), \
        f"pure scaling must not also be reported as exact_linear (redundant, b~=0): {f}"


def test_genuine_nonzero_intercept_still_flags_exact_linear_at_normal_scale():
    # a real affine offset (y = 3x + 7) is a DISTINCT signal constant_ratio cannot express.
    x = [12.0, 45.0, 7.0, 88.0, 33.0, 61.0, 19.0, 50.0, 27.0, 6.0]
    y = [3 * v + 7 for v in x]
    f = _kinds(x, y)
    assert any(k["kind"] == "exact_linear" for k in f), f
    assert not any(k["kind"] == "constant_ratio" for k in f), \
        f"an affine offset is not a pure ratio: {f}"


def test_pure_scaling_with_zero_in_x_still_detected():
    # edge case: constant_ratio's divide guard (all |x| > 1e-12) skips columns containing 0,
    # so the b~=0 exact_linear is the SOLE witness of the proportional relationship — the
    # dedupe must NOT drop it here (that would be a false negative).
    x = [0.0, 1.5, 3.0, 4.5, 6.0, 7.5, 9.0, 10.5, 12.0, 13.5]
    y = [2.5 * v for v in x]
    f = _kinds(x, y)
    assert not any(k["kind"] == "constant_ratio" for k in f), \
        "constant_ratio's zero-guard should skip this column"
    assert any(k["kind"] == "exact_linear" for k in f), \
        f"proportional relationship with a zero in x must still be caught: {f}"


def test_ratio_emitted_flag_does_not_leak_across_column_pairs():
    # Guard: the per-pair `ratio_emitted` flag must be re-initialized for every column
    # pair. If it were hoisted outside the pair loop, a constant_ratio on an EARLIER pair
    # (c0,c1) would leave the flag True and wrongly suppress the b~=0 exact_linear on a
    # LATER pair (c2,c3) whose constant_ratio was skipped by the divide-by-zero guard —
    # a cross-pair false negative. c2 contains a 0; c2/c3 are independent of c0/c1.
    c0 = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    c1 = [2.0 * v for v in c0]                      # pair (c0,c1): pure scaling -> constant_ratio
    c2 = [0.0, 3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0, 5.0]   # has a 0; irregular
    c3 = [2.5 * v for v in c2]                      # pair (c2,c3): pure scaling with a 0 in x
    s = _sheet([c0, c1, c2, c3])
    f = detect_relations(s, 1, len(c0) + 1, 0, 4, ["c0", "c1", "c2", "c3"])
    cr = {(x["col_a"], x["col_b"]) for x in f if x["kind"] == "constant_ratio"}
    el = {(x["col_a"], x["col_b"]) for x in f if x["kind"] == "exact_linear"}
    assert ("c0", "c1") in cr, f"pure scaling of the first pair should be a constant_ratio: {f}"
    assert ("c0", "c1") not in el, f"first pair's redundant exact_linear must be deduped: {f}"
    assert ("c2", "c3") in el, \
        f"later zero-in-x pair's exact_linear must NOT be suppressed by an earlier pair's flag: {f}"


def test_integer_diff_shared_fraction_excludes_small_denominator_column():
    # Two columns where col_b = col_a + a varied integer, and col_a's fractions are k/13
    # residues (0.076923, 0.153846, …). Those recur across integers as a quantization
    # artifact, not a copy — the >=3-distinct-fraction gate alone lets k/13 through (it has
    # 12 distinct residues), so the shared small-denominator gate must exclude it.
    a = [10 + i + (i % 13) / 13 for i in range(26)]
    b = [a[i] + (3 if i % 2 else 7) for i in range(26)]   # integer diffs {3, 7}
    s = _sheet([a, b])
    f = detect_relations(s, 1, 27, 0, 2, ["c0", "c1"])
    assert not [x for x in f if x["kind"] == "integer_diff_shared_fraction"], \
        "k/13 residue column wrongly flagged as shared-fraction copy"


def test_integer_diff_shared_fraction_still_flags_arbitrary_tails():
    # Arbitrary high-entropy shared fractions (not any small p/q) must STILL flag.
    fr = [0.316768, 0.849559, 0.647899, 0.173653, 0.508241, 0.930517]
    a = [10 + i + fr[i % len(fr)] for i in range(24)]
    b = [a[i] + (2 if i % 3 else 9) for i in range(24)]
    s = _sheet([a, b])
    f = detect_relations(s, 1, 25, 0, 2, ["c0", "c1"])
    assert [x for x in f if x["kind"] == "integer_diff_shared_fraction"], \
        "genuine arbitrary shared-fraction copy was lost"
