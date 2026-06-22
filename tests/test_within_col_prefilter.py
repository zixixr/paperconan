"""within_col FP-reduction: detector enrichment + prefilter_within_col rules.

The within_col detectors (value-duplication / decimal-repetition) flood high-severity
output on large omics/matrix tables. The current profile already demotes ~83% via
boundary/omics rules; these tests pin (a) the cheap detector enrichment that lets a
prefilter decide precisely, and (b) the deterministic within_col prefilter that demotes
the remaining structural FPs (categorical/integer columns, normalized ~1.0 floods, /3
decimal artifacts, per-sheet floods) while KEEPING genuine high-dominance repeats.
"""
import numpy as np
from paperconan._sheet import Sheet
from paperconan._audit import detect_within_column_patterns


def _sheet(cols, headers=None):
    headers = headers or [f"c{j}" for j in range(len(cols))]
    rows = [list(headers)]
    for k in range(len(cols[0])):
        rows.append([cols[j][k] for j in range(len(cols))])
    return Sheet.from_rows(rows)


def _detect(col, header="c0"):
    s = _sheet([col], [header])
    return detect_within_column_patterns(s, 1, len(col) + 1, 0, 1, [header])


# ----------------------- detector enrichment -----------------------

def test_value_duplication_finding_is_enriched():
    col = [5, 5, 5, 5, 5, 5, 1, 2, 3]  # 5 repeated 6/9; all integer; 4 distinct
    vd = [f for f in _detect(col) if f["kind"] == "within_col_value_duplication"]
    assert vd, "expected a value-duplication finding"
    g = vd[0]
    assert g["n_distinct"] == 4
    assert g["all_integer"] is True
    assert abs(g["frac_repeat"] - 6 / 9) < 1e-9
    assert isinstance(g["value_sample"], list) and 5.0 in g["value_sample"]


def test_decimal_repetition_finding_is_enriched():
    col = [1.33, 2.33, 3.33, 4.33, 5.33, 6.33, 7.33, 8.33, 9.01]  # 8/9 share '.33'
    dr = [f for f in _detect(col) if f["kind"] == "within_col_decimal_repetition"]
    assert dr, "expected a decimal-repetition finding"
    g = dr[0]
    assert g["all_integer"] is False
    assert abs(g["frac_repeat"] - 8 / 9) < 1e-9
    assert isinstance(g["value_sample"], list) and len(g["value_sample"]) >= 1


# ----------------------- prefilter_within_col rules -----------------------
from paperconan._prefilter import prefilter_within_col


def _vd(col="", dup_value=3.7, frac_repeat=0.8, all_integer=False, n_distinct=10, **kw):
    f = dict(kind="within_col_value_duplication", col=col, dup_value=dup_value,
             frac_repeat=frac_repeat, all_integer=all_integer, n_distinct=n_distinct,
             rule=f"col has value {dup_value} repeated")
    f.update(kw)
    return f


def _dr(col="", ending="19", frac_repeat=0.9, n_distinct=20, **kw):
    f = dict(kind="within_col_decimal_repetition", col=col, ending=ending,
             frac_repeat=frac_repeat, all_integer=False, n_distinct=n_distinct,
             rule=f"values share last-2 decimals '.{ending}'")
    f.update(kw)
    return f


def test_rule_sheet_flood_drops():
    assert prefilter_within_col(_vd(), sheet_high_count=15) == ("drop", "within_col_sheet_flood")


def test_rule_axis_or_index_drops():
    assert prefilter_within_col(_vd(col="Day")) == ("drop", "axis_or_index")


def test_rule_count_or_categorical_drops():
    assert prefilter_within_col(_vd(col="Community"))[1] == "categorical_or_integer_code"
    assert prefilter_within_col(_vd(col="cluster id"))[1] == "categorical_or_integer_code"


