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
    "cumulative", "fraction", "centered", "centred", "standardized",
    "standardised", "scaled",
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
    r"aic|bic|aicc|effect[._ -]?size|lcl|ucl|"
    r"firstingroupbyenrichment|by[ _-]?logp|"
    r"confidence[ _-]*interval)\b",
    re.I,
)
_QPCR_DERIVED_RE = re.compile(
    r"\b(?:dct|ddct|delta[ _-]*ct|delta[ _-]*delta[ _-]*ct|"
    r"deltacq|delta[ _-]*cq|delta[ _-]*delta[ _-]*cq|"
    r"2\^-?ddct|relative[ _-]*(?:expression|quantity|value)|rq|"
    r"from[ _-]*equation)\b",
    re.I,
)
_SEM_LABEL_RE = re.compile(
    r"\b(?:sem|s\.e\.m|se|standard[ _-]*error|std[._ -]*error|st[._ -]*error)\b",
    re.I,
)
_SD_LABEL_RE = re.compile(
    r"\b(?:sd|s\.d|stdev|stddev|standard[ _-]*deviation)\b|\bstd\b(?![._ -]*error)",
    re.I,
)
_IMAGE_DERIVED_RE = re.compile(
    r"\b(gray|grey|intensity|pixel|invert|normalized|normalised|"
    r"set\s*0|threshold|roi|intden|rawintden|raw\s*int\s*den|integrated\s*density|"
    r"mean\s*gr[ae]y)\b",
    re.I,
)
# A single-sided correction token is only safe to auto-drop when the rest of the
# label still names the same measurement, e.g. "Absorbance" vs "Absorbance corrected".
_DERIVED_TRANSFORM_RE = re.compile(
    r"\b(?:corrected|baseline[ _-]*(?:correction|subtract(?:ion|ed)?)|"
    r"background[ _-]*(?:correction|subtract(?:ion|ed)?)|"
    r"blank[ _-]*(?:correction|subtract(?:ion|ed)?)|"
    r"bkg[ _-]*sub(?:tract(?:ion|ed)?)?|drift[ _-]*correct(?:ion|ed)?|"
    r"debleach(?:ed)?)\b",
    re.I,
)
_DERIVED_TRANSFORM_STRIP_RE = re.compile(
    r"\b(?:corrected|baseline[ _-]*(?:correction|subtract(?:ion|ed)?)|"
    r"background[ _-]*(?:correction|subtract(?:ion|ed)?)|"
    r"blank[ _-]*(?:correction|subtract(?:ion|ed)?)|"
    r"bkg[ _-]*sub(?:tract(?:ion|ed)?)?|drift[ _-]*correct(?:ion|ed)?|"
    r"debleach(?:ed)?)\b",
    re.I,
)
# Proteome Discoverer / MaxQuant export twin:  "X" vs "X (by Search Engine): Sequest HT"
_SEARCH_ENGINE_SUFFIX_RE = re.compile(r"\s*\(by\s+search\s+engine\)?:?.*$", re.I)
_GENOMIC_START_RE = re.compile(
    r"(^|[^a-z])(chrom)?(motif|midpoint|block|thick)?[_ -]*(start[lr]?|s1|bp)([^a-z]|$)"
    r"|start[_ -]*position",
    re.I,
)
_GENOMIC_END_RE = re.compile(
    r"(^|[^a-z])(chrom)?(motif|midpoint|block|thick)?[_ -]*(end[lr]?|stop|e1|genpos)([^a-z]|$)"
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
    r"precision|recall|sensitivity|specificity|frac(?:tion)?(?:of)?samples?)\b",
    re.I,
)
_INTERVAL_BOUND_CONTEXT_RE = re.compile(
    r"\b(?:limit|bound|ci|interval|parallel|outlier)\b|"
    r"\bconfidence[ _-]*interval\b|"
    r"(?:^|[_ -])(?:lcl|ucl)(?:$|[_ -])",
    re.I,
)
_LOWER_BOUND_RE = re.compile(r"\b(?:lower|low|lcl)\b|(?:^|[_ -])lwr(?:$|[_ -])", re.I)
_UPPER_BOUND_RE = re.compile(r"\b(?:upper|high|ucl)\b|(?:^|[_ -])upr(?:$|[_ -])", re.I)
_LATITUDE_RE = re.compile(r"\b(?:latitude|lat)\b", re.I)
_LONGITUDE_RE = re.compile(r"\b(?:longitude|lon|long)\b", re.I)
_INFO_CRITERION_RE = re.compile(r"^(?:aic|bic|aicc)$", re.I)
_CENTERED_STANDARDIZED_RE = re.compile(
    r"(?:^|[_\s-])(?:centered|centred|standardi[sz]ed)(?:$|[_\s-])",
    re.I,
)
_ID_TIMESTAMP_RE = re.compile(
    r"\b(?:id|identifier|accession|barcode|timestamp|time[ _-]*stamp|epoch|unix[ _-]*time|"
    r"date[ _-]*(?:time|stamp)?|sample[ _-]*(?:id|identifier)|specimen[ _-]*id)\b",
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
_GENOME_SIZE_MBP_FACTORS = {980.0, 1.0 / 980.0}
_GENOME_SIZE_GBP_FACTORS = {0.98, 1.0 / 0.98}


def _derived_label(label: str | None) -> bool:
    text = " " + (label or "").lower() + " "
    return any(token in text for token in _AGG)


def _axis_label(label: str | None) -> bool:
    return bool(_AXIS_LABEL_RE.search(label or ""))


def _derived_stat_label(label: str | None) -> bool:
    return bool(_STAT_DERIVED_RE.search(label or ""))


def _image_derived_label(label: str | None) -> bool:
    return bool(_IMAGE_DERIVED_RE.search(label or ""))


def _derived_transform_token(label: str | None) -> bool:
    return bool(_DERIVED_TRANSFORM_RE.search(label or ""))


def _substantive_label_tokens(label: str | None) -> set[str]:
    text = _DERIVED_TRANSFORM_STRIP_RE.sub(" ", label or "")
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 1 and token not in {"raw", "nominal", "value", "values", "data"}
    }


