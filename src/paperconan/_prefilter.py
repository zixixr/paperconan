"""Deterministic false-positive prefilter for relation-style findings.

Detectors intentionally emit broad signals. This layer marks obvious mechanical
false positives before human/LLM adjudication while preserving ambiguous cases
for review. Rules here must be conservative: auto-drop only when the benign
explanation is structural, and downweight when labels suggest a likely FP but
independence is still plausible.
"""
from __future__ import annotations

import re
from typing import Any


def _nums(samples: list[Any] | None) -> list[float]:
    return [
        float(v)
        for v in (samples or [])
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]


def _high_precision(samples: list[Any] | None) -> bool:
    vals = _nums(samples)
    if not vals:
        return False
    hp = sum(
        1
        for x in vals
        if "." in repr(float(x)) and len(repr(float(x)).split(".")[-1].rstrip("0")) >= 2
    )
    return hp >= max(3, len(vals) // 2)


def _is_constant(samples: list[Any] | None) -> bool:
    vals = _nums(samples)
    return len(vals) >= 2 and len(set(vals)) == 1


def _is_arith_axis(samples: list[Any] | None) -> bool:
    vals = _nums(samples)
    if len(vals) < 4:
        return False
    diffs = [round(vals[i + 1] - vals[i], 9) for i in range(len(vals) - 1)]
    return len(set(diffs)) == 1 and diffs[0] != 0


_AGG = (
    "average", "mean", "median", "std", " sd", "sd ", "stdev", "stddev",
    "s.d", "sem", "s.e.m", "variance", " var", "error", "95%", " ci",
    "sum", "total", "%", "percent", "ratio", "norm", "fold", "relative",
    "cumulative", "fraction",
)
_AXIS_LABEL_RE = re.compile(
    r"(^|[^a-z0-9])("
    r"m/z|mz|ppm|wavelength|wavenumber|wave\s*number|binding\s*energy|"
    r"retention\s*time|elution\s*volume|2theta|2θ|theta|"
    r"time|temperature|temp|voltage|potential|frequency|freq"
    r")([^a-z0-9]|$)",
    re.I,
)
_STAT_DERIVED_RE = re.compile(
    r"\b(adj(?:usted)?[._ -]?p(?:[._ -]?val(?:ue)?)?|p[._ -]?val(?:ue)?|"
    r"q[._ -]?val(?:ue)?|fdr|fwer|padj|p\.?value|adj\.?p\.?val|"
    r"bonferroni|benjamini|critical|rank|log2err|z[._ -]?score|"
    r"confidence[ _-]*interval)\b",
    re.I,
)
_QPCR_DERIVED_RE = re.compile(
    r"\b(?:dct|ddct|delta[ _-]*ct|delta[ _-]*delta[ _-]*ct|"
    r"deltacq|delta[ _-]*cq|delta[ _-]*delta[ _-]*cq|"
    r"2\^-?ddct|relative[ _-]*(?:expression|quantity|value)|rq)\b",
    re.I,
)
_SUMMARY_SCALE_RE = re.compile(r"\b(?:sem|s\.e\.m|se|sd|s\.d|std|stdev|stddev)\b", re.I)
_IMAGE_DERIVED_RE = re.compile(
    r"\b(gray|grey|intensity|pixel|invert|background|bg|normalized|normalised|"
    r"set\s*0|threshold|roi)\b",
    re.I,
)
_GENOMIC_START_RE = re.compile(
    r"(^|[^a-z])(chrom)?(motif|midpoint|block|thick)?[_ -]*(start|s1|bp)([^a-z]|$)"
    r"|start[_ -]*position",
    re.I,
)
_GENOMIC_END_RE = re.compile(
    r"(^|[^a-z])(chrom)?(motif|midpoint|block|thick)?[_ -]*(end|stop|e1|genpos)([^a-z]|$)"
    r"|end[_ -]*position",
    re.I,
)
_COORDINATE_CONTEXT_RE = re.compile(
    r"\b(?:target|peak|tss|tts|window|tile|interval|coordinate|coord|position|genomic|"
    r"chrom|crispr|guide|probe)\b",
    re.I,
)
_EXPLICIT_FORMULA_RE = re.compile(
    r"(?<![a-z0-9])(?:x|×|\*)\s+(?:\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+)\b|"
    r"(?<![a-z0-9])(?:x|×|\*)\s*10(?:\^|-)?\d*\b|"
    r"(?<![a-z0-9])(?:×|\*)\s*\d+(?:\.\d+)?\b|"
    r"\bper\s+\d+(?:\.\d+)?\b|"
    r"\b(?:startplusone|2\^-|1/koff|ln\(2\)|log2|log10)\b",
    re.I,
)
_PROBABILITY_RATE_RE = re.compile(
    r"\b(?:probability|prob|frequency|freq|rate|coverage|depth|mapping[ _-]*rate|"
    r"frac(?:tion)?(?:of)?samples?)\b",
    re.I,
)
_COMPLEMENT_LABEL_PAIRS = (
    ("with", "without"),
    ("pos", "neg"),
    ("positive", "negative"),
    ("wt", "mut"),
    ("wildtype", "mutant"),
    ("supernatant", "pellet"),
    ("in", "out"),
    ("old", "nov"),
    ("m1", "m2"),
    ("hyper", "hypo"),
    ("primary", "metastatic"),
)
_COMMON_UNIT_FACTORS = {
    1e-12, 1e-9, 1e-6, 1e-3, 1e-2,
    1e2, 1e3, 1e6, 1e9, 1e12,
}


def _derived_label(label: str | None) -> bool:
    text = " " + (label or "").lower() + " "
    return any(token in text for token in _AGG)


def _axis_label(label: str | None) -> bool:
    return bool(_AXIS_LABEL_RE.search(label or ""))


def _derived_stat_label(label: str | None) -> bool:
    return bool(_STAT_DERIVED_RE.search(label or ""))


def _image_derived_label(label: str | None) -> bool:
    return bool(_IMAGE_DERIVED_RE.search(label or ""))


def _percent_label(label: str | None) -> bool:
    text = (label or "").lower()
    return "%" in text or "percent" in text or "percentage" in text


def _sample_sums_to(sa: list[Any] | None, sb: list[Any] | None, target: float, tol: float = 1e-9) -> bool:
    va, vb = _nums(sa), _nums(sb)
    if len(va) < 3 or len(vb) < 3:
        return False
    n = min(len(va), len(vb))
    scale = max(max(abs(x) for x in va[:n] + vb[:n]), abs(target), 1.0)
    return all(abs((va[i] + vb[i]) - target) <= tol * scale for i in range(n))


def _samples_sum_constant(sa: list[Any] | None, sb: list[Any] | None, tol: float = 1e-9) -> bool:
    va, vb = _nums(sa), _nums(sb)
    if len(va) < 3 or len(vb) < 3:
        return False
    n = min(len(va), len(vb))
    sums = [va[i] + vb[i] for i in range(n)]
    target = sums[0]
    scale = max(max(abs(x) for x in sums), 1.0)
    return all(abs(x - target) <= tol * scale for x in sums[1:])


def _complement_percentage(kind: str | None, a: str | None, b: str | None,
                           sa: list[Any] | None, sb: list[Any] | None) -> bool:
    if kind != "sum_constant":
        return False
    if _percent_label(a) or _percent_label(b):
        return True
    return _sample_sums_to(sa, sb, 100.0)


def _complement_fraction(kind: str | None, _a: str | None, _b: str | None,
                         sa: list[Any] | None, sb: list[Any] | None) -> bool:
    if kind != "sum_constant":
        return False
    return _sample_sums_to(sa, sb, 1.0) or _sample_sums_to(sa, sb, 2.0)


def _tokenized_label(label: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (label or "").lower()))


