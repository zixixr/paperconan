"""False-positive profile handling.

Detectors emit raw anomaly signals. Profiles are a separate interpretation
layer that can keep, demote, or hide findings without destroying scan.json
provenance.
"""
from __future__ import annotations

import re
from typing import Iterable

from ._prefilter import make_finding as _make_relation_prefilter_finding
from .schema import Profile, VALID_PROFILES


BOUNDARY_VALUES = {-1.0, 0.0, 1.0, 100.0}
NOISY_CONTEXTS = {
    "axis_or_scan_column",
    "censoring_or_boundary_value",
    "derived_or_unit_conversion",
    "same_data_replot_or_duplicate_upload",
    "omics_or_large_matrix_boundary_flood",
}

_AXIS_RE = re.compile(
    r"\b(day|days|time|dose|concentration|conc|dilution|frequency|freq|"
    r"voltage|field|index|rank|position|ppm|theta|2theta|2θ|x)\b"
    r"|时间|剂量|浓度|频率|电压|场强|索引|坐标",
    re.I,
)
_DERIVED_RE = re.compile(
    r"\b(ng|ug|µg|mg|kg|g|ml|ul|µl|nm|um|µm|mm|cm|"
    r"percent|percentage|ratio|fraction|proportion|mean|sd|std|sem|se|"
    r"log2fc|logfc|fold|normalized|normalised|zscore|score)\b"
    r"|%|百分|比例|均值|标准差|标准误|归一|标准化|单位",
    re.I,
)
_OMICS_RE = re.compile(
    r"\b(gene|genes|protein|proteins|metabolite|metabolites|omics|"
    r"p[-_ ]?value|padj|adjusted|fdr|q[-_ ]?value|log2fc|logfc|"
    r"counts?|reads?|umi|cluster|celltype)\b",
    re.I,
)
_REplot_RE = re.compile(
    r"\b(source\s*data|source|supplementary|supp|table|data\s*for\s*figure)\b"
    r"|补充|源数据|附表",
    re.I,
)


def normalize_profile(profile: str | None) -> Profile:
    p = (profile or "review").lower()
    if p not in VALID_PROFILES:
        raise ValueError(f"unknown profile {profile!r}; expected one of {', '.join(VALID_PROFILES)}")
    return p  # type: ignore[return-value]


def initialize_profile_fields(findings: Iterable[dict]) -> None:
    for f in findings:
        f.setdefault("profile_action", "kept")
        f.setdefault("false_positive_context", [])


def _add_context(f: dict, ctx: str, reason: str | None = None) -> None:
    contexts = f.setdefault("false_positive_context", [])
    if ctx not in contexts:
        contexts.append(ctx)
    if reason and not f.get("likely_benign"):
        f["likely_benign"] = reason


def _demote_or_hide(f: dict, profile: Profile) -> None:
    if profile == "triage":
        f["severity"] = "low"
        f["profile_action"] = "hidden"
    else:
        f["severity"] = "low"
        f["profile_action"] = "demoted"


def _names_for(f: dict) -> str:
    return " ".join(str(f.get(k) or "") for k in (
        "col", "col_a", "col_b", "mean_col", "n_col", "sd_col", "sheet_a", "sheet_b", "file",
    ))


def _is_boundary_dup(f: dict) -> bool:
    if f.get("kind") != "within_col_value_duplication":
        return False
    try:
        v = float(f.get("dup_value"))
    except (TypeError, ValueError):
        return False
    if round(v, 8) in BOUNDARY_VALUES:
        return True
    names = _names_for(f)
    return abs(v - 1.0) < 1e-9 and bool(re.search(r"\bp[-_ ]?value|padj|fdr|q[-_ ]?value\b", names, re.I))


def _is_axis_finding(f: dict) -> bool:
    if f.get("kind") != "arithmetic_progression":
        return False
    try:
        step = float(f.get("step"))
    except (TypeError, ValueError):
        step = None
    if step is not None and abs(step - round(step)) < 1e-9:
        return True
    return bool(_AXIS_RE.search(_names_for(f)))