def _derived_transform_pair(a: str | None, b: str | None) -> bool:
    """One side carries a correction token and both sides name the same base measurement."""
    if _derived_transform_token(a) == _derived_transform_token(b):
        return False
    ta = _substantive_label_tokens(a)
    tb = _substantive_label_tokens(b)
    if not ta or not tb:
        return False
    return ta == tb


def _search_engine_export_twin(kind: str | None, a: str | None, b: str | None) -> bool:
    if kind not in {"identical_column", "many_equal_pairs"}:
        return False
    if not a or not b:
        return False
    if not (_SEARCH_ENGINE_SUFFIX_RE.search(a) or _SEARCH_ENGINE_SUFFIX_RE.search(b)):
        return False
    base_a = _SEARCH_ENGINE_SUFFIX_RE.sub("", a).strip().lower()
    base_b = _SEARCH_ENGINE_SUFFIX_RE.sub("", b).strip().lower()
    return bool(base_a) and base_a == base_b


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


def _exact_linear_negative_unit_intercept(slope: Any, intercept: Any, target: float,
                                          tol: float = 1e-9) -> bool:
    try:
        s = float(slope)
        b = float(intercept)
    except (TypeError, ValueError):
        return False
    return abs(s + 1.0) <= tol and abs(b - target) <= tol * max(abs(target), 1.0)