def _complement_category(kind: str | None, a: str | None, b: str | None,
                         sa: list[Any] | None, sb: list[Any] | None) -> bool:
    if kind != "sum_constant" or not _samples_sum_constant(sa, sb):
        return False
    ta, tb = _tokenized_label(a), _tokenized_label(b)
    for left, right in _COMPLEMENT_LABEL_PAIRS:
        if (left in ta and right in tb) or (right in ta and left in tb):
            return True
    return ("+" in (a or "") and "-" in (b or "")) or ("-" in (a or "") and "+" in (b or ""))


def _common_unit_scale(kind: str | None, sa: list[Any] | None, sb: list[Any] | None) -> bool:
    if kind not in {"constant_ratio", "exact_linear"}:
        return False
    pairs = [(x, y) for x, y in zip(_nums(sa), _nums(sb)) if abs(x) > 1e-12 and abs(y) > 1e-12]
    if len(pairs) < 3:
        return False
    ratios = [y / x for x, y in pairs]
    ratio = sum(ratios) / len(ratios)
    if not any(abs(ratio - k) <= 1e-6 * abs(k) for k in _COMMON_UNIT_FACTORS):
        return False
    scale = max(max(abs(x) for pair in pairs for x in pair), 1.0)
    return all(abs(y - ratio * x) <= 1e-9 * scale for x, y in pairs)