def test_rule_derived_or_model_output_drops():
    # computed / model-output / derived / percentage columns are format artifacts (Phase-2 taxonomy)
    assert prefilter_within_col(_vd(col="predicted score"))[1] == "derived_or_model_output"
    assert prefilter_within_col(_vd(col="mean intensity"))[1] == "derived_or_model_output"
    assert prefilter_within_col(_dr(col="percent positive", ending="11"))[1] == "derived_or_model_output"


def test_rule_any_integer_column_drops():
    # integer-valued column = counts/codes/categories; genuine signals are non-integer
    assert prefilter_within_col(_vd(col="", all_integer=True, n_distinct=4)) == ("drop", "categorical_or_integer_code")
    assert prefilter_within_col(_vd(col="signal", all_integer=True, n_distinct=60)) == ("drop", "categorical_or_integer_code")


def test_rule_normalized_near_unit_drops():
    d = prefilter_within_col(_vd(col="", dup_value=0.9977, all_integer=False, n_distinct=25))
    assert d == ("drop", "normalized_or_fold_change")


def test_rule_fixed_denominator_drops():
    # values consistent with k/N (here k/6) -> sample-size / ratio arithmetic, not manufactured
    d = prefilter_within_col(_dr(col="x", ending="67", n_distinct=8,
                                 value_sample=[0.667, 0.333, 0.5, 0.833, 0.167]))
    assert d == ("drop", "fixed_denominator")


def test_rule_low_n_downweights():
    # too few rows to judge -> insufficient context (kept visible, not dropped)
    d = prefilter_within_col(_vd(col="abundance", dup_value=3.71, n=7, frac_repeat=0.9, n_distinct=6))
    assert d == ("downweight", "low_n_or_insufficient_context")


def test_rule_multivalue_shared_decimal_ending_downweights_not_drops():
    # >=3 distinct precise values sharing a /3 ending is the genuine manufactured-digit
    # signal -> must NOT be hard-dropped; downweighted so it still reaches the judge
    assert prefilter_within_col(_dr(ending="33", n_distinct=4, frac_repeat=0.7)) == ("downweight", "shared_decimal_ending")
    assert prefilter_within_col(_dr(ending="67", n_distinct=12, frac_repeat=0.8)) == ("downweight", "shared_decimal_ending")


def test_rule_low_dominance_downweights():
    d = prefilter_within_col(_vd(col="abundance", dup_value=3.71, frac_repeat=0.5, all_integer=False, n_distinct=20))
    assert d == ("downweight", "weak_repeat_dominance")


def test_rule_low_cardinality_downweights():
    # few-value (n_distinct<=4) non-integer column -> too low-cardinality to be a keep
    d = prefilter_within_col(_vd(col="measure", dup_value=3.71, frac_repeat=0.7, all_integer=False, n_distinct=3))
    assert d == ("downweight", "low_cardinality_column")


# --- KEEP reflexes: genuine high-dominance precise repeats must survive ---

def test_keep_genuine_value_duplication():
    # precise non-integer value repeated in most rows of a measurement column, no flood
    d = prefilter_within_col(_vd(col="OD600", dup_value=0.4523, frac_repeat=0.9,
                                 all_integer=False, n_distinct=10), sheet_high_count=2)
    assert d == ("keep", None)


def test_keep_genuine_decimal_repetition():
    # shared last-2 decimals not explained by /3 or a derived label, high dominance
    d = prefilter_within_col(_dr(col="value", ending="19", frac_repeat=0.95, n_distinct=30),
                             sheet_high_count=3)
    assert d == ("keep", None)


def test_non_within_col_kind_is_passthrough():
    assert prefilter_within_col(dict(kind="identical_column")) == ("keep", None)


# ----------------------- profile integration (flood gate end-to-end) -----------------------
from paperconan._profiles import apply_profile_to_findings
from paperconan._prefilter import WC_FLOOD_K