def _complement_percentage(kind: str | None, a: str | None, b: str | None,
                           sa: list[Any] | None, sb: list[Any] | None,
                           slope: Any = None, intercept: Any = None) -> bool:
    if kind not in {"sum_constant", "exact_linear"}:
        return False
    if kind == "sum_constant" and (_percent_label(a) or _percent_label(b)):
        return True
    if kind == "exact_linear" and not _exact_linear_negative_unit_intercept(slope, intercept, 100.0):
        return False
    return _sample_sums_to(sa, sb, 100.0)


def _complement_fraction(kind: str | None, _a: str | None, _b: str | None,
                         sa: list[Any] | None, sb: list[Any] | None,
                         slope: Any = None, intercept: Any = None) -> bool:
    if kind not in {"sum_constant", "exact_linear"}:
        return False
    if not (_derived_label(_a) or _derived_label(_b) or _percent_label(_a) or _percent_label(_b)):
        return False
    if kind == "exact_linear" and not (
        _exact_linear_negative_unit_intercept(slope, intercept, 1.0)
        or _exact_linear_negative_unit_intercept(slope, intercept, 2.0)
    ):
        return False
    return _sample_sums_to(sa, sb, 1.0) or _sample_sums_to(sa, sb, 2.0)


def _tokenized_label(label: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (label or "").lower()))


def _complement_category(kind: str | None, a: str | None, b: str | None,
                         sa: list[Any] | None, sb: list[Any] | None,
                         slope: Any = None, intercept: Any = None) -> bool:
    if kind not in {"sum_constant", "exact_linear"} or not _samples_sum_constant(sa, sb):
        return False
    if kind == "exact_linear" and not _exact_linear_negative_unit_intercept(
        slope, intercept, _nums(sa)[0] + _nums(sb)[0] if _nums(sa) and _nums(sb) else 0.0
    ):
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


def _genome_size_unit_conversion(kind: str | None, a: str | None, b: str | None,
                                 sa: list[Any] | None, sb: list[Any] | None) -> bool:
    if kind not in {"constant_ratio", "exact_linear"}:
        return False
    labels = f" {a or ''} {b or ''} ".lower()
    has_genome_size_context = any(token in labels for token in ("1c", "c-value", "c value", "genome"))
    has_pg = re.search(r"(^|[^a-z])pg([^a-z]|$)", labels) is not None
    has_mbp = re.search(r"(^|[^a-z])mbp([^a-z]|$)", labels) is not None
    has_gbp = re.search(r"(^|[^a-z])gbp([^a-z]|$)", labels) is not None
    pairs = [(x, y) for x, y in zip(_nums(sa), _nums(sb)) if abs(x) > 1e-12 and abs(y) > 1e-12]
    if len(pairs) < 3:
        return False
    ratios = [y / x for x, y in pairs]
    ratio = sum(ratios) / len(ratios)
    factors = set()
    if has_mbp:
        factors.update(_GENOME_SIZE_MBP_FACTORS)
    if has_gbp:
        factors.update(_GENOME_SIZE_GBP_FACTORS)
    if not factors or not any(abs(ratio - k) <= 1e-9 * max(abs(k), 1.0) for k in factors):
        return False
    scale = max(max(abs(x) for pair in pairs for x in pair), 1.0)
    if not all(abs(y - ratio * x) <= 1e-9 * scale for x, y in pairs):
        return False
    return has_genome_size_context and has_pg and (has_mbp or has_gbp)


def _median(vals: list[float]) -> float:
    ordered = sorted(vals)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _sample_ratio(sa: list[Any] | None, sb: list[Any] | None) -> float | None:
    pairs = [(x, y) for x, y in zip(_nums(sa), _nums(sb)) if abs(x) > 1e-12 and abs(y) > 1e-12]
    if len(pairs) < 3:
        return None
    ratios = [y / x for x, y in pairs]
    ratio = _median(ratios)
    scale = max(abs(ratio), 1.0)
    if not all(abs(r - ratio) <= 1e-4 * scale for r in ratios):
        return None
    return abs(ratio)


