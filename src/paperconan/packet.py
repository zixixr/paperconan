"""Review-packet distillation helpers.

This module turns the full `scan_dir()` output into compact findings suitable
for downstream review systems. It is infrastructure-agnostic: no Blob, DB, DOI
claiming, or cloud-worker assumptions live here.
"""
from __future__ import annotations

from typing import Any

from ._prefilter import evidence_confidence
from .detectors import prefilter_relation_finding


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


def distill_findings_for_review(scan: dict[str, Any], *,
                                within_col_drop_budget: int = 100) -> list[dict[str, Any]]:
    """Return compact, prefiltered review findings from a full PaperConan scan.

    Findings with `prefilter == "drop"` are retained in the returned list so
    callers can compute auto-drop/no-finding states and audit why a candidate
    was filtered.
    """
    findings = []
    findings.extend(_distill_cross_sheet(scan))
    findings.extend(_distill_relations(scan))
    findings.extend(_distill_within_col(scan, within_col_drop_budget))
    return findings


distill_and_filter = distill_findings_for_review
