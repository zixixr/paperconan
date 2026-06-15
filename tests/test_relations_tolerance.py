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