def _adjacent_column_rule(rule: str | None) -> bool:
    if not rule:
        return False
    cols = [int(x) for x in re.findall(r"col\[(\d+)\]", rule)]
    return len(cols) >= 2 and abs(cols[0] - cols[1]) == 1


def _low_context_label(label: str | None) -> bool:
    text = (label or "").strip()
    return not text or re.fullmatch(r"(?:col(?:umn)?|var|x|y)?\s*\d*", text, re.I) is not None


def _near_zero_intercept(intercept: Any) -> bool:
    if intercept is None:
        return True
    try:
        return abs(float(intercept)) <= 1e-8
    except (TypeError, ValueError):
        return False


def _sem_sd_integer_n_scaling(kind: str | None, a: str | None, b: str | None,
                              sa: list[Any] | None, sb: list[Any] | None,
                              slope: Any = None, intercept: Any = None,
                              n: int = 0, rule: str | None = None) -> bool:
    if kind not in {"constant_ratio", "exact_linear"}:
        return False
    has_sd_sem_pair = (
        (_SD_LABEL_RE.search(a or "") and _SEM_LABEL_RE.search(b or ""))
        or (_SEM_LABEL_RE.search(a or "") and _SD_LABEL_RE.search(b or ""))
    )
    unlabeled_adjacent_pair = (
        not has_sd_sem_pair
        and int(n or 0) >= 100
        and _low_context_label(a)
        and _low_context_label(b)
        and _adjacent_column_rule(rule)
    )
    if not (has_sd_sem_pair or unlabeled_adjacent_pair):
        return False
    ratio = None
    if kind == "exact_linear":
        if not _near_zero_intercept(intercept):
            return False
        try:
            ratio = abs(float(slope))
        except (TypeError, ValueError):
            ratio = None
    if ratio is None:
        ratio = _sample_ratio(sa, sb)
    if ratio is None or ratio <= 1e-12:
        return False
    ratio = min(ratio, 1.0 / ratio)
    implied_n = 1.0 / (ratio * ratio)
    nearest = round(implied_n)
    if abs(implied_n - nearest) > 0.03:
        return False
    if has_sd_sem_pair:
        return 2 <= nearest <= 200
    return 5 <= nearest <= 30


def _mostly_integerish(vals: list[float]) -> bool:
    if len(vals) < 3:
        return False
    return sum(1 for v in vals if abs(v - round(v)) <= 1e-6) >= max(3, len(vals) - 1)


def _dominant_id_timestamp_values(vals: list[float], other: list[float], label: str | None) -> bool:
    if len(vals) < 3 or len(other) < 3 or not _mostly_integerish(vals):
        return False
    abs_vals = [abs(v) for v in vals if abs(v) > 0]
    abs_other = [abs(v) for v in other]
    if not abs_vals or not abs_other:
        return False
    median_large = _median(abs_vals)
    max_other = max(abs_other)
    label_match = _ID_TIMESTAMP_RE.search(label or "") is not None
    spread = max(abs_vals) - min(abs_vals)
    # Unlabeled timestamp-like columns are restricted to epoch-millisecond scale
    # and tight local ranges; other ID/timestamp ranges require label support.
    timestamp_like = 1e12 <= median_large <= 3e12 and spread <= 1e8
    if not (label_match or timestamp_like):
        return False
    return median_large >= 1e9 and max_other / median_large <= 1e-4


def _id_timestamp_dominant_sum(kind: str | None, a: str | None, b: str | None,
                               sa: list[Any] | None, sb: list[Any] | None) -> bool:
    if kind != "sum_constant" or not _samples_sum_constant(sa, sb):
        return False
    va, vb = _nums(sa), _nums(sb)
    return _dominant_id_timestamp_values(va, vb, a) or _dominant_id_timestamp_values(vb, va, b)


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


def _replicate_label(label: str | None) -> bool:
    text = (label or "").lower()
    return any(token in text for token in ("replicate", "rep ", "rep.", "trial", " run", "technical"))


