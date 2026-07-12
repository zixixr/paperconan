"""Review-packet distillation helpers.

This module turns the full `scan_dir()` output into compact findings suitable
for downstream review systems. It is infrastructure-agnostic: no Blob, DB, DOI
claiming, or cloud-worker assumptions live here.
"""
from __future__ import annotations

from typing import Any

from ._audit import BLOCK_FINDING_GROUPS
from ._prefilter import evidence_confidence
from ._profiles import _is_axis_finding
from .detectors import prefilter_relation_finding

# Groups distilled generically here (severity-only) = the canonical per-block groups MINUS the
# ones with dedicated distillers (relations/equal_pairs via _distill_relations, within_col via
# _distill_within_col). Derived from BLOCK_FINDING_GROUPS so a newly-added group flows in here
# automatically (the whole point of the single-source-of-truth constant).
_SPECIALIZED_GROUPS = {"relations", "equal_pairs", "within_col"}
_SIMPLE_BLOCK_GROUPS = tuple(g for g in BLOCK_FINDING_GROUPS if g not in _SPECIALIZED_GROUPS)


def _relation_finding(kind: str | None, a: str | None, b: str | None, n: int,
                      frac: float | None, rule: str | None,
                      sa: list[Any] | None, sb: list[Any] | None,
                      **extra: Any) -> dict[str, Any]:
    return prefilter_relation_finding(kind, a, b, n, frac, rule, sa, sb, **extra)


