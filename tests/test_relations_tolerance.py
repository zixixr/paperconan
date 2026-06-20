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