def _count_label(label: str | None) -> bool:
    text = " " + (label or "").lower() + " "
    return any(token in text for token in (
        "count", "number of", "sample size", " n=", "n =", "(n)", " n ",
        "ndots", " dots", "reads", "intersection_size", "intersection size",
    ))


def _explicit_formula_label(label: str | None) -> bool:
    return bool(_EXPLICIT_FORMULA_RE.search(label or ""))


def _signed_formula_counterpart(a: str | None, b: str | None) -> bool:
    left = re.sub(r"\s+", "", a or "")
    right = re.sub(r"\s+", "", b or "")
    if not left or not right:
        return False
    if not any(ch in left + right for ch in "/^*"):
        return False
    return right == "-" + left or left == "-" + right


def _count_to_probability_or_rate(kind: str | None, a: str | None, b: str | None) -> bool:
    if kind not in {"constant_ratio", "exact_linear"}:
        return False
    count_a, count_b = _count_label(a), _count_label(b)
    rate_a = bool(_PROBABILITY_RATE_RE.search(a or ""))
    rate_b = bool(_PROBABILITY_RATE_RE.search(b or ""))
    return (count_a and rate_b) or (count_b and rate_a)


def _interval_bound_pair(a: str | None, b: str | None) -> bool:
    la, lb = a or "", b or ""
    text = f"{la} {lb}"
    if not _INTERVAL_BOUND_CONTEXT_RE.search(text):
        return False
    return (
        (_LOWER_BOUND_RE.search(la) and _UPPER_BOUND_RE.search(lb))
        or (_UPPER_BOUND_RE.search(la) and _LOWER_BOUND_RE.search(lb))
    )


def _coordinate_label_pair(a: str | None, b: str | None) -> bool:
    la, lb = a or "", b or ""
    return (
        (_LATITUDE_RE.search(la) and _LONGITUDE_RE.search(lb))
        or (_LONGITUDE_RE.search(la) and _LATITUDE_RE.search(lb))
    )


def _information_criterion_pair(a: str | None, b: str | None) -> bool:
    return bool(_INFO_CRITERION_RE.search(a or "") and _INFO_CRITERION_RE.search(b or ""))