def _distill_cross_sheet(scan: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for f in scan.get("cross_sheet_findings", []) or []:
        if str(f.get("severity")).lower() != "high":
            continue
        n = int(f.get("same_position_count") or f.get("size_a") or 0)
        # Preserve the decimal-tail-reuse identity: a long fractional tail shared across
        # sheets is a near-zero-chance fabrication fingerprint, distinct from a generic
        # value_tweaked partial overlap — it must not be relabeled away (or the judge sees
        # only "a small partial overlap" and dismisses it as benign).
        if f.get("kind") == "cross_sheet_decimal_tail_reuse":
            distilled_kind = "cross_sheet:decimal_tail_reuse"
        else:
            distilled_kind = "cross_sheet:" + str((f.get("delta") or {}).get("pattern") or "")
        findings.append(_relation_finding(
            distilled_kind,
            f.get("sheet_a"),
            f.get("sheet_b"),
            n,
            f.get("fraction_of_smaller"),
            f.get("rule"),
            None,
            None,
            figure_a=f.get("figure_a"),
            figure_b=f.get("figure_b"),
            same_figure=f.get("same_figure"),
            file_a=f.get("file_a"),
            file_b=f.get("file_b"),
            label_context_a=f.get("label_context_a"),
            label_context_b=f.get("label_context_b"),
            shared_context=f.get("shared_context"),
            tail_benign_reason=f.get("tail_benign_reason"),
        ))
    return findings


def _distill_relations(scan: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for block in scan.get("relations_blocks", []) or []:
        relations = (block.get("relations", []) or []) + (block.get("equal_pairs", []) or [])
        for r in relations:
            if str(r.get("severity")).lower() != "high":
                continue
            findings.append(_relation_finding(
                r.get("kind"),
                r.get("col_a"),
                r.get("col_b"),
                int(r.get("n") or 0),
                1.0,
                r.get("rule"),
                r.get("col_a_sample"),
                r.get("col_b_sample"),
                sheet=block.get("sheet"),
                file=block.get("file"),
                figure_label=block.get("figure_label"),
                headers=(r.get("evidence") or {}).get("headers"),
                slope=r.get("slope"),
                intercept=r.get("intercept"),
            ))
    return findings


def _distill_within_col(scan: dict[str, Any], drop_budget: int) -> list[dict[str, Any]]:
    findings = []
    wc_kinds = {"within_col_value_duplication", "within_col_decimal_repetition"}
    for block in scan.get("relations_blocks", []) or []:
        for r in block.get("within_col", []) or []:
            if r.get("kind") not in wc_kinds:
                continue
            severity = str(r.get("severity")).lower()
            prefilter = r.get("prefilter")
            decision = "keep" if severity == "high" else (
                "downweight" if prefilter == "downweight" else "drop"
            )
            if decision == "drop":
                if drop_budget <= 0:
                    continue
                drop_budget -= 1
            n = int(r.get("n") or 0)
            all_int = bool(r.get("all_integer"))
            high_precision = not all_int
            try:
                frac = float(r["frac_repeat"]) if r.get("frac_repeat") is not None else None
            except (TypeError, ValueError):
                frac = None
            findings.append({
                "kind": r.get("kind"),
                "col_a": r.get("col"),
                "col_b": None,
                "n": n,
                "rule": r.get("rule"),
                "top5_a": (r.get("value_sample") or [])[:5],
                "top5_b": [],
                "high_precision": high_precision,
                "mass": bool(n >= 200 or (n >= 5 and high_precision)),
                "evidence_confidence": evidence_confidence(n, frac, high_precision),
                "prefilter": decision,
                "prefilter_reason": r.get("prefilter_reason"),
                "within_col": True,
                "dup_value": r.get("dup_value"),
                "ending": r.get("ending"),
                "frac_repeat": r.get("frac_repeat"),
                "n_distinct": r.get("n_distinct"),
                "all_integer": all_int,
                "sheet": block.get("sheet"),
                "file": block.get("file"),
                "figure_label": block.get("figure_label"),
            })
    return findings


def _distill_block_findings(scan: dict[str, Any]) -> list[dict[str, Any]]:
    """Distill the single-column statistical HIGH findings the specialized distillers
    skip (arithmetic_progression, row_pair_digit_coupling, identical_after_rounding, grim).

    Without this, a paper whose only HIGH signal lives in these groups distilled to an
    EMPTY packet and was gated out of deep review as `no_surviving_high`. Only HIGH
    (post-profile-demotion) findings are kept; axis progressions are additionally skipped
    via `_is_axis_finding` as a belt-and-suspenders (they are already demoted to low in the
    `review`/`triage` profiles, but `forensic` skips demotion).
    """
    findings = []
    for block in scan.get("relations_blocks", []) or []:
        for group in _SIMPLE_BLOCK_GROUPS:
            for r in block.get(group, []) or []:
                if str(r.get("severity")).lower() != "high":
                    continue
                if _is_axis_finding(r):
                    continue
                n = int(r.get("n") or 0)
                # grim/grimmer identify their column as mean_col; the row-oriented
                # detectors (constant_ratio_row/identical_row) locate by row_a/row_b and
                # sample as row_a_sample — carry those so the distilled entry keeps a
                # usable location and value peek instead of dropping them.
                sample = (r.get("col_a_sample") or r.get("value_sample")
                          or r.get("row_a_sample") or [])
                col_a = r.get("col") or r.get("col_a") or r.get("mean_col") or r.get("row_a")
                findings.append({
                    "kind": r.get("kind"),
                    "col_a": col_a,
                    "col_b": r.get("col_b") or r.get("sd_col") or r.get("row_b"),
                    "n": n,
                    "rule": r.get("rule"),
                    "top5_a": list(sample)[:5],
                    "top5_b": [],
                    "high_precision": True,
                    "mass": bool(n >= 200),
                    "evidence_confidence": evidence_confidence(n, 1.0, True),
                    "prefilter": "keep",
                    "prefilter_reason": None,
                    "sheet": block.get("sheet"),
                    "file": block.get("file"),
                    "figure_label": block.get("figure_label"),
                })
    return findings


def distill_findings_for_review(scan: dict[str, Any], *,
                                within_col_drop_budget: int = 100) -> list[dict[str, Any]]:
    """Return compact, prefiltered review findings from a full PaperConan scan.

    Iterates every per-block finding group (see `paperconan.BLOCK_FINDING_GROUPS`):
    relations/equal_pairs and within_col via their specialized distillers, the remaining
    single-column statistical groups via `_distill_block_findings`, plus cross-sheet.

    Findings with `prefilter == "drop"` are retained in the returned list so
    callers can compute auto-drop/no-finding states and audit why a candidate
    was filtered.
    """
    findings = []
    findings.extend(_distill_cross_sheet(scan))
    findings.extend(_distill_relations(scan))
    findings.extend(_distill_within_col(scan, within_col_drop_budget))
    findings.extend(_distill_block_findings(scan))
    return findings


distill_and_filter = distill_findings_for_review