def _is_derived_relation(f: dict) -> bool:
    if f.get("kind") not in {"constant_ratio", "exact_linear", "sum_constant"}:
        return False
    return bool(_DERIVED_RE.search(_names_for(f)))


def _relation_prefilter(f: dict) -> tuple[str, str | None] | None:
    if f.get("kind") not in {
        "identical_column",
        "constant_offset",
        "constant_ratio",
        "exact_linear",
        "sum_constant",
        "many_equal_pairs",
    }:
        return None
    try:
        n = int(f.get("n") or 0)
    except (TypeError, ValueError):
        n = 0
    frac = None
    if f.get("kind") == "many_equal_pairs" and n:
        try:
            frac = float(f.get("equal")) / n
        except (TypeError, ValueError):
            frac = None
    pf = _make_relation_prefilter_finding(
        f.get("kind"),
        f.get("col_a"),
        f.get("col_b"),
        n,
        frac,
        f.get("rule"),
        f.get("col_a_sample"),
        f.get("col_b_sample"),
        slope=f.get("slope"),
        intercept=f.get("intercept"),
    )
    action = pf.get("prefilter")
    reason = pf.get("prefilter_reason")
    if action in {"drop", "downweight"} and reason:
        f["prefilter"] = action
        f["prefilter_reason"] = reason
        f["prefilter_flags"] = pf.get("flags", {})
        return str(action), str(reason)
    return None


def _is_omics_boundary_flood(f: dict, sheet_context: str = "") -> bool:
    if f.get("kind") not in {"within_col_value_duplication", "within_col_decimal_repetition"}:
        return False
    return bool(_OMICS_RE.search(" ".join([_names_for(f), sheet_context])))


def _is_same_data_replot(f: dict) -> bool:
    if f.get("kind") not in {"cross_sheet_position_identical", "cross_sheet_value_overlap"}:
        return False
    delta = f.get("delta") or {}
    if delta.get("pattern") != "perfect_dup":
        return False
    if f.get("same_figure"):
        return True
    return False


def apply_profile_to_findings(findings: Iterable[dict], profile: str | None,
                              *, sheet_context: str = "") -> None:
    profile_name = normalize_profile(profile)
    findings = list(findings)
    initialize_profile_fields(findings)
    if profile_name == "forensic":
        return

    for f in findings:
        if _is_boundary_dup(f):
            _add_context(
                f, "censoring_or_boundary_value",
                "repeated boundary values such as 0/1/-1/100 often reflect censoring, "
                "saturation, absent counts, or adjusted p-values rather than copied measurements",
            )
            _demote_or_hide(f, profile_name)
        elif _is_axis_finding(f):
            _add_context(
                f, "axis_or_scan_column",
                "this looks like a dose/time/frequency/index axis or scan column rather than measured data",
            )
            _demote_or_hide(f, profile_name)
        elif _is_derived_relation(f):
            _add_context(
                f, "derived_or_unit_conversion",
                "the exact relation is consistent with a unit conversion, complementary percentage, "
                "or derived summary statistic",
            )
            _demote_or_hide(f, profile_name)
        elif relation_decision := _relation_prefilter(f):
            action, reason = relation_decision
            ctx = (
                "deterministic_relation_prefilter"
                if action == "drop"
                else "deterministic_relation_downweight"
            )
            _add_context(
                f,
                ctx,
                f"deterministic relation prefilter matched {reason}; "
                "this pattern is usually a derived, structural, or low-information relation",
            )
            _demote_or_hide(f, profile_name)
        elif _is_same_data_replot(f):
            _add_context(
                f, "same_data_replot_or_duplicate_upload",
                "the duplicate table looks like a source-data/supplementary-table replot or duplicate upload",
            )
            _demote_or_hide(f, profile_name)
        elif _is_omics_boundary_flood(f, sheet_context):
            _add_context(
                f, "omics_or_large_matrix_boundary_flood",
                "large omics/statistical matrices commonly contain many zero, one, adjusted-p, or logFC boundary values",
            )
            _demote_or_hide(f, profile_name)