def _centered_or_standardized_label(a: str | None, b: str | None) -> bool:
    return bool(_CENTERED_STANDARDIZED_RE.search(a or "") or _CENTERED_STANDARDIZED_RE.search(b or ""))


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
    if flags.get("interval_bound_pair"):
        return "drop", "interval_bounds"
    if flags.get("coordinate_label_pair"):
        return "drop", "coordinate_table"
    if flags.get("information_criterion_pair"):
        return "drop", "information_criterion_columns"
    if flags.get("centered_or_standardized_label"):
        return "drop", "centered_or_standardized_column"
    if flags.get("genomic_coordinate"):
        return "drop", "genomic_coordinate_table"
    if flags.get("complement_percentage"):
        return "drop", "complement_percentage_sum_to_100"
    if flags.get("complement_fraction"):
        return "drop", "complement_fraction_sum_to_constant"
    if flags.get("complement_category"):
        return "drop", "complement_category_sum_to_constant"
    if flags.get("id_timestamp_dominant_sum"):
        return "drop", "id_timestamp_dominant_sum"
    if flags.get("qpcr_derived_label"):
        return "drop", "qpcr_formula_derived_column"
    if flags.get("summary_scale_label"):
        return "drop", "summary_statistic_scaling"
    if flags.get("search_engine_export_twin"):
        return "drop", "search_engine_export_duplicate"
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
    if flags.get("derived_transform_pair") and kind in {"constant_offset", "constant_ratio", "exact_linear"}:
        return "drop", "baseline_correction_derived"
    if flags.get("genome_size_unit_conversion"):
        return "drop", "unit_conversion_or_normalization"
    if flags.get("common_unit_scale"):
        return "drop", "unit_conversion_or_normalization"
    if flags["derived_label"]:
        if kind in {"constant_ratio", "constant_offset", "exact_linear", "sum_constant"}:
            return "downweight", "derived_normalized"
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
        "summary_scale_label": _sem_sd_integer_n_scaling(
            kind, a, b, sa, sb, extra.get("slope"), extra.get("intercept"), int(n or 0), rule
        ),
        "image_derived_label": _image_derived_label(a) or _image_derived_label(b),
        "derived_transform_pair": _derived_transform_pair(a, b),
        "search_engine_export_twin": _search_engine_export_twin(kind, a, b),
        "complement_percentage": _complement_percentage(kind, a, b, sa, sb, extra.get("slope"), extra.get("intercept")),
        "complement_fraction": _complement_fraction(kind, a, b, sa, sb, extra.get("slope"), extra.get("intercept")),
        "complement_category": _complement_category(kind, a, b, sa, sb, extra.get("slope"), extra.get("intercept")),
        "id_timestamp_dominant_sum": _id_timestamp_dominant_sum(kind, a, b, sa, sb),
        "interval_bound_pair": _interval_bound_pair(a, b),
        "coordinate_label_pair": _coordinate_label_pair(a, b),
        "information_criterion_pair": _information_criterion_pair(a, b),
        "centered_or_standardized_label": _centered_or_standardized_label(a, b),
        "explicit_formula_label": (
            _explicit_formula_label(a) or _explicit_formula_label(b)
            or _signed_formula_counterpart(a, b)
        ),
        "count_to_probability_or_rate": _count_to_probability_or_rate(kind, a, b),
        "common_unit_scale": _common_unit_scale(kind, sa, sb),
        "genome_size_unit_conversion": _genome_size_unit_conversion(kind, a, b, sa, sb),
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


# --------------------------------------------------------------------------
# within_col prefilter
#
# The within_col_value_duplication / within_col_decimal_repetition detectors
# flood high-severity output on large omics/matrix tables. The relation prefilter
# above does not apply (single-column findings, different fields). This is the
# parallel deterministic layer: it demotes structural FPs (axis/index, categorical
# or integer-coded columns, normalized ~1.0 floods, /3 decimal artifacts, per-sheet
# floods) and downweights weak signals (integer repeats, low dominance), while
# keeping genuine high-dominance repeats of precise measured values for review.
#
# Tunable thresholds (see plan + offline iteration on the cached corpus):
WC_FLOOD_K = 12        # within_col HIGH findings in one sheet/block -> matrix flood
WC_CARD_K = 8          # distinct values <= this + all-integer -> categorical/coded
WC_DOM_MIN = 0.60      # repeat dominance below this -> weak signal
WC_NEAR_UNIT = (0.9, 1.1)   # |dup_value| in this band (and != 1.0) -> normalized/corr flood
_WC_DECIMAL_THIRDS = {"33", "67", "66", "34"}   # last-2 decimals of k/3 fractions

_WC_CATEGORICAL_RE = re.compile(
    r"\b(community|communit\w*|mode|cluster|type|group|grade|stage|class|categor\w*|"
    r"label|id|index|rank|bin|level|state|status|condition|genotype|cohort|batch|"
    r"replicate|well|plate|channel|count|number|reads?|order|flag|module|partition)\b"
    r"|分类|簇|类型|分组|等级|分期|索引|批次|计数|编号",
    re.I,
)
_WC_AXIS_RE = re.compile(
    r"\b(day|days|time|hour|hours|hr|hrs|min|mins|week|weeks|month|months|year|years|age|"
    r"dose|concentration|conc|dilution|distance|depth|position|coordinate|coord|"
    r"step|cycle|frame|wavelength|frequency|freq|voltage|temperature|temp)\b"
    r"|时间|剂量|浓度|位置|坐标|波长|频率|温度|天数|周|月|年",
    re.I,
)