def _wc_high(col, dup_value=3.7, frac_repeat=0.9, all_integer=False, n_distinct=20):
    return dict(kind="within_col_value_duplication", col=col, dup_value=dup_value,
                frac_repeat=frac_repeat, all_integer=all_integer, n_distinct=n_distinct,
                severity="high", rule=f"col[{col}] has value {dup_value} repeated")


def test_profile_review_demotes_within_col_flood():
    # each finding alone would be KEPT; WC_FLOOD_K of them in one block -> matrix flood -> all demoted
    block = [_wc_high(f"c{i}") for i in range(WC_FLOOD_K)]
    apply_profile_to_findings(block, "review")
    assert all(f["severity"] == "low" and f["profile_action"] == "demoted" for f in block)
    assert all(any("within_col" in c for c in f.get("false_positive_context", [])) for f in block)


def test_profile_forensic_keeps_within_col_flood_high():
    block = [_wc_high(f"c{i}") for i in range(WC_FLOOD_K)]
    apply_profile_to_findings(block, "forensic")
    assert all(f["severity"] == "high" and f["profile_action"] == "kept" for f in block)


def test_profile_review_keeps_genuine_survivor():
    block = [_wc_high("OD600", dup_value=0.4523, frac_repeat=0.9, n_distinct=10)]
    apply_profile_to_findings(block, "review")
    assert block[0]["severity"] == "high" and block[0]["profile_action"] == "kept"


# ----------------------- regression guard -----------------------
# These genuine-signal decimal-repetition findings (oracle-confirmed on the 69-paper
# offline corpus) MUST keep reaching the judge — a rule change that hard-drops any of
# them is a false negative. Frozen from within_col_regression.json.
import pytest

# Genuine-signal survivors whose patterns resist EVERY computational explanation. The k/N rule
# (Phase 2) correctly reclassified DN_sen (k/30), Cre+ (k/40), 0.05 (k/300) and Ctrl (8 values all
# exactly k/75) as benign sample-size/ratio arithmetic, so they left the guard. What remains must
# stay reachable by the judge (kept or downweighted, never hard-dropped).
_REGRESSION = [
    dict(kind="within_col_decimal_repetition", col="dp", ending="78", n_distinct=4, frac_repeat=0.75, all_integer=False,
         value_sample=[0.127, 0.278, 0.778, 0.878]),
    dict(kind="within_col_decimal_repetition", col="fracCorNeg", ending="52", n_distinct=6, frac_repeat=0.86, all_integer=False,
         value_sample=[0.9995, 1.0, 0.8, 0.7, 0.73, 0.9667]),
]


@pytest.mark.parametrize("rec", _REGRESSION, ids=[r["col"] for r in _REGRESSION])
def test_regression_genuine_signals_are_never_hard_dropped(rec):
    rec = dict(rec, rule=f"col {rec['col']} shares last-2 decimals")
    decision, _reason = prefilter_within_col(rec, sheet_high_count=None)
    assert decision != "drop", f"genuine signal {rec['col']} would be hard-dropped (false negative)"


# ----------------------- per-sheet within_col flood gate (_audit) -----------------------
from paperconan._audit import _demote_within_col_flood, WITHIN_COL_SHEET_CAP


def test_per_sheet_within_col_flood_demotes_all():
    # a sheet with > cap within_col findings is a repetitive data table -> demote wholesale
    flood = [dict(kind="within_col_value_duplication", severity="high", prefilter="keep")
             for _ in range(WITHIN_COL_SHEET_CAP + 5)]
    _demote_within_col_flood(flood)
    assert all(f["severity"] == "low" and f["prefilter"] == "drop"
               and f["prefilter_reason"] == "within_col_sheet_flood" for f in flood)


def test_per_sheet_within_col_below_cap_untouched():
    few = [dict(kind="within_col_value_duplication", severity="high", prefilter="keep") for _ in range(3)]
    _demote_within_col_flood(few)
    assert all(f["severity"] == "high" and f["prefilter"] == "keep" for f in few)