def _large_integer_coordinates(sa: list[Any] | None, sb: list[Any] | None) -> bool:
    vals = _nums(sa) + _nums(sb)
    if len(vals) < 4:
        return False
    large = sum(1 for v in vals if abs(v) >= 1000)
    integerish = sum(1 for v in vals if abs(v - round(v)) <= 1e-9)
    return large >= max(4, len(vals) // 2) and integerish >= max(4, len(vals) // 2)


def _coordinate_pair(a: str | None, b: str | None, sa: list[Any] | None, sb: list[Any] | None) -> bool:
    la, lb = a or "", b or ""
    has_start_end = (
        (_GENOMIC_START_RE.search(la) and _GENOMIC_END_RE.search(lb))
        or (_GENOMIC_END_RE.search(la) and _GENOMIC_START_RE.search(lb))
    )
    if not has_start_end:
        return False
    if _large_integer_coordinates(sa, sb):
        return True
    return bool(_COORDINATE_CONTEXT_RE.search(la) or _COORDINATE_CONTEXT_RE.search(lb))


def _qpcr_derived_label(label: str | None) -> bool:
    return bool(_QPCR_DERIVED_RE.search(label or ""))


def _summary_scale_label(label: str | None) -> bool:
    return bool(_SUMMARY_SCALE_RE.search(label or ""))


def _replicate_label(label: str | None) -> bool:
    text = (label or "").lower()
    return any(token in text for token in ("replicate", "rep ", "rep.", "trial", " run", "technical"))


def _count_label(label: str | None) -> bool:
    text = " " + (label or "").lower() + " "
    return any(token in text for token in ("count", "number of", "sample size", " n=", "n =", "(n)", " n ", "ndots", " dots", "reads"))


def _explicit_formula_label(label: str | None) -> bool:
    return bool(_EXPLICIT_FORMULA_RE.search(label or ""))


def _count_to_probability_or_rate(kind: str | None, a: str | None, b: str | None) -> bool:
    if kind not in {"constant_ratio", "exact_linear"}:
        return False
    count_a, count_b = _count_label(a), _count_label(b)
    rate_a = bool(_PROBABILITY_RATE_RE.search(a or ""))
    rate_b = bool(_PROBABILITY_RATE_RE.search(b or ""))
    return (count_a and rate_b) or (count_b and rate_a)


def _low_information_sparse(kind: str | None, n: int, sa: list[Any] | None,
                            sb: list[Any] | None, high_precision: bool) -> bool:
    if kind not in {"identical_column", "constant_ratio", "constant_offset", "exact_linear"}:
        return False
    if n > 7 or high_precision:
        return False
    vals = _nums(sa) + _nums(sb)
    if len(vals) < 6:
        return False
    unique = len({round(v, 12) for v in vals})
    zeros = sum(1 for v in vals if abs(v) <= 1e-12)
    return unique <= 4 or zeros >= len(vals) // 2


def evidence_confidence(n: int, frac: float | None, high_precision: bool) -> float:
    """Legacy mechanical certainty score kept for packet compatibility.

    This is not a suspicion score and should not be used for ranking.
    """
    score = 0.4
    if n >= 5:
        score += 0.15
    if n >= 20:
        score += 0.15
    if n >= 200:
        score += 0.10
    if high_precision:
        score += 0.20
    if (frac or 1.0) >= 0.999:
        score += 0.10
    return min(round(score, 2), 0.99)


def prefilter(kind: str | None, a: str | None, b: str | None,
              _sa: list[Any] | None, _sb: list[Any] | None,
              flags: dict[str, bool]) -> tuple[str, str | None]:
    if flags["trivial"]:
        return "drop", "trivial_constant"
    if flags.get("genomic_coordinate"):
        return "drop", "genomic_coordinate_table"
    if flags.get("complement_percentage"):
        return "drop", "complement_percentage_sum_to_100"
    if flags.get("complement_fraction"):
        return "drop", "complement_fraction_sum_to_constant"
    if flags.get("complement_category"):
        return "drop", "complement_category_sum_to_constant"
    if flags.get("qpcr_derived_label"):
        return "drop", "qpcr_formula_derived_column"
    if flags.get("summary_scale_label"):
        return "drop", "summary_statistic_scaling"
    if flags.get("derived_stat_label"):
        return "downweight", "derived_statistical_column"
    if flags.get("explicit_formula_label"):
        return "drop", "explicit_formula_or_unit_conversion"
    if flags["is_axis"]:
        return "drop", "shared_axis"
    if flags.get("count_to_probability_or_rate"):
        return "drop", "count_to_probability_or_rate"
    if flags["count_label"]:
        return "drop", "n_column"
    if flags.get("image_derived_label"):
        return "drop", "image_processing_derived_column"
    if flags.get("common_unit_scale"):
        return "drop", "unit_conversion_or_normalization"
    if flags["derived_label"]:
        return "drop", "derived_normalized"
    if flags.get("low_information_sparse"):
        return "downweight", "low_information_sparse_transform"
    if flags["same_label"] or flags["replicate"]:
        return "downweight", "same_label_replicate"
    return "keep", None


def make_finding(kind: str | None, a: str | None, b: str | None, n: int,
                 frac: float | None, rule: str | None, sa: list[Any] | None,
                 sb: list[Any] | None, **extra: Any) -> dict[str, Any]:
    hp = _high_precision(sa) or _high_precision(sb)
    flags = {
        "trivial": _is_constant(sa) or _is_constant(sb),
        "is_axis": _is_arith_axis(sa) or _is_arith_axis(sb) or _axis_label(a) or _axis_label(b),
        "same_label": bool(a) and a == b,
        "derived_label": _derived_label(a) or _derived_label(b),
        "derived_stat_label": _derived_stat_label(a) or _derived_stat_label(b),
        "qpcr_derived_label": _qpcr_derived_label(a) or _qpcr_derived_label(b),
        "summary_scale_label": _summary_scale_label(a) or _summary_scale_label(b),
        "image_derived_label": _image_derived_label(a) or _image_derived_label(b),
        "complement_percentage": _complement_percentage(kind, a, b, sa, sb),
        "complement_fraction": _complement_fraction(kind, a, b, sa, sb),
        "complement_category": _complement_category(kind, a, b, sa, sb),
        "explicit_formula_label": _explicit_formula_label(a) or _explicit_formula_label(b),
        "count_to_probability_or_rate": _count_to_probability_or_rate(kind, a, b),
        "common_unit_scale": _common_unit_scale(kind, sa, sb),
        "genomic_coordinate": _coordinate_pair(a, b, sa, sb),
        "low_information_sparse": _low_information_sparse(kind, int(n or 0), sa, sb, hp),
        "replicate": _replicate_label(a) and _replicate_label(b),
        "count_label": _count_label(a) or _count_label(b),
    }
    decision, reason = prefilter(kind, a, b, sa, sb, flags)
    finding = {
        "kind": kind,
        "col_a": a,
        "col_b": b,
        "n": n,
        "rule": rule,
        "top5_a": (sa or [])[:5],
        "top5_b": (sb or [])[:5],
        "high_precision": hp,
        "mass": bool((n or 0) >= 200 or ((n or 0) >= 5 and hp)),
        "evidence_confidence": evidence_confidence(int(n or 0), frac, hp),
        "flags": flags,
        "prefilter": decision,
        "prefilter_reason": reason,
    }
    finding.update(extra)
    return finding