def _wc_name(f: dict[str, Any]) -> str:
    return " ".join(str(f.get(k) or "") for k in ("col", "rule"))


def prefilter_within_col(f: dict[str, Any],
                         sheet_high_count: int | None = None) -> tuple[str, str | None]:
    """Deterministic FP filter for within_col_* findings.

    Returns (decision, reason) with decision in {"drop", "downweight", "keep"}.
    `sheet_high_count` is the number of within_col HIGH findings in the same
    sheet/block (enables the matrix-flood gate); pass None to disable it.
    Relies on the detector enrichment fields all_integer / n_distinct / frac_repeat.
    """
    kind = f.get("kind")
    if kind not in {"within_col_value_duplication", "within_col_decimal_repetition"}:
        return "keep", None

    name = _wc_name(f)
    all_integer = bool(f.get("all_integer"))
    n_distinct = f.get("n_distinct")
    n_distinct = n_distinct if isinstance(n_distinct, int) else None
    try:
        frac_repeat = float(f["frac_repeat"]) if f.get("frac_repeat") is not None else None
    except (TypeError, ValueError):
        frac_repeat = None

    # 1) per-sheet within_col flood -> correlation/omics matrix dump, demote the noise
    if sheet_high_count is not None and sheet_high_count >= WC_FLOOD_K:
        return "drop", "within_col_sheet_flood"
    # 2) axis / index / scan column by name
    if _axis_label(name) or _WC_AXIS_RE.search(name):
        return "drop", "axis_or_index_column"
    # 3) count / categorical column by name
    if _count_label(name) or _WC_CATEGORICAL_RE.search(name):
        return "drop", "count_or_categorical_column"
    # 4) integer-valued + low cardinality -> categorical / coded column
    if all_integer and n_distinct is not None and n_distinct <= WC_CARD_K:
        return "drop", "categorical_integer_column"
    # 5) integer-valued otherwise -> counts repeat naturally; weak, keep visible but demote
    if all_integer:
        return "downweight", "integer_valued_repeat"
    # 6) normalized / correlation flood: precise value clustered near 1.0 (e.g. 0.99x)
    if kind == "within_col_value_duplication":
        try:
            dv = abs(float(f.get("dup_value")))
        except (TypeError, ValueError):
            dv = None
        if dv is not None and WC_NEAR_UNIT[0] <= dv <= WC_NEAR_UNIT[1] and abs(dv - 1.0) > 1e-9:
            return "drop", "normalized_near_unit"
    # 7) decimal repetition. This is the strongest fabrication signal (manufactured digits),
    #    so only HARD-drop when the benign explanation is unambiguous: an explicitly
    #    proportion/percent column, or a low-cardinality /3-family fraction column. A
    #    high-cardinality precise column that merely shares a /3 ending is kept for the
    #    judge (downweighted, not dropped) so genuine signals are never lost.
    if kind == "within_col_decimal_repetition":
        if _derived_label(name) or _derived_stat_label(name) or _percent_label(name):
            return "drop", "derived_fraction_decimal"
        if str(f.get("ending")) in _WC_DECIMAL_THIRDS:
            # only HARD-drop a constant / two-value fraction column; any column with >=3
            # distinct precise values sharing a /3 ending is kept for the judge (the
            # manufactured-digit signal lives here), downweighted not dropped.
            if n_distinct is not None and n_distinct <= 2:
                return "drop", "derived_fraction_decimal"
            return "downweight", "shared_decimal_ending"
    # 8) weak repeat dominance -> downweight (only ~half the column repeats)
    if frac_repeat is not None and frac_repeat < WC_DOM_MIN:
        return "downweight", "weak_repeat_dominance"
    # 9) low-information column (<=2 distinct values)
    if n_distinct is not None and n_distinct <= 2:
        return "downweight", "low_information_column"
    return "keep", None
